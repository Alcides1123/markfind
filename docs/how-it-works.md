# How it works

```text
 browser export ──import──▶ bookmarks table ──fetch──▶ page text ──▶ FTS5 index ──search──▶ results
  (HTML / JSON)             (url, title, folder)        (extract)                    (BM25)
```

The code is small and has one module per stage, all in `src/markfind/`:

| Module | Responsibility |
|---|---|
| `importers.py` | Parse browser exports into `Bookmark` objects |
| `browsers.py` | Find the bookmark files of installed browsers, for `sync` |
| `fetch.py` | Download pages concurrently and politely; classify failures |
| `formats.py` | Write bookmarks as HTML, Markdown, JSON or CSV for `export` |
| `extract.py` | Turn HTML into the page's main text |
| `store.py` | SQLite schema, migrations, the FTS5 index, search |
| `duplicates.py` | URL normalization, duplicate grouping and near-duplicate text matching |
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
- Tags come from the hidden *Tags* folder (`tags________`). It holds one
  folder per tag, with a second copy of each tagged bookmark inside, so
  those copies become tags instead of bookmarks.
- A missing bookmark title falls back to the page title in `moz_places`.
- `dateAdded` is microseconds since 1970.

**Safari `Bookmarks.plist`.** Detected by the binary property list header
(`bplist00`) or an XML `<plist>`, and read with the standard library's
`plistlib`. The file is a tree of dictionaries:

- `WebBookmarkTypeList` entries are folders, with their contents in
  `Children`. The built-in ones get Safari's display names: `BookmarksBar` is
  *Favorites*, `BookmarksMenu` is *Bookmarks Menu*, and
  `com.apple.ReadingList` is *Reading List*. The root has no title.
- `WebBookmarkTypeLeaf` entries are bookmarks: the URL is `URLString` and the
  title is `URIDictionary.title`. Only Reading List items have a date
  (`ReadingList.DateAdded`).
- `WebBookmarkTypeProxy` entries, such as the *History* shortcut, aren't
  bookmarks and are skipped.

## Syncing and pruning

`markfind sync` is `import` with automatic file discovery. `find_sources()`
looks in each browser's standard data folder for the current platform:

- **Chromium browsers** keep one folder per profile (`Default`, `Profile 1`,
  ...) under a "User Data" folder, each with a `Bookmarks` file. The profile
  names the user sees come from `profile.info_cache` in the `Local State` JSON
  file next to them.
- **Firefox** keeps profiles in `Profiles/` (Windows, macOS) or directly in
  `~/.mozilla/firefox` (Linux). Any folder with a `places.sqlite` counts, and
  `profiles.ini` supplies the names.
- **Safari** (macOS only) has a single `~/Library/Safari/Bookmarks.plist`.
  Without Full Disk Access, macOS refuses even to say whether that file
  exists, so a permission error counts as "found": the import then fails with
  an explanation instead of Safari silently not appearing.

Bad or missing metadata falls back to the folder name, and the same file
reached twice (through a symlink, say) is only read once.

Every import records each bookmark's `source`: the absolute path of the file
it came from. Pruning a file deletes bookmarks whose `source` is that file and
whose URL isn't in it any more. A URL that's in two files belongs to whichever
was imported last, so `sync` imports every file first and only then prunes.
That way a bookmark that moved from Firefox to Chrome is claimed by Chrome
before Firefox is pruned.

## Fetching

`fetch_many()` runs downloads on a thread pool (`--workers`). Two mechanisms
keep it polite to websites:

- **`HostLimiter`** gives each host its own lock, so only one request per host
  is in flight. It also enforces a minimum gap of `--delay` seconds between
  requests to that host. A failed request counts too, so a host that's down
  isn't retried immediately.
- **Retries.** A 429 ("too many requests") or 503 ("unavailable") response is
  retried up to twice, after the wait in its `Retry-After` header (seconds or
  an HTTP date; 5 seconds if absent). `HostLimiter.defer()` holds back
  *every* request to that host meanwhile, since the server asked the client
  as a whole to slow down. A wait longer than `--max-retry-wait` isn't worth
  stalling a fetch for, so that page fails at once. Responses with
  `cf-mitigated: challenge` or `x-vercel-mitigated: challenge` are
  bot-protection challenge pages, not rate limits: they're never retried and
  count as `blocked`.
- **`interleave_hosts()`** reorders the work round-robin by host before it's
  queued. Without this, 200 bookmarks on one site would occupy every worker
  waiting on that site's limiter while other sites sat idle.

Results are written to the database from the main thread as they arrive, so a
fetch can be interrupted at any point without losing finished work.

Servers don't always say what they're sending. `sniff_content_type()`
checks the start of the body when the `Content-Type` header is missing (Python
would otherwise report `text/plain`) or says `text/plain`: a body that opens
with `<!DOCTYPE html`, `<html`, `<head` or `<body` is HTML. Without a header,
NUL bytes or a `%PDF` signature mean binary data, which isn't indexed.

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
   at least 30 words, only that text is kept. The 30-word minimum stops a
   short teaser in `<main>` from hiding the rest of the page.
4. **Otherwise finds the content block by its text.** Many pages have no
   `<main>`, and their menus aren't labelled either: just a `<div>` of links.
   Each `div`, `section` and `td` gets a score:
   - the words of *prose* directly inside it, meaning text outside links;
   - plus half the score of each block directly inside it, so an article
     written as a run of `<div>` paragraphs adds up in their parent;
   - scaled by the share of the block's words that aren't link text, so
     link lists score low.

   Starting from the best-scoring block, markfind moves out to its parent
   until the block holds at least 80% of the page's prose, and keeps only
   that block's text. Dropping real text hurts a search index more than an
   extra menu does, so this is cautious: it removes menus, link lists, tag
   clouds and small bits like a copyright line, but a page whose prose is
   spread over several blocks (two columns, an article plus a long aside) is
   kept whole. If the block would be the whole page or under 30 words,
   nothing changes.
5. **Never empties a page by mistake.** If the rules above leave under 30
   words and remove more than 90% of the visible text, a wrapper element was
   probably misjudged. In that case all the visible text is indexed instead,
   still without scripts and styles.

Skipping is depth-counted per tag name, so nested `<div>`s inside a skipped
`<div class="sidebar">` are handled. Void elements such as `<input
class="nav-search">` and self-closing tags never start a skip.
Missing end tags are common in old table layouts (`<td>a<td>b`), so a new
`<td>` closes an open one, and `</tr>` and `</table>` close any left open.

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
    error_kind TEXT,      -- failure kind, NULL if the last fetch succeeded
    source TEXT,          -- file it was last imported from, for pruning
    tags TEXT NOT NULL DEFAULT '',          -- browser_tags + my_tags
    browser_tags TEXT NOT NULL DEFAULT '',  -- from the last import
    my_tags TEXT NOT NULL DEFAULT '',       -- added with `markfind tag`
    note TEXT NOT NULL DEFAULT ''           -- set with `markfind note`
);

CREATE VIRTUAL TABLE bookmarks_fts USING fts5(
    title, url, body, folder, tags, note,
    tokenize = 'porter unicode61 remove_diacritics 2'
);
```

- `bookmarks_fts.rowid` equals `bookmarks.id`. Whenever a bookmark's title,
  folder, tags, note or page text changes, its index row is deleted and
  re-inserted.
- Tags are comma-separated. Imports write only `browser_tags`, and
  `markfind tag` only `my_tags`; after either, `tags` is recomputed as both
  together without repeats. Everything that reads tags (search, filters,
  export, the web UI) reads `tags`, so it can't tell the two apart.
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
| `tags` | 4 | Chosen to describe the page, by you or in Firefox |
| `note` | 3 | Your own words about the page, but longer and looser than tags |
| `body` | 2 | The page text is where most matches come from |
| `url` | 1 | Useful for domains and slugs, but noisy |
| `folder` | 1 | Helps to find "everything in Recipes", but folders are broad |

These weights are a reasonable starting point, not tuned values. Tuning them
on real queries is on the [roadmap](../ROADMAP.md).

The snippet is FTS5's `snippet()` over the `body` column: up to 16 tokens
around the matches.

## The web interface

`markfind serve` (`server.py`) is a `ThreadingHTTPServer` with three JSON
endpoints and three static files. The files (`web/index.html`, `app.js` and
`style.css`) ship as package data. There's no framework and no build step.

| Endpoint | Returns |
|---|---|
| `GET /api/similar?url=…&limit=N` | `{"url", "title", "keywords", "results": [...]}`, results as for search. `400` without `url`, `404` if the bookmark isn't in markfind. |
| `GET /api/search?q=…&raw=1&folder=PATH&tag=TAG&limit=N` | `{"query", "results": [{url, title, folder, snippet, tags, note}]}`. `folder` keeps one folder and its subfolders; `tag` keeps bookmarks with that tag, ignoring case. With `tag` and no `q`, lists the bookmarks with that tag in folder order, with empty snippets. `400` with `{"error"}` for an invalid raw query. |
| `GET /api/folders` | `{"folders": [{path, count}]}`: every folder, parents included, in tree order. A count includes subfolders. |
| `GET /api/tags` | `{"tags": [{tag, count}]}`: every tag, most used first, as [`markfind tags`](cli.md#tags) lists them. |
| `GET /api/broken?all=1` | `{"all", "failures": [{url, title, folder, kind, error, checked}]}` |
| `GET /api/stats` | `{"total", "indexed", "failed", "pending", "failed_by_kind"}` |

Design choices:

- **Read-only connections.** Each request opens the database with
  `mode=ro`. The server never writes, not even the schema check, so it can't
  contend with a `fetch` running at the same time. `markfind serve` creates or
  migrates the database once at startup, before serving.
- **Page text is data, never HTML.** Snippets come from arbitrary websites.
  The API marks matched words with the control characters `\x02` and `\x03`
  instead of HTML tags. The page splits on those characters and builds text
  nodes and `<mark>` elements with `textContent`, so markup in page text is
  displayed rather than run. Only `http(s)` URLs become links.
- **Defence in depth.** Every response sends `Content-Security-Policy:
  default-src 'none'; script-src 'self'; …` (no inline scripts are allowed),
  plus `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`.
  Static files are looked up in a fixed table, so paths like `/../x` can't
  reach other files.
- **DNS rebinding.** On a loopback address, any request whose `Host` header
  isn't `localhost`, `127.0.0.1` or `[::1]` is refused. Otherwise a website
  could point its own domain at `127.0.0.1` and read your results from your
  browser.
- **One server per port.** On Windows, Python's default `SO_REUSEADDR` lets
  two servers bind the same port, so the server uses `SO_EXCLUSIVEADDRUSE`
  there instead.

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
| 2 | Adds `bookmarks.source`. Existing bookmarks get no source, so they're never pruned until a later import records one. |
| 3 | No schema change. Pages fetched with no text, which used to count as indexed, are marked as `empty` failures. |
| 4 | Adds `bookmarks.tags` and a `tags` column to `bookmarks_fts`. FTS5 tables can't gain columns, so the index is copied into a new table, page text included. |
| 5 | Adds `bookmarks.browser_tags`, `my_tags` and `note`, and a `note` column to `bookmarks_fts` (copied the same way). Existing tags all came from the browser, so they're copied to `browser_tags`. |

See [Development](development.md#changing-the-database-schema) for how to add
a migration.
