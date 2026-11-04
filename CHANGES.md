# Changelog

## Unreleased

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
- Per-site rate limiting: one request at a time per host, at least `--delay`
  seconds apart (default 1). Downloads are interleaved across hosts.
- Fetch errors are classified (`gone`, `dns`, `blocked`, `server`, `http`,
  `timeout`, `connection`, `ssl`, `unsupported`, `other`) and shown as
  readable messages such as `HTTP 404 Not Found` or `host not found` (#1).
- New `markfind broken` command lists likely-dead bookmarks; `--all`,
  `--kind` and `--urls` options (#1).
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
