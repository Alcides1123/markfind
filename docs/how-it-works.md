# How it works

```text
 browser export ──import──▶ bookmarks table ──fetch──▶ page text ──▶ FTS5 index ──search──▶ results
  (HTML / JSON)             (url, title, folder)        (extract)                    (BM25)
```

The code is small and has one module per stage, all in `src/markfind/`:

| Module | Responsibility |
|---|---|
| `importers.py` | Parse browser exports into `Bookmark` objects |
| `fetch.py` | Download pages concurrently and politely; classify failures |
| `extract.py` | Turn HTML into the page's main text |
| `store.py` | SQLite schema, migrations, the FTS5 index, search |
| `cli.py` | The `markfind` command |

Everything uses the Python standard library: `html.parser`, `urllib`,
`sqlite3`, `concurrent.futures` and `argparse`.

## Importing

**Netscape HTML.** The format is old and loosely structured: folders are `<H3>`
headings, each followed by a `<DL>` list, and closing `</DT>` and `</p>` tags
are usually missing. The parser does no tree-building. It keeps a stack of open
`<DL>` lists, and each list remembers the folder name from the `<H3>` just
before it. Each `<A HREF>` becomes a bookmark whose folder is the stack joined
with `/`. `ADD_DATE` (Unix seconds) becomes `added_at`.

**Chrome JSON.** The parser walks the `roots` tree recursively. Chrome stores
dates as microseconds since 1601-01-01 UTC, and those are converted. Non-folder
values in `roots`, such as sync metadata, are ignored.

**Firefox `places.sqlite`.** Detected by the file's SQLite header. Firefox
keeps the database open while it runs and writes recent changes to a
`places.sqlite-wal` file next to it. markfind copies both files to a temporary
directory and reads the copy, so it sees those changes and can't lock or
damage the profile. Bookmarks are rows of `moz_bookmarks` (type 1) joined to
`moz_places` for the URL. Folders (type 2) are followed up their `parent`
chain to build the path:

- Built-in folders are identified by their fixed `guid` and shown with
  Firefox's display names: *Bookmarks Menu*, *Bookmarks Toolbar*, *Other
  Bookmarks* and *Mobile Bookmarks*.
- The hidden *Tags* folder (`tags________`) and everything under it is
  skipped. Firefox stores each tag as a second copy of the bookmark there.
- A missing bookmark title falls back to the page title in `moz_places`.
- `dateAdded` is microseconds since 1970.

## Fetching

`fetch_many()` runs downloads on a thread pool (`--workers`). Two mechanisms
keep it polite to websites:

- **`HostLimiter`** gives each host its own lock, so only one request per host
  is in flight. It also enforces a minimum gap of `--delay` seconds between
  requests to that host. A failed request counts too, so a host that's down
  isn't retried immediately.
- **`interleave_hosts()`** reorders the work round-robin by host before it's
  queued. Without this, 200 bookmarks on one site would occupy every worker
  waiting on that site's limiter while other sites sat idle.

Results are written to the database from the main thread as they arrive, so a
fetch can be interrupted at any point without losing finished work.

Failures go through `classify_error()`, which maps the exception (HTTP status,
DNS failure, timeout, certificate error, and so on) to one of the
[failure kinds](cli.md#fetch) plus a short readable message.

## Text extraction

Indexing a whole page would fill the index with navigation menus, cookie
banners and footers, and every page would match words like "home" or
"privacy". `extract_text()` makes one pass with `HTMLParser` and:

1. **Never indexes** `script`, `style`, `noscript`, `template`, `svg`, `iframe`
   or `canvas`.
2. **Skips boilerplate elements and everything inside them**:
   - The elements `nav`, `footer`, `aside`, `form`, `button` and `dialog`.
   - `header`, but only outside the main content. Inside an `<article>`, the
     header usually holds the article's title, so it's kept.
   - Anything with an ARIA role of `navigation`, `banner`, `contentinfo`,
     `complementary`, `search`, `menu`, `menubar` or `dialog`.
   - Anything whose `class` or `id` contains a boilerplate word as a whole
     word, such as `nav`, `menu`, `sidebar`, `footer`, `cookie`, `consent`,
     `banner`, `ad`, `promo`, `share`, `social`, `related`, `newsletter`,
     `popup`, `modal` or `comments`. "Whole word" means `site-footer` and
     `cookie_bar` match but `additional` does not.
   - Hidden elements (`hidden`, `aria-hidden="true"`).

   The class, id and role rules never apply to `<html>`, `<body>`, `<main>`
   or `<article>`, because sites put page-wide flags on them. Wikipedia's
   `<html>` class includes `vector-feature-main-menu-pinned-disabled`, for
   example. State classes such as `has-sidebar` or `is-menu-open` are
   ignored too.
3. **Prefers the main content.** If the page has `<main>` or `<article>` with
   at least 30 words, only that text is kept. Otherwise the whole page, minus
   the boilerplate above, is used. The 30-word minimum stops a short teaser in
   `<main>` from hiding the rest of the page.
4. **Never empties a page by mistake.** If the rules above leave under 30
   words and remove more than 90% of the visible text, a wrapper element was
   probably misjudged. In that case all the visible text is indexed instead,
   still without scripts and styles.

Skipping is depth-counted per tag name, so nested `<div>`s inside a skipped
`<div class="sidebar">` are handled. Void elements such as `<input
class="nav-search">` and self-closing tags never start a skip.

## The index

Two tables live in one SQLite file:

```sql
CREATE TABLE bookmarks (
    id INTEGER PRIMARY KEY,
    url TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL DEFAULT '',
    folder TEXT NOT NULL DEFAULT '',
    added_at TEXT,        -- from the browser export
    fetched_at TEXT,      -- last fetch attempt, ISO 8601 UTC
    fetch_error TEXT,     -- NULL if the last fetch succeeded
    error_kind TEXT       -- failure kind, NULL if the last fetch succeeded
);

CREATE VIRTUAL TABLE bookmarks_fts USING fts5(
    title, url, body, folder,
    tokenize = 'porter unicode61 remove_diacritics 2'
);
```

- `bookmarks_fts.rowid` equals `bookmarks.id`. Whenever a bookmark's title,
  folder or page text changes, its index row is deleted and re-inserted.
- The page text (`body`) is stored only in the FTS table.
- The tokenizer folds case and accents (`unicode61 remove_diacritics 2`) and
  reduces English words to their stems (`porter`).

## Searching and ranking

A plain query like `full-text search` becomes the FTS5 query
`"full-text" "search"`. Quoting each word stops punctuation from being read as
query syntax, and listing them side by side means *all must match*. `--raw`
skips this step.

Results are ordered by FTS5's `bm25()` with per-column weights:

| Column | Weight | Why |
|---|---|---|
| `title` | 5 | A title match is the strongest signal of what a page is about |
| `body` | 2 | The page text is where most matches come from |
| `url` | 1 | Useful for domains and slugs, but noisy |
| `folder` | 1 | Helps to find "everything in Recipes", but folders are broad |

These weights are a reasonable starting point, not tuned values. Tuning them
on real queries is on the [roadmap](../ROADMAP.md).

The snippet is FTS5's `snippet()` over the `body` column: up to 16 tokens
around the matches.

## Schema versions and migrations

The database stores its schema version in `PRAGMA user_version`. When
`Store` opens a database, it:

1. Creates any missing tables. `SCHEMA` always describes the latest version.
2. Runs each migration in `MIGRATIONS` that's newer than the stored version,
   all in one transaction.
3. Refuses to open a database whose version is newer than the code knows, so
   an old markfind can't damage a newer database.

| Version | Change |
|---|---|
| 0 | markfind 0.1.0 |
| 1 | Adds `bookmarks.error_kind`. Existing failures get the kind `other`. |

See [Development](development.md#changing-the-database-schema) for how to add
a migration.
