# markfind

[![CI](https://github.com/Alcides1123/markfind/actions/workflows/ci.yml/badge.svg)](https://github.com/Alcides1123/markfind/actions/workflows/ci.yml)

Full-text search over your own browser bookmarks.

Browsers only search bookmark *titles*. markfind downloads the pages you've
bookmarked and indexes their text, so you can find "that article about SQLite
indexes" even when it's saved as "Untitled" in a folder you forgot about.
Everything stays on your machine in a single SQLite file.

**Who it's for:** anyone with hundreds of bookmarks who uses them as a
personal reference library.

**Built with:** Python 3.10+ and the standard library only — `sqlite3` with
the FTS5 full-text extension, `html.parser`, `urllib`, and `argparse`. No
third-party runtime dependencies.

## Install

```sh
git clone https://github.com/Alcides1123/markfind.git
cd markfind
python -m pip install -e .
```

To index bookmarked PDFs too, install the optional `pypdf` dependency with
`python -m pip install -e ".[pdf]"`. Everything else needs only the standard
library.

## Usage

1. **Export your bookmarks** to an HTML file:
   - Chrome / Edge: Bookmark manager → ⋮ → *Export bookmarks*
   - Firefox: Bookmarks → *Manage bookmarks* → Import and Backup → *Export Bookmarks to HTML*
   - Safari: File → Export → *Bookmarks*

   Or skip the export: `markfind sync` finds the bookmarks of Chrome, Edge,
   Brave, Vivaldi, Chromium, Firefox and Safari on its own, imports them and fetches
   the pages in one step. Run it again whenever you like; `--prune` also
   removes bookmarks you deleted in the browser.

2. **Import, fetch, search:**

   ```sh
   markfind import ~/Downloads/bookmarks.html
   markfind fetch                 # downloads pages and indexes their text
   markfind search sqlite index   # all words must match
   ```

   ```text
   1. Optimizing SQLite full-text search
      https://example.com/sqlite-fts
      folder: Bookmarks bar/Dev
      ...how to tune [SQLite] full-text [indexes] for speed...
   ```

3. **Or search in your browser:** `markfind serve --open` starts a local page
   where results update as you type.

### Commands

| Command | What it does |
|---|---|
| `markfind import FILE...` | Add bookmarks from an HTML export, Chrome / Edge `Bookmarks` file, Firefox `places.sqlite`, or Safari `Bookmarks.plist`. Re-importing updates titles and folders and keeps fetched text; `--prune` removes bookmarks no longer in the file. |
| `markfind sync [--prune] [--browser NAME] [--list]` | Find every installed browser's bookmarks (all profiles), import them and fetch new pages. `--prune` removes bookmarks deleted in the browser; `--list` only shows what was found; `--no-fetch` skips fetching. |
| `markfind fetch [--older-than AGE] [--failed] [--all]` | Download pages not fetched yet. `--older-than 90d` also refreshes pages fetched more than 90 days ago, `--failed` retries failed ones, `--all` re-fetches everything. Tuning: `--workers N` (default 8), `--timeout S` (default 10), `--delay S` between requests to the same site (default 1), `--max-retry-wait S` for sites that say "too many requests" (default 60). |
| `markfind search WORDS... [-n N] [--raw] [--folder PATH] [--tag TAG]` | Search titles, URLs, folders, tags and page text, best matches first. `--folder` keeps one folder and its subfolders, `--tag` one tag. `--raw` passes [FTS5 query syntax](https://sqlite.org/fts5.html#full_text_query_syntax) through, e.g. `--raw 'title:python OR rust'`. |
| `markfind similar URL [-n N]` | "More like this": bookmarks most like this one, and the distinctive words they share. |
| `markfind list [--folder PATH] [--tag TAG]` | List bookmarks, optionally one folder and its subfolders, or one tag. |
| `markfind tags` | Tags from Firefox and your own, with how many bookmarks have each. |
| `markfind tag URL TAG...` | Add your own tags to a bookmark (`--remove` takes them off). Imports never change them. |
| `markfind note URL TEXT` | Write a note on a bookmark: searchable, and shown in results and exports. |
| `markfind duplicates [--by url\|text\|similar] [--urls]` | Bookmarks saved more than once: the same URL apart from `http`/`https`, `www.`, tracking parameters and the like, the same page text, or nearly the same text (`--similarity`, default 80%). The cleanest URL is starred; `--urls` prints the other copies. |
| `markfind broken [--all] [--kind KIND] [--urls \| --delete]` | Bookmarks that are likely dead (HTTP 404/410 or unknown host). `--all` shows every failure, grouped by kind; `--kind` picks kinds (`gone`, `dns`, `blocked`, `server`, `http`, `timeout`, `connection`, `ssl`, `unsupported`, `empty`, `other`); `--urls` prints bare URLs for scripting; `--delete` removes the listed bookmarks from markfind after asking. |
| `markfind export [-f FORMAT] [-o FILE] [--skip-dead]` | Write bookmarks as an HTML file any browser can import, or as Markdown, JSON or CSV. `--skip-dead` leaves out dead links; `--folder` and `--query` pick which bookmarks. |
| `markfind archive URL` | Open markfind's saved copy of a page in your browser, even after the page is gone. `-o FILE` saves it, `--text` prints the text. |
| `markfind stats` | How many bookmarks are indexed, failed (by kind), or pending, and how many saved copies there are. |
| `markfind serve [--open] [--port N]` | Search in your browser: results update as you type, folder and tag filters, arrow-key navigation, a *Similar* button on each result, *Saved copy* links, and a dead-links view. Local and read-only. |

When indexing a page, markfind skips navigation, footers, sidebars, cookie
banners and similar boilerplate, and keeps only the `<main>` / `<article>`
content when a page has one. Pages without those are judged by their text:
unlabelled menus and link lists around the block holding the page's prose
are left out. Either way, searches match what the page is actually about.

It also saves a readable copy of that content: text, headings, lists, code,
tables and links, without scripts or images, compressed to a few KB a page.
When a bookmark dies, `markfind archive URL` still shows you what it said.

Fetching is polite: markfind sends at most one request at a time to each
site, waits `--delay` seconds between them, and spreads the work across sites
so a folder full of links to one domain doesn't slow everything else down.

The database lives at `~/.markfind.sqlite`. Override it with `--db PATH` or
the `MARKFIND_DB` environment variable.

## Troubleshooting

### SSL certificate errors

If `markfind fetch` reports `ssl: certificate verification failed` for most
HTTPS sites, Python can't find the certificate authorities it needs to trust
them. Common causes and fixes:

- **python.org installer on macOS:** run *Install Certificates.command* from
  the `Applications/Python 3.x` folder.
- **Antivirus or a company proxy that inspects HTTPS:** export its root
  certificate (or get it from your IT team) and point Python at it with the
  `SSL_CERT_FILE` environment variable.
- **Anywhere else:** use the Mozilla certificate bundle from `certifi`:

  ```sh
  python -m pip install certifi
  # bash / zsh
  export SSL_CERT_FILE="$(python -m certifi)"
  # PowerShell
  $env:SSL_CERT_FILE = python -m certifi
  ```

If only a few sites fail, those sites likely have expired or misconfigured
certificates; see them with `markfind broken --kind ssl`.

## Documentation and examples

- [Getting started](docs/getting-started.md): install, export, first search
- [Command reference](docs/cli.md): every option, advanced search syntax
- [How it works](docs/how-it-works.md): extraction, ranking, schema
- [Python API](docs/python-api.md): use markfind from your own code
- [Examples](examples/README.md): an offline demo, a Markdown export script,
  and scheduled refresh scripts. Try `python examples/offline_demo.py`.

## Development

```sh
python -m pip install -e ".[dev]"
python -m pytest
ruff check . && ruff format --check .
```

Every push runs [CI](.github/workflows/ci.yml): ruff lint and format checks,
the test suite on Linux and Windows (Python 3.10–3.13), and a package
build with a smoke test of the installed wheel. See
[docs/development.md](docs/development.md) for the workflow, schema
migrations and releases.

Planned work is in [ROADMAP.md](ROADMAP.md).

## License

BSD 3-Clause — see [LICENSE](LICENSE).
