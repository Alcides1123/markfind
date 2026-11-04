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
| `test_importers.py` | Netscape HTML and Chrome JSON parsing |
| `test_firefox.py` | Firefox `places.sqlite` import, including WAL changes |
| `test_server.py` | `markfind serve`: API, static files, security headers, Host checks, read-only access |
| `test_extract.py` | Text extraction and boilerplate removal |
| `test_fetch.py` | Rate limiting, host interleaving, duration parsing |
| `test_errors.py` | Failure classification and schema migrations |
| `test_store.py` | Storage, indexing and search |
| `test_cli.py` | The `markfind` command end to end |
| `test_sync.py` | Browser detection with fake home folders, pruning, `sync` and `import --prune` |
| `test_examples.py` | The scripts in `examples/` |

## Continuous integration

[`.github/workflows/ci.yml`](../.github/workflows/ci.yml) runs on every push:

- **Lint**: `ruff check` and `ruff format --check`.
- **Test**: pytest on Ubuntu with Python 3.10–3.13 and on Windows with
  Python 3.10 and 3.13. Each job first checks that Python's SQLite has FTS5.
- **Build**: builds the sdist and wheel, runs `twine check`, installs the
  wheel in a clean virtualenv and runs `markfind --version`.

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

## Releasing

1. Move the *Unreleased* entries in `CHANGES.md` under a new version heading.
2. Bump the version in `pyproject.toml` and `src/markfind/__init__.py`.
3. Merge, tag `vX.Y.Z` on `main`, and create a GitHub release from the tag.
   The *Build* job's `dist` artifact contains the sdist and wheel.
