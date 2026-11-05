# Python API

The `markfind` command is a thin layer over a few Python functions and
classes. You can use them directly, for example to export results, build your
own interface, or index bookmarks from somewhere else.

> **Stability:** until version 1.0, these interfaces may change between minor
> versions. Changes will be listed in [CHANGES.md](../CHANGES.md).

## A complete example

```python
from markfind.fetch import classify_error, fetch_many
from markfind.importers import load_file
from markfind.extract import Page
from markfind.store import Store, to_fts_query

with Store("bookmarks.sqlite") as store:
    store.add_bookmarks(load_file("bookmarks.html"))

    for id_, url, result in fetch_many(store.pending()):
        if isinstance(result, Page):
            store.set_page(id_, result.text, result.title)
        else:
            kind, message = classify_error(result)
            store.set_error(id_, message, kind)

    for r in store.search(to_fts_query("sqlite index"), limit=5):
        print(r.title, r.url, r.snippet)
```

See also [`examples/offline_demo.py`](../examples/offline_demo.py) and
[`examples/export_markdown.py`](../examples/export_markdown.py).

## `markfind.importers`

**`Bookmark(url, title="", folder="", added_at=None, tags=None)`** is a frozen
dataclass. `folder` is the folder path joined with `/`, and `added_at` is a
UTC `datetime` or `None`. `tags` is a tuple of strings, or `None` when the
source can't have tags (Chrome, Safari, an HTML export without `TAGS`):
importing a `None` keeps a bookmark's recorded tags, while `()` clears them.
`clean_tags(tags)` trims tags and drops commas, blanks and repeats.

| Function | Returns |
|---|---|
| `load_file(path)` | `list[Bookmark]`. Detects Firefox SQLite, a Safari property list, Chrome JSON or HTML from the content. Raises `ValueError` for a non-Firefox SQLite file or an unreadable property list. |
| `parse_netscape_html(text)` | `list[Bookmark]` from a Netscape HTML export |
| `parse_chrome_json(text)` | `list[Bookmark]` from Chrome / Edge `Bookmarks` JSON |
| `parse_firefox_places(path)` | `list[Bookmark]` from Firefox's `places.sqlite`, read from a temporary copy |
| `parse_safari_plist(data)` | `list[Bookmark]` from the bytes of Safari's `Bookmarks.plist` (binary or XML) |

Only `http` and `https` URLs are returned.

You can create `Bookmark` objects yourself to index links from other sources,
such as a read-later service export or a list of URLs:

```python
from markfind.importers import Bookmark

store.add_bookmarks(Bookmark(url, folder="Reading list") for url in urls)
```

## `markfind.browsers`

**`find_sources() -> list[Source]`** returns the bookmark file of every
browser profile found on this computer, Chromium browsers first. Each
**`Source`** has `browser` (`"Chrome"`, `"Firefox"`, ...), `profile` (the name
shown in the browser), `path`, and `name` (`"Chrome (Work)"`). Pass the path
to `load_file()`.

```python
from markfind.browsers import find_sources
from markfind.importers import load_file

for source in find_sources():
    print(source.name, len(load_file(source.path)))
```

`BROWSERS` lists the supported browser names.

## `markfind.store`

**`Store(path)`** opens or creates a database and migrates it if needed. Use
it as a context manager, or call `close()`. A `Store` should be used from one
thread.

| Method | Description |
|---|---|
| `add_bookmarks(bookmarks, source=None) -> (added, updated)` | Insert new bookmarks. For URLs already present, update the title and folder and keep the page text. `source` (any string, usually the file path) records where they came from; `None` keeps the recorded source. |
| `delete(urls) -> int` | Delete the bookmarks with these URLs and their indexed text. Unknown URLs are ignored. Returns how many were deleted. |
| `prune(source, keep) -> list[(url, title)]` | Delete bookmarks whose source is `source` and whose URL isn't in `keep`. Returns what was removed. Import every source before pruning any. |
| `pending(refetch=False, older_than=None, failed=False) -> list[(id, url)]` | Bookmarks to fetch: never-fetched ones, plus everything (`refetch`), those fetched before `now - older_than` (a `timedelta`), or failed ones (`failed`). |
| `set_page(id, text, page_title="")` | Store fetched text and clear any error. `page_title` is used only if the bookmark has no title. |
| `set_error(id, message, kind="other")` | Record a failed fetch. |
| `search(query, limit=20, mark=("[", "]"), exclude=None, folder=None, tag=None) -> list[SearchResult]` | Run an FTS5 query. `mark` wraps matched words in the snippet; `exclude` leaves out one bookmark id; `folder` keeps one folder and its subfolders; `tag` keeps bookmarks with that tag (ignoring case). Raises `QueryError` for invalid syntax. |
| `similar(url, limit=10, mark=("[", "]")) -> Similar` | Bookmarks most like this one (see [`markfind similar`](cli.md#similar)). Raises `KeyError` if the URL isn't in the database. Works on read-only connections. |
| `failures(kinds=None) -> list[Failure]` | Bookmarks whose last fetch failed, optionally only the given kinds. |
| `list_bookmarks(folder=None, tag=None) -> list[(url, title, folder)]` | All bookmarks, or one folder and its subfolders, or one tag. |
| `tags() -> list[(tag, count)]` | Every tag, most used first. Tags differing only in case count as one. |
| `folders() -> list[(path, count)]` | Every folder, parents included, in tree order. A count includes subfolders. |
| `records(folder=None, tag=None) -> list[Record]` | Like `list_bookmarks`, with dates and fetch status, for exporting. |
| `stats() -> Stats` | Counts: `total`, `fetched`, `failed`, `failed_by_kind`. |
| `pages() -> list[(url, title, folder, text)]` | Every bookmark with its indexed page text (`''` if not fetched). |
| `duplicates(by=REASONS, min_similarity=0.8) -> list[Group]` | Bookmarks saved more than once; see [`markfind.duplicates`](#markfindduplicates). |

Result types (frozen dataclasses):

- **`SearchResult`**: `url`, `title`, `folder`, `snippet`, `tags`
- **`Similar`**: `title` (of the bookmark you asked about), `keywords` (most
  telling first), `results` (`list[SearchResult]`)
- **`Failure`**: `url`, `title`, `folder`, `kind`, `error`, `fetched_at`
- **`Record`**: `url`, `title`, `folder`, `added_at`, `fetched_at`,
  `error_kind`, `tags`, and `status` (`indexed`, `pending` or the failure kind)

**`to_fts_query(text)`** turns plain words into a safe FTS5 query in which
all words must match. Pass the user's text through it unless you want raw FTS5
syntax.

## `markfind.duplicates`

| Function | Returns |
|---|---|
| `canonical_url(url)` | A comparison key: the URL without scheme, `www.`, default port, trailing slash, index page, `#section` or tracking parameters. Not a URL to visit. |
| `text_key(text, min_words=50)` | A fingerprint of the text ignoring case and spacing, or `None` if it's shorter than `min_words`. |
| `find_duplicates(pages, by=REASONS, min_words=50, min_similarity=0.8)` | `list[Group]` from `(url, title, folder, text)` rows: same-URL groups, then same-text, then nearly-same-text. `REASONS` is `("url", "text", "similar")`. |
| `shingles(text, size=5)` | The set of hashes of every `size`-word run in the text, ignoring case and spacing. Comparable only within one process. |
| `similarity(a, b)` | Jaccard similarity of two `shingles` sets, from 0 to 1. |
| `near_pairs(sets, min_similarity=0.8)` | `(i, j, similarity)` for every pair of sets at least that alike, without comparing every pair. |

A **`Group`** has `reason` (`"url"`, `"text"` or `"similar"`), `entries` (a
list of **`Entry(url, title, folder)`**, the cleanest URL first), `title`, and
`similarity`: for `"similar"` groups, the lowest similarity between two pages
that joined the group; `None` otherwise.

```python
from markfind.duplicates import canonical_url

canonical_url("http://www.example.com/a/?utm_source=x#top")  # 'example.com/a'
```

`TRACKING_PARAMS` and `TRACKING_PREFIXES` list the parameters that are removed.

## `markfind.fetch`

| Name | Description |
|---|---|
| `fetch_page(url, timeout=10.0, max_bytes=2_000_000) -> Page` | Download one page and extract its text. Raises `urllib` errors, `TimeoutError`, `UnsupportedContentError`, or `EmptyPageError` if the page has no text. |
| `fetch_many(items, fetch=fetch_page, workers=8, timeout=10.0, limiter=None, max_retry_wait=60.0, retries=2)` | Fetch `(id, url)` pairs concurrently. Yields `(id, url, Page \| Exception)` as each one finishes. Never raises for a single failed page. Retries HTTP 429 / 503 after the wait the server asks for. |
| `retry_wait(exc) -> float \| None` | Seconds to wait before retrying a failure (from `Retry-After`, default 5), or `None` if retrying won't help. |
| `HostLimiter(delay=1.0)` | One request at a time per host, at least `delay` seconds apart. Pass it to `fetch_many` as `limiter=`. |
| `classify_error(exc) -> (kind, message)` | Map a fetch exception to a failure kind and a readable message. |
| `ERROR_KINDS`, `DEAD_KINDS` | All failure kinds, and the ones treated as dead (`gone`, `dns`). |

The `fetch` parameter makes it easy to swap the downloader, for example to
add authentication or use a cached copy:

```python
def fetch_from_cache(url, timeout):
    html = cache.get(url)  # your own cache
    if html is None:
        return fetch_page(url, timeout=timeout)
    return extract_text(html)


results = fetch_many(store.pending(), fetch=fetch_from_cache)
```

## `markfind.formats`

Each function takes a list of `Record`s and returns the whole file as a string.

| Function | Returns |
|---|---|
| `to_html(records)` | A Netscape bookmark file that browsers import. `parse_netscape_html` reads it back unchanged. |
| `to_markdown(records, ranked=False)` | Sections per folder, or a numbered list in the given order if `ranked`. |
| `to_json(records)` / `to_csv(records)` | The `FIELDS`: `url`, `title`, `folder`, `added_at`, `fetched_at`, `status`, `tags` (a list in JSON, comma-separated in CSV). |

`FORMATS` maps each format name to its function.

```python
from pathlib import Path

from markfind.formats import to_html
from markfind.store import Store

with Store("bookmarks.sqlite") as store:
    keep = [r for r in store.records() if r.status in ("indexed", "pending")]
Path("working-links.html").write_text(to_html(keep), encoding="utf-8")
```

## `markfind.extract`

| Name | Description |
|---|---|
| `extract_text(html) -> Page` | Title and main text of an HTML document, with boilerplate removed (see [How it works](how-it-works.md#text-extraction)). |
| `Page(title, text)` | Frozen dataclass. |
