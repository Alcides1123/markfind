"""Download bookmarked pages so their text can be indexed."""

from __future__ import annotations

import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import chain, zip_longest
from urllib.parse import urlsplit

from . import __version__
from .extract import Page, extract_text, normalize

USER_AGENT = f"markfind/{__version__} (+personal bookmark indexer)"
MAX_BYTES = 2_000_000
_HTML_TYPES = ("text/html", "application/xhtml+xml")
# How an HTML document starts, after any BOM and whitespace.
_HTML_START = re.compile(rb"<(?:!doctype\s+html|html|head|body)(?:[\s>/]|$)", re.IGNORECASE)


class FetchError(Exception):
    pass


class UnsupportedContentError(FetchError):
    pass


# Error kinds that almost certainly mean the bookmark is dead for good.
DEAD_KINDS = ("gone", "dns")

ERROR_KINDS = (
    "gone",
    "dns",
    "blocked",
    "server",
    "http",
    "timeout",
    "connection",
    "ssl",
    "unsupported",
    "other",
)


def classify_error(exc: BaseException) -> tuple[str, str]:
    """Return (kind, readable message) for a fetch failure; kinds are ERROR_KINDS."""
    if isinstance(exc, urllib.error.HTTPError):
        message = f"HTTP {exc.code} {exc.reason}".strip()
        if exc.code in (404, 410):
            return "gone", message
        if exc.code in (401, 403):
            return "blocked", message
        if exc.code == 429 or exc.code >= 500:
            return "server", message
        return "http", message

    # urlopen wraps most low-level errors in URLError(reason=<original error>).
    cause: object = exc
    if isinstance(exc, urllib.error.URLError):
        cause = exc.reason

    if isinstance(cause, UnsupportedContentError):
        return "unsupported", str(cause)
    if isinstance(cause, socket.gaierror):
        return "dns", "host not found"
    if isinstance(cause, ssl.SSLCertVerificationError):
        detail = getattr(cause, "verify_message", None) or cause
        return "ssl", f"certificate verification failed: {detail}"
    if isinstance(cause, ssl.SSLError):
        return "ssl", f"SSL error: {getattr(cause, 'reason', None) or cause}"
    if isinstance(cause, (TimeoutError, socket.timeout)):
        return "timeout", "timed out"
    if isinstance(cause, ConnectionError):
        return "connection", f"connection failed: {type(cause).__name__}"
    if isinstance(cause, str) and "timed out" in cause:
        return "timeout", "timed out"
    return "other", f"{type(exc).__name__}: {exc}"


def sniff_content_type(declared: str | None, body: bytes) -> str | None:
    """The type to treat a response as, given its declared type (None if absent).

    HTML sent without a Content-Type, or mislabelled text/plain, is HTML.
    Without a Content-Type, binary data gives None; anything else is text.
    """
    if declared not in (None, "text/plain"):
        return declared
    start = body[:1024].lstrip(b"\xef\xbb\xbf \t\r\n")
    if _HTML_START.match(start):
        return "text/html"
    if declared is None and (b"\x00" in body[:1024] or start.startswith(b"%PDF")):
        return None
    return "text/plain"


def fetch_page(url: str, timeout: float = 10.0, max_bytes: int = MAX_BYTES) -> Page:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.1",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        # get_content_type() reports a missing header as text/plain, so check
        # for the header itself.
        declared = (
            response.headers.get_content_type() if "Content-Type" in response.headers else None
        )
        if declared not in (None, *_HTML_TYPES, "text/plain"):
            raise UnsupportedContentError(f"unsupported content type: {declared}")
        # Very large pages are truncated rather than skipped.
        raw = response.read(max_bytes)
        charset = response.headers.get_content_charset() or "utf-8"

    content_type = sniff_content_type(declared, raw)
    if content_type is None:
        raise UnsupportedContentError("unsupported content: binary data with no content type")

    try:
        body = raw.decode(charset, errors="replace")
    except LookupError:
        body = raw.decode("utf-8", errors="replace")
    body = body.removeprefix("﻿")  # a UTF-8 byte-order mark isn't text

    if content_type == "text/plain":
        return Page(title="", text=normalize(body))
    return extract_text(body)


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


class HostLimiter:
    """Allow one request at a time per host, at least ``delay`` seconds apart."""

    def __init__(
        self,
        delay: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.delay = delay
        self._clock = clock
        self._sleep = sleep
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
        self._last: dict[str, float] = {}

    def run(self, host: str, func: Callable[[], Page]) -> Page:
        with self._guard:
            lock = self._locks[host]
        with lock:
            if host in self._last:
                wait = self._last[host] + self.delay - self._clock()
                if wait > 0:
                    self._sleep(wait)
            try:
                return func()
            finally:
                self._last[host] = self._clock()


def interleave_hosts(items: Iterable[tuple[int, str]]) -> list[tuple[int, str]]:
    """Reorder (id, url) pairs round-robin by host.

    With many bookmarks on one site, this keeps the workers busy with other
    sites instead of all queueing behind that site's rate limit.
    """
    by_host: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for item in items:
        by_host[host_of(item[1])].append(item)
    rounds = zip_longest(*by_host.values())
    return [item for item in chain.from_iterable(rounds) if item is not None]


def fetch_many(
    items: Iterable[tuple[int, str]],
    fetch: Callable[..., Page] = fetch_page,
    workers: int = 8,
    timeout: float = 10.0,
    limiter: HostLimiter | None = None,
) -> Iterator[tuple[int, str, Page | Exception]]:
    """Fetch (id, url) pairs concurrently, yielding results as they finish.

    Failures are yielded as exceptions instead of being raised, so one bad
    link does not stop the rest.
    """
    limiter = limiter or HostLimiter()

    def task(url: str) -> Page:
        return limiter.run(host_of(url), lambda: fetch(url, timeout=timeout))

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(task, url): (id_, url) for id_, url in interleave_hosts(items)}
        for future in as_completed(futures):
            id_, url = futures[future]
            try:
                yield id_, url, future.result()
            except Exception as exc:  # noqa: BLE001 - reported per bookmark
                yield id_, url, exc
