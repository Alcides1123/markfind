# Changelog

## Unreleased

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

## 0.1.0

First release.

- Import bookmarks from Netscape HTML exports (Chrome, Edge, Firefox,
  Safari) and Chrome's `Bookmarks` JSON file.
- Fetch bookmarked pages concurrently and index their text with SQLite FTS5.
- `search`, `list` and `stats` commands.
