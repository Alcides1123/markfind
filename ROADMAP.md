# Roadmap

## 0.2 — Better indexing

- [x] Main-content extraction, so navigation menus and footers don't
      pollute results
- [x] Score unmarked `<div>` blocks by text density for pages without
      `<main>` / `<article>` (#39)
- [ ] Respect `<meta name="robots" content="noindex">`? (decide: it's a personal
      index, but be explicit about it)
- [ ] Index PDF bookmarks (optional dependency)
- [x] Re-fetch pages older than N days: `markfind fetch --older-than 90d`
- [x] Retry failed fetches: `markfind fetch --failed`
- [x] Per-host rate limiting during fetch
- [x] Honor `Retry-After` on HTTP 429 / 503 responses (#26)
- [x] Clearer error messages, plus a fix hint for SSL certificate failures (#1)
- [ ] Tune `bm25` column weights with a small set of real queries

## 0.3 — More sources

- [x] Import directly from Firefox's `places.sqlite` (read-only copy) (#7)
- [x] Import Firefox tags as searchable keywords (#35)
- [x] Auto-detect browser profile locations on Windows, macOS and Linux (#13)
- [x] `markfind sync`: import + fetch new bookmarks in one step (#13)
- [x] Remove bookmarks that disappeared from the browser export (`--prune`) (#13)
- [x] Safari: read `Bookmarks.plist` directly (needs Full Disk Access on macOS) (#31)

## 0.4 — Maintenance tools

- [x] Dead-link report: list bookmarks that now return 404 / DNS errors (#1)
- [x] Remove dead bookmarks from the index (`markfind broken --delete`) (#33)
- [x] Duplicate detection (same URL after normalization, or same page text) (#17)
- [x] Near-duplicate pages (mostly the same text, e.g. a page with a changed footer) (#37)
- [x] Export search results to Markdown / HTML / JSON, and a cleaned-up
      bookmark file to re-import into the browser (#19)

## 0.5 — Discovery

- [x] "More like this": `markfind similar URL` (#15)
- [x] Web UI: a "similar" link on each search result (#28)
- [ ] Group bookmarks into topics automatically, using the same keywords

## Later

- [x] Small local web UI (`markfind serve`) with search-as-you-type (#11)
- [x] Web UI: filter by folder, keyboard navigation through results
- [x] User tags and notes, searchable alongside page text (#43)
- [x] Save an archived copy of each page so results survive link rot (#45)
- [x] CI: GitHub Actions with lint, tests on Linux and Windows, and a package build (#3)
- [ ] Publish to PyPI
- [ ] Run CI on macOS too (dropped in #5 because jobs never got a runner)
