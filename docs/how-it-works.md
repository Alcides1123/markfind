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
| `pdf.py` | Reading text and titles from PDFs, with the optional `pypdf` |
| `topics.py` | Grouping pages into topics: TF-IDF vectors and spherical k-means |
| `archive.py` | Showing a saved copy as a standalone page |
| `server.py` | The local web interface (`markfind serve`) and its JSON API |
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

**Conditional re-fetching.** A successful fetch stores whatever the response
offered as a validator: its `ETag`, its `Last-Modified`, or both. With
`markfind fetch --if-changed`, those go back out as `If-None-Match` and
`If-Modified-Since`, and a server that recognises them answers `304 Not
Modified` with no body. `fetch_page()` raises `NotModified`, and only the
fetch time is updated: the indexed text and the saved copy are left alone.

This is what HTTP caching is for, and it costs the sites nothing to answer. On
five real pages (sqlite.org, Wikipedia, the Python docs, git-scm.com and
example.com) a refresh went from 796 KB of page data to none at all. Both
validators are worth keeping: Wikipedia and example.com sent only
`Last-Modified`, while the Python docs sent both.

Bookmarks whose last fetch failed never take part, since there is nothing good
to revalidate against, and neither do pages whose server offered no validator.

Servers don't always say what they're sending. `sniff_content_type()`
checks the start of the body when the `Content-Type` header is missing (Python
would otherwise report `text/plain`) or says `text/plain`: a body that opens
with `<!DOCTYPE html`, `<html`, `<head` or `<body` is HTML. Without a header,
a `%PDF` signature means a PDF, and NUL bytes mean other binary data, which
isn't indexed.

**PDFs** (`pdf.py`) are read with `pypdf`, an optional dependency, so the
core stays standard-library only. The module imports it only when a PDF
arrives.

- **Size:** a PDF can't be read once truncated, so `fetch_page()` keeps
  reading past the usual 2 MB limit, up to `MAX_PDF_BYTES` (30 MB).
- **Encryption:** PDFs encrypted only to set permissions open with an
  empty password; ones that need a real password are reported as such.
- **Text:** the first `MAX_PAGES` (300) pages are read. Their text is
  indexed, and the saved copy has one paragraph per block of text, with a
  rule between pages.

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

**Robots directives are ignored, on purpose.** `<meta name="robots"
content="noindex">` (or the `X-Robots-Tag` header) asks public search engines
not to list a page, and `noarchive` asks them not to show a cached copy.
markfind isn't a search engine anyone else can query: it's a private index of
pages you chose to bookmark, on your own machine. So it indexes those pages
and keeps a [saved copy](#saved-copies) like any other. Leaving them out would
hide pages you saved yourself, such as a private wiki page or an
unlisted document, from the one search meant to find them.

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

These weights are a reasonable starting point, not tuned values. They haven't
been measured against real searches yet. The tool for that is
[`scripts/rank_eval.py`](development.md#evaluating-search-ranking), and the
evaluation itself is on the [roadmap](../ROADMAP.md).

The snippet is FTS5's `snippet()` over the `body` column: up to 16 tokens
around the matches.

## The web interface

`markfind serve` (`server.py`) is a `ThreadingHTTPServer` with JSON
endpoints, a page for saved copies, and three static files. The files (`web/index.html`, `app.js` and
`style.css`) ship as package data. There's no framework and no build step.

| Endpoint | Returns |
|---|---|
| `GET /api/similar?url=…&limit=N` | `{"url", "title", "keywords", "results": [...]}`, results as for search. `400` without `url`, `404` if the bookmark isn't in markfind. |
| `GET /api/search?q=…&raw=1&folder=PATH&tag=TAG&limit=N` | `{"query", "results": [{url, title, folder, snippet, tags, note, saved}]}`. `saved` says whether there's a saved copy. `folder` keeps one folder and its subfolders; `tag` keeps bookmarks with that tag, ignoring case. With `tag` and no `q`, lists the bookmarks with that tag in folder order, with empty snippets. `400` with `{"error"}` for an invalid raw query. |
| `GET /api/folders` | `{"folders": [{path, count}]}`: every folder, parents included, in tree order. A count includes subfolders. |
| `GET /api/tags` | `{"tags": [{tag, count}]}`: every tag, most used first, as [`markfind tags`](cli.md#tags) lists them. |
| `GET /api/broken?all=1` | `{"all", "failures": [{url, title, folder, kind, error, checked, saved}]}` |
| `GET /api/stats` | `{"total", "indexed", "failed", "pending", "failed_by_kind"}` |
| `GET /archive?url=…` | The bookmark's [saved copy](#saved-copies) as an HTML page, or `404` with a plain-text message if there's none. |

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
- **Saved copies are sandboxed.** `/archive` serves HTML built from other
  sites' pages, so on top of the allowlist it was built from, it gets its
  own policy: `sandbox; default-src 'none'; style-src 'sha256-…'`. The
  sandbox disables scripts and gives the page an opaque origin, so even a
  sanitizer bug couldn't call this server's API. Nothing outside can be
  loaded, and the one stylesheet allowed is markfind's own, by its hash.
- **DNS rebinding.** On a loopback address, any request whose `Host` header
  isn't `localhost`, `127.0.0.1` or `[::1]` is refused. Otherwise a website
  could point its own domain at `127.0.0.1` and read your results from your
  browser.
- **One server per port.** On Windows, Python's default `SO_REUSEADDR` lets
  two servers bind the same port, so the server uses `SO_EXCLUSIVEADDRUSE`
  there instead.

## Topics

`markfind topics` (`topics.py`, with `Store.topics()`) groups pages by the
words they use. It's plain Python over sparse dictionaries, with no
numerical libraries.

1. **Words.** Each indexed page's words are counted the way `similar` counts
   them: title words count three times, stopwords are dropped, and words are
   grouped by the index's own Porter stems.
2. **Vectors.** Each page becomes a TF-IDF vector, `(1 + log tf) × log(N /
   df)`, normalised to length 1. Some terms are left out:
   - terms on only one page, which can't group anything;
   - terms on more than half the pages, which can't tell most of them apart.

   Only a page's 100 heaviest terms are kept: enough to place it, and short
   vectors keep the clustering fast.
3. **Spherical k-means.** This is k-means on unit vectors, compared by cosine
   similarity.
   - **Start:** centres are seeded with k-means++, from a fixed seed, so the
     same pages always give the same topics.
   - **Speed:** pages are compared only with centres they share a term with,
     found through an inverted index.
   - **Restarts:** k-means can settle on a poor grouping, so it runs from 4
     starting points and keeps the one whose pages are closest to their
     centres in total.
4. **How many.** By default, k starts high, at one topic per 3 pages, capped
   at 40. Too few topics forces different subjects together, which nothing can
   undo later, while too many only splits a subject, which merging can fix. So
   topics whose centres are more than 0.15 alike are merged, and k-means runs
   again from the merged centres. With `--count`, there's no merging.
5. **Leftovers.** Pages less than 0.1 similar to their topic, and topics
   under `--min-size`, go to *No clear topic*.
6. **Labels.** Each topic is named by its centre's three heaviest stems, each
   shown as its most common spelling in the pages ("database", not
   "databas").

The settings come from real pages, using
[`scripts/topics_eval.py`](../scripts/topics_eval.py). They were chosen on two sets
of about 120 pages in 12 subjects each, then scored once on a third set of 108 pages
with other subjects and sites. ARI is the adjusted Rand index, where 1 is a perfect
grouping and 0 is chance.

| Set | ARI before | ARI now | Purity before | Purity now | Topics before | Topics now |
|---|---|---|---|---|---|---|
| First (used to choose) | 0.49 | 0.78 | 0.62 | 0.85 | 8 | 10 |
| Second (used to choose) | 0.51 | 0.67 | 0.63 | 0.74 | 8 | 9 |
| Third (scored once) | 0.47 | 0.86 | 0.57 | 0.91 | 7 | 11 |

The scores vary between sets and runs, so expect roughly 0.7 to 0.85, not the best
figure. On random small collections from the third set, 10 to 50 pages over 9 sizes,
the mean ARI rose from 0.63 to 0.84, and every size improved.

What's left on the third set: nginx and Node.js shared a topic, both being about
servers, Redis split in two, and 2 pages had no clear topic. These are encyclopedia
and documentation pages, so a real collection may do worse. Pairs of similar pages
stay in *No clear topic* under the default `--min-size` of 3.

**Large collections.** Every k-means pass touches every page, so past
`CLUSTER_SAMPLE` (3,000) pages the centres are found from a sample of that size
and the rest of the pages are placed against the finished centres in one pass.
The iterative part then costs about the same whatever the collection size:

| Pages | Before | Now |
|---|---|---|
| 5,000 | 99.5 s | 33.7 s |
| 10,000 | 529.1 s | 36.8 s |

On a synthetic collection of 10,000 pages in 10 subjects, the sampled run still
recovers all 10 exactly, with nothing left unsorted. Below the threshold nothing
is sampled, so those collections give exactly the topics they gave before.

## Saved copies

`extract_text()` builds the readable copy (`Page.html`) in the same pass as
the text, so the copy holds exactly the content that's indexed: the
`<main>`/`<article>`, or the block picked by text density, or the whole page
minus boilerplate.

- **Allowlist, not filtering.** The copy is rebuilt from scratch, never
  passed through:
  - only these tags are written: headings, `p`, lists, `blockquote`,
    `pre`/`code`, inline emphasis, tables, `a`, `br` and `hr`;
    `div`/`section`/`article`/`main`/`header` all become `div`;
  - other tags are dropped, but their text is kept;
  - text is always HTML-escaped;
  - the only attributes are `href` on links (made absolute, and only
    `http`/`https`) and numeric `colspan`/`rowspan` on table cells.
- **Balanced.** End tags without a matching start are dropped, and anything
  left open is closed, since the copy is a slice of a possibly broken page.
  Wrappers left empty are removed.
- **Stored compressed.** `set_page()` writes the copy to the `archive`
  table, zlib-compressed. `set_error()` leaves it alone, so a dead page
  keeps its last good copy.
- **Fallback.** For pages without a copy, `saved_copy()` falls back to the
  indexed text in `bookmarks_fts`, which also survives failed fetches.
- **Showing it.** `archive.render()` wraps the copy in a page with a banner
  and a fixed stylesheet. `markfind archive` writes that page to a file;
  `markfind serve` sends it with a sandboxing policy (see above).

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
| 6 | Adds the `archive` table for [saved copies](#saved-copies). |
| 7 | Adds `bookmarks.etag` and `last_modified` for [conditional re-fetching](#fetching). |

See [Development](development.md#changing-the-database-schema) for how to add
a migration.
