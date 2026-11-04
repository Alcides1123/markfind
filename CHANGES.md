# Changelog

## Unreleased

- `markfind import` reads Safari's `~/Library/Safari/Bookmarks.plist`
  directly (binary or XML property list), keeping *Favorites*, *Bookmarks
  Menu* and *Reading List* as folders. `markfind sync` finds it on macOS
  (`--browser safari`). If macOS blocks access, markfind explains how to
  grant the terminal Full Disk Access (#31).

- `markfind serve`: a folder menu next to the search box limits results to one
  folder and its subfolders, with a bookmark count for each folder. Arrow keys
  move through the results, `Enter` opens one and `Shift+Enter` shows similar
  bookmarks. New endpoint `GET /api/folders`; `/api/search` takes `folder`.
- `markfind search --folder PATH`, and `Store.search(folder=...)` and
  `Store.folders()` in the Python API.

- `markfind serve`: each search result has a *Similar* button that shows the
  bookmarks most like it and the words they share, using the same matching as
  `markfind similar`. The view is kept in the page address, and *Back to
  search* or `Esc` returns to the search. New endpoint:
  `GET /api/similar?url=...` (#28).

- `fetch` and `sync` retry pages answered with HTTP 429 ("too many requests")
  or 503, after the wait the site's `Retry-After` header asks for, holding
  back every request to that site meanwhile. `--max-retry-wait S` (default 60)
  caps the wait; a site asking for longer fails at once, with the wait in the
  message (#26).
- Bot-protection challenge pages (Cloudflare, Vercel) are now `blocked`
  instead of `server` ("try later"), since an automated client can't pass
  them (#26).

- Pages that download fine but have no text (apps drawn by JavaScript, pages
  behind a login) are now an `empty` failure instead of counting as indexed,
  so `stats` shows what's really searchable. See them with
  `markfind broken --kind empty`; `fetch --failed` retries them. Existing
  databases are updated automatically (schema version 3) (#24).

- Fix: pages served without a `Content-Type` header, or HTML labelled
  `text/plain`, were indexed as raw HTML source (`<!DOCTYPE ...> <script>`...).
  markfind now recognises HTML from the page itself, skips binary data sent
  without a type, and drops a leading byte-order mark. Run
  `markfind fetch --all` to re-index affected pages (#22).

- Fix: Wikipedia articles (and other pages with page-wide flag classes on
  `<html>` or `<body>`) were indexed with no text. Class, id and role rules
  no longer apply to `<html>`, `<body>`, `<main>` and `<article>`; state
  classes like `has-sidebar` are ignored; and a page is never indexed nearly
  empty because of one misjudged wrapper. Run `markfind fetch --all` to
  re-index affected pages (#9).

- `markfind import` reads Firefox's `places.sqlite` directly, so there's no
  need to export to HTML. It reads a temporary copy, including recent changes
  in the WAL file, and skips tags (#7).
- New `markfind serve`: a local, read-only web interface with
  search-as-you-type, highlighted snippets, a dead-links view and stats.
  Standard library only. It listens on 127.0.0.1 by default and is protected
  against DNS rebinding, with a strict Content-Security-Policy (#11).
- `Store(path, readonly=True)` opens an existing database without writing.

- Documentation in `docs/`: getting started, command reference, how it
  works, Python API, and development guide.
- Examples in `examples/`: an offline demo, a sample bookmark export, a
  Markdown export script, and scheduled refresh scripts for cron and
  Windows Task Scheduler. The test suite runs them.

- New `markfind sync`: finds the bookmarks of every Chrome, Edge, Brave,
  Vivaldi, Chromium and Firefox profile on Windows, macOS and Linux, imports
  them, and fetches new pages in one step. `--list` shows what was found and
  `--browser` picks browsers (#13).
- `--prune` on `sync` and `import` removes bookmarks that were deleted in the
  browser. Only bookmarks last imported from a file that was read in this run
  are considered, so other imports are never touched (#13).
- Bookmarks remember the file they were imported from (schema version 2,
  upgraded automatically). `Store.prune()` and `markfind.browsers` are new in
  the Python API.

- Page text extraction now drops navigation, headers, footers, sidebars,
  forms, cookie banners, share widgets and hidden elements, detected by tag,
  ARIA role, and class / id names.
- When a page has a `<main>` or `<article>` with at least 30 words, only that
  content is indexed.
- Run `markfind fetch --all` to re-index pages fetched with 0.1.0.
- `fetch --older-than AGE` (e.g. `90d`, `2w`, `12h`) refreshes stale pages,
  and `fetch --failed` retries pages whose last fetch failed.
- New `markfind export` writes bookmarks as an HTML file every browser can
  import, or as Markdown, JSON or CSV. `--skip-dead` leaves out dead links,
  so you can import a cleaned-up file into the browser; `--folder` and
  `--query` choose what to export (#19).
- Per-site rate limiting: one request at a time per host, at least `--delay`
  seconds apart (default 1). Downloads are interleaved across hosts.
- Fetch errors are classified (`gone`, `dns`, `blocked`, `server`, `http`,
  `timeout`, `connection`, `ssl`, `unsupported`, `other`) and shown as
  readable messages such as `HTTP 404 Not Found` or `host not found` (#1).
- New `markfind broken` command lists likely-dead bookmarks; `--all`,
  `--kind` and `--urls` options (#1).
- New `markfind duplicates` finds bookmarks saved more than once: the same
  URL apart from `http`/`https`, `www.`, trailing slashes, `#section` links
  and tracking parameters, or the same page text. The cleanest URL is
  starred, and `--urls` lists the other copies (#17).
- `stats` breaks failures down by kind; `fetch` prints a hint when SSL
  certificate checks fail (#1).
- Databases are versioned (`PRAGMA user_version`) and upgraded automatically
  when opened. Failures recorded by 0.1.0 get the kind `other`; run
  `markfind fetch --failed` to classify them.

- New `markfind similar URL`: "more like this". Lists the bookmarks most like
  a given one and the distinctive words they share, chosen by TF-IDF over the
  index and stemmed like search. `Store.similar()` in the Python API (#15).

## 0.1.0

First release.

- Import bookmarks from Netscape HTML exports (Chrome, Edge, Firefox,
  Safari) and Chrome's `Bookmarks` JSON file.
- Fetch bookmarked pages concurrently and index their text with SQLite FTS5.
- `search`, `list` and `stats` commands.
