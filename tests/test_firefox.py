import hashlib
import sqlite3
from datetime import datetime, timezone

import pytest

from markfind import cli
from markfind.importers import load_file, parse_firefox_places

# The subset of Firefox's schema that markfind reads.
SCHEMA = """
CREATE TABLE moz_places (
    id INTEGER PRIMARY KEY,
    url LONGVARCHAR,
    title LONGVARCHAR
);
CREATE TABLE moz_bookmarks (
    id INTEGER PRIMARY KEY,
    type INTEGER,
    fk INTEGER DEFAULT NULL,
    parent INTEGER,
    position INTEGER,
    title LONGVARCHAR,
    dateAdded INTEGER,
    guid TEXT
);
"""

BOOKMARK, FOLDER = 1, 2
ADDED = 1_700_000_000_000_000  # microseconds; 2023-11-14 22:13:20 UTC


def make_places(path, wal=False):
    """A small places.sqlite resembling a real profile. Returns the open connection."""
    db = sqlite3.connect(path)
    if wal:
        db.execute("PRAGMA journal_mode = wal")
        # Keep changes in the -wal file, like a running Firefox.
        db.execute("PRAGMA wal_autocheckpoint = 0")
    db.executescript(SCHEMA)
    db.executemany(
        "INSERT INTO moz_places (id, url, title) VALUES (?, ?, ?)",
        [
            (1, "https://sqlite.org/fts5.html", "SQLite FTS5 Extension"),
            (2, "https://docs.python.org/3/", "Python docs (history title)"),
            (3, "https://news.example/", "News"),
            (4, "place:sort=8&maxResults=10", "Recent Tags"),
            (5, "about:preferences", "Settings"),
            (6, "https://untitled.example/", None),
        ],
    )
    db.executemany(
        "INSERT INTO moz_bookmarks (id, type, fk, parent, position, title, dateAdded, guid)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            # Built-in folders use internal titles; markfind shows display names.
            (1, FOLDER, None, 0, 0, "", 0, "root________"),
            (2, FOLDER, None, 1, 0, "menu", 0, "menu________"),
            (3, FOLDER, None, 1, 1, "toolbar", 0, "toolbar_____"),
            (4, FOLDER, None, 1, 2, "tags", 0, "tags________"),
            (5, FOLDER, None, 1, 3, "unfiled", 0, "unfiled_____"),
            (6, FOLDER, None, 1, 4, "mobile", 0, "mobile______"),
            # User folders.
            (10, FOLDER, None, 3, 0, "Dev", 0, "aaaaaaaaaaaa"),
            (11, FOLDER, None, 10, 0, "  Python  ", 0, "bbbbbbbbbbbb"),
            (12, FOLDER, None, 4, 0, "databases", 0, "cccccccccccc"),  # a tag
            # Bookmarks.
            (20, BOOKMARK, 1, 10, 0, "SQLite FTS5", ADDED, "dddddddddddd"),
            (21, BOOKMARK, 2, 11, 0, "", 0, "eeeeeeeeeeee"),  # falls back to history title
            (22, BOOKMARK, 3, 2, 0, "News", ADDED, "ffffffffffff"),
            (23, BOOKMARK, 1, 12, 0, None, ADDED, "gggggggggggg"),  # tag entry: skipped
            (24, BOOKMARK, 4, 3, 1, "Recent Tags", ADDED, "hhhhhhhhhhhh"),  # place: query
            (25, BOOKMARK, 5, 5, 0, "Settings", ADDED, "iiiiiiiiiiii"),  # about: page
            (26, BOOKMARK, 6, 6, 0, None, ADDED, "jjjjjjjjjjjj"),  # no title anywhere
        ],
    )
    db.commit()
    return db


def as_tuples(bookmarks):
    return sorted((b.url, b.title, b.folder) for b in bookmarks)


def test_parse_places(tmp_path):
    path = tmp_path / "places.sqlite"
    make_places(path).close()

    bookmarks = load_file(path)

    assert as_tuples(bookmarks) == [
        (
            "https://docs.python.org/3/",
            "Python docs (history title)",
            "Bookmarks Toolbar/Dev/Python",
        ),
        ("https://news.example/", "News", "Bookmarks Menu"),
        ("https://sqlite.org/fts5.html", "SQLite FTS5", "Bookmarks Toolbar/Dev"),
        ("https://untitled.example/", "", "Mobile Bookmarks"),
    ]
    by_url = {b.url: b for b in bookmarks}
    assert by_url["https://sqlite.org/fts5.html"].added_at == datetime(
        2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc
    )
    assert by_url["https://docs.python.org/3/"].added_at is None


def test_reads_uncommitted_wal_and_leaves_profile_untouched(tmp_path):
    path = tmp_path / "places.sqlite"
    db = make_places(path, wal=True)  # held open, like a running Firefox
    try:
        db.execute(
            "INSERT INTO moz_places (id, url, title) VALUES (7, 'https://new.example/', 'New')"
        )
        db.execute(
            "INSERT INTO moz_bookmarks (id, type, fk, parent, position, title, dateAdded, guid)"
            " VALUES (27, 1, 7, 5, 1, 'Just added', 0, 'kkkkkkkkkkkk')"
        )
        db.commit()
        wal = tmp_path / "places.sqlite-wal"
        assert wal.stat().st_size > 0

        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.iterdir()}
        bookmarks = parse_firefox_places(path)
        after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.iterdir()}

        assert ("https://new.example/", "Just added", "Other Bookmarks") in as_tuples(bookmarks)
        assert before == after
    finally:
        db.close()


def test_orphaned_folder_keeps_partial_path(tmp_path):
    path = tmp_path / "places.sqlite"
    db = make_places(path)
    db.execute("INSERT INTO moz_bookmarks VALUES (30, 2, NULL, 999, 0, 'Lost', 0, 'llllllllllll')")
    db.execute("INSERT INTO moz_bookmarks VALUES (31, 1, 1, 30, 0, 'Orphan', 0, 'mmmmmmmmmmmm')")
    db.commit()
    db.close()

    assert ("https://sqlite.org/fts5.html", "Orphan", "Lost") in as_tuples(load_file(path))


def test_other_sqlite_file_is_rejected(tmp_path):
    path = tmp_path / "other.sqlite"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE t (x)")
    db.commit()
    db.close()

    with pytest.raises(ValueError, match="not a readable Firefox places database"):
        load_file(path)


def test_cli_import(tmp_path, capsys):
    places = tmp_path / "places.sqlite"
    make_places(places).close()
    db = str(tmp_path / "markfind.sqlite")

    assert cli.main(["--db", db, "import", str(places)]) == 0
    assert "4 added, 0 updated" in capsys.readouterr().out

    assert cli.main(["--db", db, "list", "--folder", "Bookmarks Toolbar"]) == 0
    out = capsys.readouterr().out
    assert (
        "Bookmarks Toolbar/Dev/Python\tPython docs (history title)\thttps://docs.python.org/3/"
        in out
    )

    broken = tmp_path / "broken.sqlite"
    broken.write_bytes(b"SQLite format 3\x00" + b"\x00" * 200)
    assert cli.main(["--db", db, "import", str(broken)]) == 1
    assert "could not read" in capsys.readouterr().err
