# Development

## Setup

```sh
git clone https://github.com/Alcides1123/markfind.git
cd markfind
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"
```

## Checks

Run these before pushing. CI runs the same checks.

```sh
python -m pytest            # tests
ruff check .                # lint
ruff format --check .       # formatting (use `ruff format .` to fix)
```

The tests never touch the network:

- Fetching is tested with fake `fetch` functions.
- Rate limiting is tested with a fake clock.
- `tests/test_examples.py` runs the scripts in `examples/`, so update those
  scripts when you change an API they use.

| Test file | Covers |
|---|---|
| `test_add.py` | `markfind add`: fetching on the spot, tags and notes, adding the same URL twice, bad addresses |
| `test_open.py` | `markfind open`: which match is opened, what is said about the rest, and what is never handed to a browser |
| `test_remove.py` | `markfind remove`: by address and by filter, what is confirmed, and what is refused |
| `test_importers.py` | Netscape HTML and Chrome JSON parsing |
| `test_export.py` | Every export format, escaping, the HTML round trip, `markfind export` |
| `test_firefox.py` | Firefox `places.sqlite` import, including WAL changes |
| `test_safari.py` | Safari `Bookmarks.plist` import (binary and XML), detection on macOS, the Full Disk Access hint |
| `test_server.py` | `markfind serve`: API, static files, security headers, Host checks, read-only access |
| `test_extract.py` | Text extraction and boilerplate removal |
| `test_fetch.py` | Rate limiting, host interleaving, duration parsing |
| `test_fetch_page.py` | Downloading against a local server: content types, sniffing, binary data |
| `test_conditional_fetch.py` | `fetch --if-changed`: validators, 304 handling, and what a skipped page keeps |
| `test_duplicates.py` | URL normalization (what must and must not match), text grouping, `markfind duplicates` |
| `test_errors.py` | Failure classification and schema migrations |
| `test_store.py` | Storage, indexing and search |
| `test_case.py` | Ignoring case means ignoring case: `--folder` and `--tag` over accents, Greek, `ß` and a dotted capital I, in the store, the CLI and the API |
| `test_folders.py` | The folder tree, searching one folder (including names with `%` and `_`), `search --folder` |
| `test_similar.py` | `markfind similar`: keyword choice, ranking, stemming, read-only use |
| `test_pdf.py` | PDF bookmarks: reading text and titles, limits, damaged and scanned files, running without `pypdf` |
| `test_rank_eval.py` | `scripts/rank_eval.py`: metrics, the verdict, leave-one-out, read-only use, bad query files |
| `test_topics_eval.py` | `scripts/topics_eval.py`: the scoring, and that the URL lists stay valid |
| `test_topics.py` | `markfind topics`: vectors, clustering, merging split topics, labels, the command |
| `test_notes.py` | Your own tags and notes, kept apart from imports |
| `test_archive_safety.py` | The saved-copy allowlist as an invariant: hostile markup in, only allowed tags and attributes out |
| `test_saved_copies.py` | Saved copies: sanitizing, storage, `markfind archive`, the sandboxed `/archive` page |
| `test_cli.py` | The `markfind` command end to end |
| `test_parser.py` | Every command is registered, has a handler, and appears in the help, the command reference and the README |
| `test_sync.py` | Browser detection with fake home folders, pruning, `sync` and `import --prune` |
| `test_examples.py` | The scripts in `examples/` |

### Browser tests

`tests_browser/` drives the web interface (`markfind serve` and
`web/app.js`) in a real browser with [Playwright](https://playwright.dev/python/).
The tests cover:

- search as you type, and searches loaded from the address;
- the folder and tag filters, and clicking a tag;
- keyboard navigation, the Similar view, and the dead-links view;
- saved copies, including their sandbox;
- that page content is never run as HTML.

They're kept out of the main run, so `python -m pytest` needs nothing beyond
pytest.

```sh
python -m pip install -e ".[browser]"
python -m playwright install chromium     # once: downloads Playwright's Chromium
python -m pytest tests_browser
```

To use a browser you already have instead of downloading one, set
`MARKFIND_BROWSER_CHANNEL` to `msedge` or `chrome`:

```sh
MARKFIND_BROWSER_CHANNEL=msedge python -m pytest tests_browser
```

Each test starts a `MarkfindServer` on a temporary database. Requests to any
other site are answered locally, so the tests never use the network.

## Continuous integration

[`.github/workflows/ci.yml`](../.github/workflows/ci.yml) runs on every push:

- **Lint**: `ruff check` and `ruff format --check`.
- **Test**: pytest on Ubuntu with Python 3.10–3.13 and on Windows with
  Python 3.10 and 3.13. Each job first checks that Python's SQLite has FTS5.
- **Browser tests**: `tests_browser/` with Playwright's Chromium on Ubuntu.
- **Build**: builds the sdist and wheel, runs `twine check`, installs the
  wheel in a clean virtualenv and runs `markfind --version`.

[`.github/workflows/macos.yml`](../.github/workflows/macos.yml) runs the tests on
macOS with Python 3.13, then starts a real Firefox and imports its profile with
`markfind sync`. It's a separate workflow because macOS runners don't always
arrive: a job can wait out its 15-minute timeout without starting, shown as
*cancelled* with no steps. That says nothing about the code, so rerun it. It runs
on pushes to `main`, weekly, on demand, and on branches with `macos` in their name.

Safari can't be checked this way. On a CI runner, Safari doesn't write
`Bookmarks.plist` until something is saved, saving through AppleScript waits on a
permission prompt nobody can answer, and the Safari folder is readable without
Full Disk Access. So Safari's tests use fixture files, one of them written by
Apple's `plutil`.

## Workflow

1. Open an issue that describes the change, its acceptance criteria and any
   design choices.
2. Create a branch named after it, for example `7-retry-after`.
3. Commit the code, tests and docs together. Update `CHANGES.md` under
   *Unreleased*, and tick the item in `ROADMAP.md` if there is one.
4. Open a pull request with `Closes #N` in the description, and merge it when
   CI is green.

## Changing the database schema

Existing users' databases must keep working, so every schema change has to
come with a migration.

1. Update `SCHEMA` in `store.py` so a **new** database gets the latest schema
   directly.
2. Write a migration function that upgrades an **old** database to the same
   result. It must also do nothing on a new database, because `SCHEMA` has
   already created everything. Check before altering:

   ```python
   def _add_notes(db: sqlite3.Connection) -> None:
       columns = {row[1] for row in db.execute("PRAGMA table_info(bookmarks)")}
       if "notes" not in columns:
           db.execute("ALTER TABLE bookmarks ADD COLUMN notes TEXT NOT NULL DEFAULT ''")
   ```

3. Append it to `MIGRATIONS`. Never reorder or remove entries, because a
   migration's position is its version number.
4. Add a test that builds a database in the **previous** schema by hand, opens
   it with `Store`, and checks that the data survived. See
   `test_migrates_0_1_0_database` in `tests/test_errors.py`.
5. Add a row to the version table in [How it works](how-it-works.md#schema-versions-and-migrations).

## Evaluating search ranking

`scripts/rank_eval.py` measures how well `RANK_WEIGHTS` ranks real searches. It
opens the database read-only and never changes it.

1. Write a query file like
   [`scripts/rank_queries.example.json`](../scripts/rank_queries.example.json):
   searches you'd really make, each with the bookmarks you'd want first. Write
   the searches before looking at the results, or they'll favour the current
   ranking. Only searches and URLs go in the file, never page text, but they
   still show what you bookmark, so decide whether to commit yours.
2. See where the current weights put them:

   ```sh
   python scripts/rank_eval.py queries.json
   ```

   Each line shows the rank of the first expected bookmark. The summary gives
   mean reciprocal rank (MRR: 1 if every search puts it first, 0.5 if every
   search puts it second) and hits@3. A `-` means it wasn't found: searches need
   every word to match, so no weights can fix that.
3. Try the alternatives, which takes a few minutes on a large database (2.5
   minutes for 3,000 pages and 15 searches):

   ```sh
   python scripts/rank_eval.py queries.json --grid
   ```

The script tries 729 combinations, and on a few searches some will win by chance.
So its verdict rests on leave-one-out MRR, where each search is scored by the
weights that did best on all the others. It says to change the weights only with:

- at least 10 searches;
- a leave-one-out MRR at least 0.05 above the current one;
- more searches helped than hurt.

The thresholds are judgement calls, kept as constants at the top of the script.
Whichever way it goes, record the numbers in `docs/how-it-works.md`.

## Releasing

1. Move the *Unreleased* entries in `CHANGES.md` under a new version heading.
2. Bump the version in `pyproject.toml` and `src/markfind/__init__.py`.
3. Merge, tag `vX.Y.Z` on `main`, and create a GitHub release from the tag.

Pushing the tag runs [`.github/workflows/publish.yml`](../.github/workflows/publish.yml).
It builds the sdist and wheel, installs each in a clean virtualenv, checks that
the tag matches the version, and publishes to PyPI. PyPI never accepts the same
version twice, so a mistake needs a new version number.

**One-time setup,** before the first upload:

1. On PyPI, open *Your projects → Publishing* and add a pending publisher:
   project `markfind`, owner `Alcides1123`, repository `markfind`, workflow
   `publish.yml`, environment `pypi`.
2. In the GitHub repository, *Settings → Environments → New environment*, name
   it `pypi`. Adding yourself as a required reviewer makes each upload wait for
   your approval.

A tag only runs the workflow if the tagged commit contains it, so tags made
before `publish.yml` existed never publish.
