"""Find bookmarks saved more than once.

Two bookmarks are duplicates if their URLs are the same once cosmetic
differences are removed (``canonical_url``), if their fetched pages have the
same text, or if the texts are nearly the same (``similarity``): mostly the
same sentences, as with a page saved again after a small edit.
"""

from __future__ import annotations

import hashlib
from array import array
from collections.abc import Collection, Iterable, Sequence
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

# Near duplicates: the share of 5-word phrases two texts have in common.
SHINGLE_WORDS = 5
MIN_SIMILARITY = 0.8
# A phrase on more pages than this is boilerplate ("all rights reserved"); it
# says little about which pages match and makes finding candidates quadratic.
_COMMON_PHRASE = 20
# Candidates are found from the phrases whose hash is a multiple of this:
# the same phrases on every page, so near duplicates still share many.
_SAMPLE = 8

REASONS = ("url", "text", "similar")


@dataclass(frozen=True)
class Entry:
    url: str
    title: str
    folder: str


@dataclass(frozen=True)
class Group:
    """Bookmarks that are the same page. The preferred one comes first."""

    reason: str  # "url", "text" or "similar"
    entries: list[Entry]
    # For "similar": the lowest similarity between two pages that put
    # the group together, from 0 to 1. None for the other reasons.
    similarity: float | None = None

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


def _words(text: str) -> list[str]:
    return text.lower().split()


def shingles(text: str, size: int = SHINGLE_WORDS) -> frozenset[int]:
    """Hashes of every run of ``size`` consecutive words, ignoring case and spacing.

    Hashes are only comparable within one Python process.
    """
    words = _words(text)
    # Shorter tails end the zip early: the last phrase ends at the last word.
    return frozenset(map(hash, zip(*(words[i:] for i in range(size)), strict=False)))


def packed_shingles(text: str, size: int = SHINGLE_WORDS) -> array[int]:
    """The same hashes as ``shingles``, as a sorted array of machine integers.

    A few hundred hashes cost about 50 KB a page as a frozenset, because each
    one is a separate Python object; in an array they cost 8 bytes each. Over
    a few thousand pages that is the difference between comparing them all and
    running out of memory.
    """
    return array("q", sorted(shingles(text, size)))


def _as_set(phrases: Collection[int]) -> Collection[int]:
    return phrases if isinstance(phrases, (set, frozenset)) else set(phrases)


def similarity(a: Collection[int], b: Collection[int]) -> float:
    """Jaccard similarity of two shingle sets: shared phrases / all phrases.

    Takes either form: ``shingles`` sets or ``packed_shingles`` arrays.
    """
    if not a or not b:
        return 0.0
    shared = len(_as_set(a) & _as_set(b))
    return shared / (len(a) + len(b) - shared)


def near_pairs(
    sets: Sequence[Collection[int]], min_similarity: float = MIN_SIMILARITY
) -> list[tuple[int, int, float]]:
    """Pairs ``(i, j, similarity)`` of sets at least ``min_similarity`` alike, i < j.

    Only pairs sharing an uncommon phrase from a fixed sample are compared, so
    the work grows with the number of real matches rather than with every pair
    of pages. Each candidate's similarity is then computed exactly.

    Takes either form: ``shingles`` sets or ``packed_shingles`` arrays.
    """
    pages_with: dict[int, list[int]] = {}
    for i, phrases in enumerate(sets):
        # Short pages have few sampled phrases; use all of theirs.
        sample = (
            [phrase for phrase in phrases if not phrase % _SAMPLE]
            if len(phrases) >= 50 * _SAMPLE
            else phrases
        )
        for phrase in sample:
            pages_with.setdefault(phrase, []).append(i)

    candidates: set[tuple[int, int]] = set()
    for pages in pages_with.values():
        if 1 < len(pages) <= _COMMON_PHRASE:
            for n, i in enumerate(pages):
                candidates.update((i, j) for j in pages[n + 1 :])

    pairs = []
    for i, j in sorted(candidates):
        small, large = sorted((len(sets[i]), len(sets[j])))
        if small < min_similarity * large:
            continue  # Jaccard can't exceed small / large
        score = similarity(sets[i], sets[j])
        if score >= min_similarity:
            pairs.append((i, j, score))
    return pairs


def _components(n: int, pairs: list[tuple[int, int, float]]) -> list[tuple[list[int], float]]:
    """Connected groups of indexes, each with the lowest pair score in it."""
    parent = list(range(n))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, j, _ in pairs:
        parent[root(i)] = root(j)
    members: dict[int, list[int]] = {}
    lowest: dict[int, float] = {}
    for i, _, score in pairs:
        r = root(i)
        lowest[r] = min(lowest.get(r, 1.0), score)
    for i in range(n):
        members.setdefault(root(i), []).append(i)
    return [(m, lowest[r]) for r, m in members.items() if len(m) > 1]


def _preference(entry: Entry) -> tuple[bool, int, str]:
    # https first, then the shortest URL (fewest leftovers), then alphabetical.
    return (not entry.url.lower().startswith("https:"), len(entry.url), entry.url)


def find_duplicates(
    pages: Iterable[tuple[str, str, str, str]],
    by: tuple[str, ...] = REASONS,
    min_words: int = MIN_WORDS,
    min_similarity: float = MIN_SIMILARITY,
) -> list[Group]:
    """Groups of duplicate bookmarks from (url, title, folder, page text) rows.

    Same-URL groups come first, then same-text, then nearly-same-text groups.
    A group is left out when its bookmarks are already one group of an
    earlier kind.
    """
    by_url: dict[str, list[Entry]] = {}
    by_text: dict[str, list[Entry]] = {}
    canonical: dict[str, str] = {}
    texts: dict[str, str] = {}  # url -> text key, for pages long enough
    near: list[tuple[Entry, array[int]]] = []
    for url, title, folder, text in pages:
        entry = Entry(url, title, folder)
        canonical[url] = canonical_url(url)
        by_url.setdefault(canonical[url], []).append(entry)
        key = text_key(text or "", min_words)
        if key:
            texts[url] = key
            by_text.setdefault(key, []).append(entry)
            if "similar" in by:
                near.append((entry, packed_shingles(text)))

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
    if "similar" in by:
        # Identical texts are one page here, so each near match shows once.
        first: dict[str, int] = {}
        unique = []
        for entry, phrases in near:
            if texts[entry.url] not in first:
                first[texts[entry.url]] = len(unique)
                unique.append((entry, phrases))
        pairs = near_pairs([phrases for _, phrases in unique], min_similarity)
        for members, score in _components(len(unique), pairs):
            keys = {texts[unique[i][0].url] for i in members}
            entries = [e for e, _ in near if texts[e.url] in keys]
            urls = {canonical[e.url] for e in entries}
            if "url" in by and len(urls) == 1:
                continue  # one page saved under several URLs, already shown
            groups.append(Group("similar", sorted(entries, key=_preference), score))
    order = {reason: n for n, reason in enumerate(REASONS)}
    return sorted(groups, key=lambda g: (order[g.reason], g.title.lower(), g.entries[0].url))
