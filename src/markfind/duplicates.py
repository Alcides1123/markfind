"""Find bookmarks saved more than once.

Two bookmarks are duplicates if their URLs are the same once cosmetic
differences are removed (``canonical_url``), or if their fetched pages have
the same text.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit

# Query parameters that only track where a click came from.
TRACKING_PARAMS = frozenset(
    {
        "fbclid",
        "gclid",
        "dclid",
        "gbraid",
        "wbraid",
        "msclkid",
        "yclid",
        "twclid",
        "igshid",
        "mc_cid",
        "mc_eid",
        "_hsenc",
        "_hsmi",
        "mkt_tok",
        "oly_anon_id",
        "oly_enc_id",
        "vero_id",
        "ref_src",
        "ref_url",
    }
)
TRACKING_PREFIXES = ("utm_",)
_DEFAULT_PORTS = {"http": 80, "https": 443}
_INDEX_PAGES = ("index.html", "index.htm", "index.php", "default.aspx")

MIN_WORDS = 50  # shorter pages are too likely to be error or login pages


@dataclass(frozen=True)
class Entry:
    url: str
    title: str
    folder: str


@dataclass(frozen=True)
class Group:
    """Bookmarks that are the same page. The preferred one comes first."""

    reason: str  # "url" or "text"
    entries: list[Entry]

    @property
    def title(self) -> str:
        return next((e.title for e in self.entries if e.title), self.entries[0].url)


def canonical_url(url: str) -> str:
    """The URL with differences that don't change the page removed.

    Not a URL to visit: the scheme is dropped, so ``http`` and ``https``
    versions compare equal.
    """
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    try:
        port = parts.port
    except ValueError:  # e.g. ":abc"
        port = None
    if port is not None and port != _DEFAULT_PORTS.get(parts.scheme.lower()):
        host += f":{port}"

    path = parts.path
    for index in _INDEX_PAGES:
        if path.lower().endswith("/" + index):
            path = path[: -len(index)]
            break
    path = path.rstrip("/")

    params = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in TRACKING_PARAMS and not key.lower().startswith(TRACKING_PREFIXES)
    ]
    query = urlencode(sorted(params))

    # Single-page apps route with "#/page" or "#!page"; other fragments only
    # scroll to a section.
    fragment = parts.fragment if parts.fragment.startswith(("/", "!")) else ""

    key = host + path
    if query:
        key += "?" + query
    if fragment:
        key += "#" + fragment
    return key


def text_key(text: str, min_words: int = MIN_WORDS) -> str | None:
    """A fingerprint of page text, or None if it's too short to compare."""
    words = text.lower().split()
    if len(words) < min_words:
        return None
    return hashlib.sha256(" ".join(words).encode("utf-8")).hexdigest()


def _preference(entry: Entry) -> tuple[bool, int, str]:
    # https first, then the shortest URL (fewest leftovers), then alphabetical.
    return (not entry.url.lower().startswith("https:"), len(entry.url), entry.url)


def find_duplicates(
    pages: Iterable[tuple[str, str, str, str]],
    by: tuple[str, ...] = ("url", "text"),
    min_words: int = MIN_WORDS,
) -> list[Group]:
    """Groups of duplicate bookmarks from (url, title, folder, page text) rows.

    Same-URL groups come first. A same-text group is left out when its
    bookmarks are already one same-URL group.
    """
    by_url: dict[str, list[Entry]] = {}
    by_text: dict[str, list[Entry]] = {}
    canonical: dict[str, str] = {}
    for url, title, folder, text in pages:
        entry = Entry(url, title, folder)
        canonical[url] = canonical_url(url)
        by_url.setdefault(canonical[url], []).append(entry)
        key = text_key(text or "", min_words)
        if key:
            by_text.setdefault(key, []).append(entry)

    groups = []
    if "url" in by:
        for entries in by_url.values():
            if len(entries) > 1:
                groups.append(Group("url", sorted(entries, key=_preference)))
    if "text" in by:
        for entries in by_text.values():
            already_grouped = "url" in by and len({canonical[e.url] for e in entries}) == 1
            if len(entries) > 1 and not already_grouped:
                groups.append(Group("text", sorted(entries, key=_preference)))
    return sorted(groups, key=lambda g: (g.reason != "url", g.title.lower(), g.entries[0].url))
