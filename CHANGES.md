# Changelog

## Unreleased

- `markfind import` reads Firefox's `places.sqlite` directly, so there's no
  need to export to HTML. It reads a temporary copy, including recent changes
  in the WAL file, and skips tags (#7).

- Documentation in `docs/`: getting started, command reference, how it
  works, Python API, and development guide.
- Examples in `examples/`: an offline demo, a sample bookmark export, a
  Markdown export script, and scheduled refresh scripts for cron and
  Windows Task Scheduler. The test suite runs them.

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
- New `markfind duplicates` finds bookmarks saved more than once: the same
  URL apart from `http`/`https`, `www.`, trailing slashes, `#section` links
  and tracking parameters, or the same page text. The cleanest URL is
  starred, and `--urls` lists the other copies (#17).
- `stats` breaks failures down by kind; `fetch` prints a hint when SSL
  certificate checks fail (#1).
- Databases are versioned (`PRAGMA user_version`) and upgraded automatically
  when opened. Failures recorded by 0.1.0 get the kind `other`; run
  `markfind fetch --failed` to classify them.

## 0.1.0

First release.

- Import bookmarks from Netscape HTML exports (Chrome, Edge, Firefox,
  Safari) and Chrome's `Bookmarks` JSON file.
- Fetch bookmarked pages concurrently and index their text with SQLite FTS5.
- `search`, `list` and `stats` commands.
