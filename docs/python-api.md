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

**`Bookmark(url, title="", folder="", added_at=None)`** is a frozen dataclass.
`folder` is the folder path joined with `/`, and `added_at` is a UTC
`datetime` or `None`.

| Function | Returns |
|---|---|
| `load_file(path)` | `list[Bookmark]`. Detects Firefox SQLite, Chrome JSON or HTML from the content. Raises `ValueError` for a non-Firefox SQLite file. |
| `parse_netscape_html(text)` | `list[Bookmark]` from a Netscape HTML export |
| `parse_chrome_json(text)` | `list[Bookmark]` from Chrome / Edge `Bookmarks` JSON |
| `parse_firefox_places(path)` | `list[Bookmark]` from Firefox's `places.sqlite`, read from a temporary copy |

Only `http` and `https` URLs are returned.

You can create `Bookmark` objects yourself to index links from other sources,
such as a read-later service export or a list of URLs:

```python
from markfind.importers import Bookmark

store.add_bookmarks(Bookmark(url, folder="Reading list") for url in urls)
```

## `markfind.store`

**`Store(path)`** opens or creates a database and migrates it if needed. Use
it as a context manager, or call `close()`. A `Store` should be used from one
thread.

| Method | Description |
|---|---|
| `add_bookmarks(bookmarks) -> (added, updated)` | Insert new bookmarks. For URLs already present, update the title and folder and keep the page text. |
| `pending(refetch=False, older_than=None, failed=False) -> list[(id, url)]` | Bookmarks to fetch: never-fetched ones, plus everything (`refetch`), those fetched before `now - older_than` (a `timedelta`), or failed ones (`failed`). |
| `set_page(id, text, page_title="")` | Store fetched text and clear any error. `page_title` is used only if the bookmark has no title. |
| `set_error(id, message, kind="other")` | Record a failed fetch. |
| `search(query, limit=20, mark=("[", "]")) -> list[SearchResult]` | Run an FTS5 query. `mark` wraps matched words in the snippet. Raises `QueryError` for invalid syntax. |
| `failures(kinds=None) -> list[Failure]` | Bookmarks whose last fetch failed, optionally only the given kinds. |
| `list_bookmarks(folder=None) -> list[(url, title, folder)]` | All bookmarks, or one folder and its subfolders. |
| `stats() -> Stats` | Counts: `total`, `fetched`, `failed`, `failed_by_kind`. |

Result types (frozen dataclasses):

- **`SearchResult`**: `url`, `title`, `folder`, `snippet`
- **`Failure`**: `url`, `title`, `folder`, `kind`, `error`, `fetched_at`

**`to_fts_query(text)`** turns plain words into a safe FTS5 query in which
all words must match. Pass the user's text through it unless you want raw FTS5
syntax.

## `markfind.fetch`

| Name | Description |
|---|---|
| `fetch_page(url, timeout=10.0, max_bytes=2_000_000) -> Page` | Download one page and extract its text. Raises `urllib` errors, `TimeoutError`, or `UnsupportedContentError`. |
| `fetch_many(items, fetch=fetch_page, workers=8, timeout=10.0, limiter=None)` | Fetch `(id, url)` pairs concurrently. Yields `(id, url, Page \| Exception)` as each one finishes. Never raises for a single failed page. |
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

## `markfind.extract`

| Name | Description |
|---|---|
| `extract_text(html) -> Page` | Title and main text of an HTML document, with boilerplate removed (see [How it works](how-it-works.md#text-extraction)). |
| `Page(title, text)` | Frozen dataclass. |
