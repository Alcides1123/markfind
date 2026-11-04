# Roadmap

## 0.2 — Better indexing

- [x] Main-content extraction, so navigation menus and footers don't
      pollute results
- [ ] Score unmarked `<div>` blocks by text density for pages without
      `<main>` / `<article>`
- [ ] Respect `<meta name="robots" content="noindex">`? (decide: it's a personal
      index, but be explicit about it)
- [ ] Index PDF bookmarks (optional dependency)
- [x] Re-fetch pages older than N days: `markfind fetch --older-than 90d`
- [x] Retry failed fetches: `markfind fetch --failed`
- [x] Per-host rate limiting during fetch
- [ ] Honor `Retry-After` on HTTP 429 / 503 responses
- [x] Clearer error messages, plus a fix hint for SSL certificate failures (#1)
- [ ] Tune `bm25` column weights with a small set of real queries

## 0.3 — More sources

- [x] Import directly from Firefox's `places.sqlite` (read-only copy) (#7)
- [ ] Import Firefox tags as searchable keywords
- [ ] Auto-detect browser profile locations on Windows, macOS and Linux
- [ ] `markfind sync`: import + fetch new bookmarks in one step
- [ ] Remove bookmarks that disappeared from the browser export (`--prune`)

## 0.4 — Maintenance tools

- [x] Dead-link report: list bookmarks that now return 404 / DNS errors (#1)
- [ ] Remove dead bookmarks from the index (`markfind broken --delete`)
- [x] Duplicate detection (same URL after normalization, or same page text) (#17)
- [ ] Near-duplicate pages (mostly the same text, e.g. a page with a changed footer)
- [ ] Export search results to Markdown / HTML / JSON

## Later

- [ ] Small local web UI (`markfind serve`) with search-as-you-type
- [ ] User tags and notes, searchable alongside page text
- [ ] Save an archived copy of each page so results survive link rot
- [ ] Publish to PyPI and set up CI (GitHub Actions, tests on Windows/macOS/Linux)
