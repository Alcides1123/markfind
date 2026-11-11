# Changelog

## Unreleased

- New `markfind fetch --if-changed` keeps the index fresh without downloading
  pages that haven't changed. markfind now remembers each page's `ETag` and
  `Last-Modified`, sends them back on a re-fetch, and skips the pages the
  server reports as unchanged; their text and saved copy are kept as they are.
  On five real pages a refresh dropped from 796 KB to nothing (#70).
  - Leave the option off after upgrading markfind, when you want every page
    read again with the new extractor.
  - Database schema 7 adds the two columns; older databases migrate on first
    use.

## 0.3.0 (2026-11-06)

- markfind is on PyPI: install it with `pipx install markfind` or
  `python -m pip install markfind`.

- `markfind topics` is much better on real pages, where unrelated subjects could
  end up together. It now starts from more topics and merges similar ones, using
  settings measured on labelled real pages: the adjusted Rand index (1 is perfect,
  0 is chance) rose from about 0.5 to between 0.67 and 0.86 across three sets. It's
  slower: 3,000 pages take under a minute instead of 5 to 10 seconds.
  `scripts/topics_eval.py` reproduces the evaluation (#65).

- HTTPS certificates: if the optional `truststore` package is installed
  (`markfind[truststore]`), markfind verifies through the operating system, so
  sites with good certificates no longer fail on Windows machines that haven't
  downloaded their root certificate yet. The README and the hint after a failed
  fetch no longer blame the site (#63).

- Fixed: pages sent as Markdown were `unsupported`. docs.docker.com sends it to
  markfind, so its pages couldn't be indexed. Run `markfind fetch --failed` to
  fetch them again (#67).

- New `scripts/rank_eval.py` measures search ranking on your own searches, tries
  other weights, and says whether a change is justified. `RANK_WEIGHTS` hasn't
  changed: that needs a set of real searches (#48).

- Packaging for PyPI: a workflow publishes version tags with trusted publishing,
  the project metadata has URLs and classifiers, the README's links work on
  PyPI, and the sdist includes the docs and examples (#51).

- A new workflow tests on macOS, separately because its runners don't always
  arrive, and so far it hasn't got one. In earlier runs on macOS, the test suite
  passed, including reading a Safari-format plist written by Apple's `plutil`,
  and `markfind sync` imported a real Firefox profile. Safari itself is still
  unchecked on a real Mac (#52).

- New `markfind topics`: groups your bookmarks into topics by the words
  their pages use, with no folders or tags needed, and names each topic by
  its most telling words.
  - **Options:** `--count`, `--min-size`, `--folder` and `--json`.
  - **Results:** the same bookmarks always give the same topics.
  - **Python API:** `Store.topics()` (#49).

- PDF bookmarks are indexed when the optional `pypdf` package is installed
  (`pip install "markfind[pdf]"`).
  - **What's read:** their text and title, from the first 300 pages. The
    saved copy is the text, page by page.
  - **Limits:** PDFs may be up to 30 MB, since a truncated PDF can't be
    read. Password-protected and damaged PDFs are `unsupported`, and scanned
    ones without text are `empty`.
  - **Without `pypdf`:** PDFs stay `unsupported`, now with a message saying
    how to add support.
  - **Re-fetching:** run `markfind fetch --failed` to index PDFs fetched
    before (#47).

- Fixed: a raw query with an unbalanced quote (`--raw '"open'`) or a bare
  `*` crashed `markfind search` and the web UI instead of reporting
  "invalid query". Found by the new browser tests (#53).

- Browser tests for the web interface (`tests_browser/`, run with
  Playwright) cover search as you type, folder and tag filters, keyboard
  navigation, the Similar view, dead links and saved copies, and check that
  page content is never run as HTML. CI runs them in their own job (#53).

- Documented that markfind indexes and saves copies of pages marked
  `noindex` or `noarchive`: those directives are for public search engines,
  and markfind is a private index of pages you chose to keep (#50).

## 0.2.0 (2026-11-05)

- markfind now keeps a readable copy of every page it fetches, so a
  bookmark that dies isn't lost: `markfind archive URL` opens it in your
  browser (`-o FILE` saves it, `--text` prints the text).
  - **The copy:** the same content markfind indexes, with headings,
    lists, code, tables and links, rebuilt from an allowlist so nothing
    from the page can run. It's stored compressed, and failed fetches
    never replace it.
  - **Older bookmarks:** pages fetched before this fall back to their
    indexed text.
  - **Elsewhere:** the web UI links to copies from search results and dead
    links, in a sandboxed page; `broken` marks bookmarks that have one, and
    `stats` counts them.
  - **Schema:** the database moves to version 6 automatically (#45).

- Add your own tags and notes to bookmarks: `markfind tag URL TAG...`
  (`--remove` to take them off) and `markfind note URL TEXT`. Your tags
  work wherever imported ones do and are kept apart from them, so a
  Firefox sync never changes them. Notes are searchable and shown in
  search results, the web UI and exports (as the bookmark's description in
  the HTML file). The database moves to schema version 5 automatically
  (#43).

- The web UI shows each result's tags. Click one, or pick it from the new
  tag menu, to filter by it; with the search box empty, that lists every
  bookmark with the tag. `/api/search` takes `tag`, and `/api/tags` lists
  the tags (#41).

- Pages without `<main>` or `<article>` are now judged by their text: the
  block holding most of the page's prose is kept, and unlabelled menus,
  link lists and similar chrome around it are left out of the index. It
  keeps the whole page unless one block holds at least 80% of the prose.
  Pages already fetched change when they're next fetched; `markfind fetch
  --all` re-fetches everything now (#39).

- `markfind duplicates` also finds pages with nearly the same text, such as
  an article saved from two sites or a page saved again after a small edit,
  and shows how alike they are. `--similarity PCT` (default 80) sets how
  alike; `--by similar` shows only these. They're left out of `--urls` unless
  asked for, since they may be versions worth keeping. In the Python API,
  `find_duplicates` and `Store.duplicates` include them by default, and
  `Group` has a new `similarity` field (#37).

- Firefox tags are imported, from `places.sqlite` (and so `markfind sync`)
  and from the `TAGS` attribute of Firefox's HTML export. They're searched
  with the rest of a bookmark and rank close to titles. New `markfind tags`
  lists them; `search`, `list` and `export` take `--tag`; search results,
  `/api/search` and every export format include them, and an HTML export
  keeps them for Firefox. Importing from a browser without tags keeps a
  bookmark's tags. The database moves to schema version 4, which rebuilds
  the search index once (#35).

- `markfind broken --delete` removes the bookmarks the report lists from
  markfind, after asking (`--yes` skips the question). It follows `--kind`
  and `--all`, and leaves the browser alone. New `Store.delete(urls)` in the
  Python API (#33).

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
