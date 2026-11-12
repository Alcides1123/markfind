# Roadmap

## Planned work

Each item has an issue with the details, and a rough estimate.

| Issue | Work | Estimate |
|---|---|---|
| #47 | ~~Index PDF bookmarks (optional `pypdf` dependency)~~ (done) | 6 h |
| #48 | Tune search ranking with a set of real queries | 5 h |
| #49 | ~~Group bookmarks into topics automatically (`markfind topics`)~~ (done) | 8 h |
| #50 | ~~Decide how to treat pages marked `noindex`~~ (done) | 1.5 h |
| #51 | Publish to PyPI, with a release workflow | 3 h |
| #52 | ~~Run CI on macOS~~ (done) and check Safari support on a real Mac | 2 h |
| #53 | ~~Browser tests for the web interface~~ (done) | 4 h |
| | **Total** | **29.5 h** |

The sections below list everything by theme, done and planned.

## Better indexing

- [x] Main-content extraction, so navigation menus and footers don't
      pollute results
- [x] Score unmarked `<div>` blocks by text density for pages without
      `<main>` / `<article>` (#39)
- [x] Respect `<meta name="robots" content="noindex">`? Decided: no, it's a
      personal index; documented in How it works (#50)
- [x] Index PDF bookmarks (optional dependency) (#47)
- [x] Re-fetch pages older than N days: `markfind fetch --older-than 90d`
- [x] Skip unchanged pages on a re-fetch, using `ETag` / `Last-Modified`
      (`markfind fetch --if-changed`) (#70)
- [x] Retry failed fetches: `markfind fetch --failed`
- [x] Per-host rate limiting during fetch
- [x] Honor `Retry-After` on HTTP 429 / 503 responses (#26)
- [x] Clearer error messages, plus a fix hint for SSL certificate failures (#1)
- [ ] Tune `bm25` column weights with a small set of real queries (#48)

## More sources

- [x] Add a bookmark by address from the command line: `markfind add URL` (#82)
- [x] Import directly from Firefox's `places.sqlite` (read-only copy) (#7)
- [x] Import Firefox tags as searchable keywords (#35)
- [x] Auto-detect browser profile locations on Windows, macOS and Linux (#13)
- [x] `markfind sync`: import + fetch new bookmarks in one step (#13)
- [x] Remove bookmarks that disappeared from the browser export (`--prune`) (#13)
- [x] Safari: read `Bookmarks.plist` directly (needs Full Disk Access on macOS) (#31)

## Maintenance tools

- [x] Dead-link report: list bookmarks that now return 404 / DNS errors (#1)
- [x] Remove dead bookmarks from the index (`markfind broken --delete`) (#33)
- [x] Duplicate detection (same URL after normalization, or same page text) (#17)
- [x] Near-duplicate pages (mostly the same text, e.g. a page with a changed footer) (#37)
- [x] Export search results to Markdown / HTML / JSON, and a cleaned-up
      bookmark file to re-import into the browser (#19)

## Discovery

- [x] "More like this": `markfind similar URL` (#15)
- [x] Web UI: a "similar" link on each search result (#28)
- [x] Group bookmarks into topics automatically, using the same keywords (#49)

## Later

- [x] Small local web UI (`markfind serve`) with search-as-you-type (#11)
- [x] Web UI: filter by folder, keyboard navigation through results
- [x] User tags and notes, searchable alongside page text (#43)
- [x] Save an archived copy of each page so results survive link rot (#45)
- [x] CI: GitHub Actions with lint, tests on Linux and Windows, and a package build (#3)
- [ ] Publish to PyPI (#51)
- [x] Test on macOS too, in a separate workflow since runners don't always arrive (#52)
- [ ] Check Safari import and the Full Disk Access message on a real Mac (#52)
- [x] Browser tests for the web interface (#53)
