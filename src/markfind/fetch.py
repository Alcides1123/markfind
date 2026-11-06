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
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import escape
from itertools import chain, count, zip_longest
from urllib.parse import urlsplit

from . import __version__
from .extract import Page, extract_text, normalize
from .pdf import MAX_PDF_BYTES, PdfEmptyError, PdfUnsupportedError, extract_pdf

USER_AGENT = f"markfind/{__version__} (+personal bookmark indexer)"
MAX_BYTES = 2_000_000
_HTML_TYPES = ("text/html", "application/xhtml+xml")
_PDF_TYPES = ("application/pdf", "application/x-pdf")
# How an HTML document starts, after any BOM and whitespace.
_HTML_START = re.compile(rb"<(?:!doctype\s+html|html|head|body)(?:[\s>/]|$)", re.IGNORECASE)


class FetchError(Exception):
    pass


class UnsupportedContentError(FetchError):
    pass


class EmptyPageError(FetchError):
    """The page downloaded fine but has no text, e.g. an app built by JavaScript."""


EMPTY_MESSAGE = "no text found; the page may need JavaScript or a login"

# Retrying HTTP 429 / 503 responses.
RETRIES = 2
MAX_RETRY_WAIT = 60.0  # longer waits are not worth holding up a fetch for
DEFAULT_RETRY_WAIT = 5.0  # when the server doesn't say
# Headers that mark a bot-protection challenge page: an automated client
# never gets past one, so it isn't retried.
_CHALLENGE_HEADERS = ("cf-mitigated", "x-vercel-mitigated")

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
    "empty",
    "other",
)


def classify_error(exc: BaseException) -> tuple[str, str]:
    """Return (kind, readable message) for a fetch failure; kinds are ERROR_KINDS."""
    if isinstance(exc, urllib.error.HTTPError):
        message = f"HTTP {exc.code} {exc.reason}".strip()
        if exc.code in (404, 410):
            return "gone", message
        if is_bot_challenge(exc):
            return "blocked", f"{message}: bot-protection challenge"
        if exc.code in (401, 403):
            return "blocked", message
        if exc.code == 429 or exc.code >= 500:
            asked = _retry_after_header(exc)
            if asked is not None and asked > 0:
                message += f"; the site asks to wait {asked:.0f} s"
            return "server", message
        return "http", message

    # urlopen wraps most low-level errors in URLError(reason=<original error>).
    cause: object = exc
    if isinstance(exc, urllib.error.URLError):
        cause = exc.reason

    if isinstance(cause, UnsupportedContentError):
        return "unsupported", str(cause)
    if isinstance(cause, EmptyPageError):
        return "empty", str(cause)
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


def is_bot_challenge(exc: urllib.error.HTTPError) -> bool:
    """Whether an error response is a bot-protection challenge (Cloudflare, Vercel)."""
    headers = exc.headers
    if headers is None:
        return False
    return any(
        (headers.get(name) or "").strip().lower() == "challenge" for name in _CHALLENGE_HEADERS
    )


def _retry_after_header(exc: urllib.error.HTTPError) -> float | None:
    """Seconds from the Retry-After header (a number or an HTTP date), or None."""
    value = (exc.headers.get("Retry-After") or "").strip() if exc.headers is not None else ""
    if not value:
        return None
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


def retry_wait(exc: BaseException) -> float | None:
    """Seconds to wait before retrying this failure, or None if retrying won't help.

    Only HTTP 429 (too many requests) and 503 (unavailable) are retried, and
    never a bot-protection challenge.
    """
    if not isinstance(exc, urllib.error.HTTPError) or exc.code not in (429, 503):
        return None
    if is_bot_challenge(exc):
        return None
    asked = _retry_after_header(exc)
    return DEFAULT_RETRY_WAIT if asked is None else asked


def sniff_content_type(declared: str | None, body: bytes) -> str | None:
    """The type to treat a response as, given its declared type (None if absent).

    HTML sent without a Content-Type, or mislabelled text/plain, is HTML.
    Without a Content-Type, a PDF is a PDF, other binary data gives None, and
    anything else is text.
    """
    if declared in _PDF_TYPES:
        return "application/pdf"
    if declared not in (None, "text/plain"):
        return declared
    start = body[:1024].lstrip(b"\xef\xbb\xbf \t\r\n")
    if _HTML_START.match(start):
        return "text/html"
    if declared is None and start.startswith(b"%PDF"):
        return "application/pdf"
    if declared is None and b"\x00" in body[:1024]:
        return None
    return "text/plain"


def fetch_page(url: str, timeout: float = 10.0, max_bytes: int = MAX_BYTES) -> Page:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": (
                "text/html,application/xhtml+xml,text/plain;q=0.9,application/pdf;q=0.8,*/*;q=0.1"
            ),
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        # get_content_type() reports a missing header as text/plain, so check
        # for the header itself.
        declared = (
            response.headers.get_content_type() if "Content-Type" in response.headers else None
        )
        if declared not in (None, *_HTML_TYPES, *_PDF_TYPES, "text/plain"):
            raise UnsupportedContentError(f"unsupported content type: {declared}")
        # Very large pages are truncated rather than skipped.
        raw = response.read(max_bytes)
        charset = response.headers.get_content_charset() or "utf-8"
        final_url = response.geturl() or url  # after redirects, for links in the copy
        content_type = sniff_content_type(declared, raw)
        if content_type == "application/pdf" and len(raw) == max_bytes:
            # A truncated PDF can't be read at all, so PDFs get a higher limit.
            raw += response.read(MAX_PDF_BYTES + 1 - len(raw))
            if len(raw) > MAX_PDF_BYTES:
                raise UnsupportedContentError(
                    f"PDF larger than {MAX_PDF_BYTES // 1_000_000} MB, not indexed"
                )

    if content_type is None:
        raise UnsupportedContentError("unsupported content: binary data with no content type")
    if content_type == "application/pdf":
        try:
            return extract_pdf(raw)
        except PdfUnsupportedError as exc:
            raise UnsupportedContentError(str(exc)) from exc
        except PdfEmptyError as exc:
            raise EmptyPageError(str(exc)) from exc

    try:
        body = raw.decode(charset, errors="replace")
    except LookupError:
        body = raw.decode("utf-8", errors="replace")
    body = body.removeprefix("\ufeff")  # a UTF-8 byte-order mark isn't text

    if content_type == "text/plain":
        text = normalize(body)
        page = Page(title="", text=text, html=f"<pre>{escape(body.strip())}</pre>" if text else "")
    else:
        page = extract_text(body, base_url=final_url)
    if not page.text:
        raise EmptyPageError(EMPTY_MESSAGE)
    return page


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
        self._not_before: dict[str, float] = {}

    def defer(self, host: str, seconds: float) -> None:
        """Hold back every request to ``host`` for ``seconds`` from now."""
        with self._guard:
            until = self._clock() + seconds
            self._not_before[host] = max(self._not_before.get(host, until), until)

    def run(self, host: str, func: Callable[[], Page]) -> Page:
        with self._guard:
            lock = self._locks[host]
        with lock:
            start = self._not_before.get(host, 0.0)
            if host in self._last:
                start = max(start, self._last[host] + self.delay)
            wait = start - self._clock()
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
    max_retry_wait: float = MAX_RETRY_WAIT,
    retries: int = RETRIES,
) -> Iterator[tuple[int, str, Page | Exception]]:
    """Fetch (id, url) pairs concurrently, yielding results as they finish.

    Failures are yielded as exceptions instead of being raised, so one bad
    link does not stop the rest. A page answered with HTTP 429 or 503 is
    retried up to ``retries`` times, after the wait the server asks for, unless
    that's longer than ``max_retry_wait`` seconds; meanwhile the whole host
    waits.
    """
    limiter = limiter or HostLimiter()

    def task(url: str) -> Page:
        host = host_of(url)
        for attempt in count():
            try:
                return limiter.run(host, lambda: fetch(url, timeout=timeout))
            except urllib.error.HTTPError as exc:
                wait = retry_wait(exc)
                give_up = wait is None or attempt >= retries or max_retry_wait <= 0
                if give_up or wait > max_retry_wait:
                    raise
                limiter.defer(host, wait)
        raise AssertionError("unreachable")  # count() never ends

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(task, url): (id_, url) for id_, url in interleave_hosts(items)}
        for future in as_completed(futures):
            id_, url = futures[future]
            try:
                yield id_, url, future.result()
            except Exception as exc:  # noqa: BLE001 - reported per bookmark
                yield id_, url, exc
