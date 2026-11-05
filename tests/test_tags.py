"""Tags: imported from Firefox, searchable, filterable and exported."""

import csv
import io
import json
import sqlite3

import pytest
from test_firefox import BOOKMARK, FOLDER, make_places

from markfind import cli
from markfind.formats import to_csv, to_html, to_json
from markfind.importers import (
    Bookmark,
    clean_tags,
    load_file,
    parse_chrome_json,
    parse_netscape_html,
)
from markfind.server import _result
from markfind.store import SCHEMA_VERSION, Store


def tagged_places(path):
    """The test_firefox profile, with more tags: databases + SQL on SQLite, python on docs."""
    db = make_places(path)
    db.executemany(
        "INSERT INTO moz_bookmarks (id, type, fk, parent, position, title, dateAdded, guid)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (13, FOLDER, None, 4, 1, "SQL", 0, "kkkkkkkkkkkk"),
            (14, FOLDER, None, 4, 2, " python ", 0, "llllllllllll"),
            (30, BOOKMARK, 1, 13, 0, None, 0, "mmmmmmmmmmmm"),
            (31, BOOKMARK, 2, 14, 0, None, 0, "nnnnnnnnnnnn"),
        ],
    )
    db.commit()
    db.close()


def by_url(bookmarks):
    return {b.url: b.tags for b in bookmarks}


def test_firefox_tags(tmp_path):
    path = tmp_path / "places.sqlite"
    tagged_places(path)
    tags = by_url(load_file(path))
    assert tags["https://sqlite.org/fts5.html"] == ("databases", "SQL")
    assert tags["https://docs.python.org/3/"] == ("python",)
    # Firefox can hold tags, so an untagged bookmark has none, not "unknown".
    assert tags["https://news.example/"] == ()
    # Tag entries are not bookmarks of their own.
    assert len(load_file(path)) == 4


def test_html_tags_attribute():
    html = """<DL><p>
    <DT><A HREF="https://a.example/" TAGS="python, Web,,PYTHON">A</A>
    <DT><A HREF="https://b.example/" TAGS="">B</A>
    <DT><A HREF="https://c.example/">C</A>
    </DL><p>"""
    tags = by_url(parse_netscape_html(html))
    assert tags == {
        "https://a.example/": ("python", "Web"),
        "https://b.example/": (),
        "https://c.example/": None,
    }


def test_chrome_has_no_tags():
    text = json.dumps(
        {"roots": {"bookmark_bar": {"children": [{"type": "url", "url": "https://a.example/"}]}}}
    )
    assert parse_chrome_json(text)[0].tags is None


def test_clean_tags():
    assert clean_tags([" a  b ", "A B", "c,d", "", "e"]) == ("a b", "c d", "e")


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "db.sqlite") as store:
        store.add_bookmarks(
            [
                Bookmark("https://a.example/", "Alpha", "Dev", tags=("rust", "Reading")),
                Bookmark("https://b.example/", "Beta", "Dev", tags=("reading",)),
                Bookmark("https://c.example/", "Gamma", "Misc"),
            ]
        )
        store.set_page(1, "a page about compilers")
        store.set_page(2, "a page about gardens")
        store.set_page(3, "a page about rust on old bikes")
        yield store


def test_tags_are_searchable(store):
    # "reading" appears only as a tag.
    assert sorted(r.url for r in store.search('"reading"')) == [
        "https://a.example/",
        "https://b.example/",
    ]
    results = {r.url: r.tags for r in store.search('"page"')}
    assert results["https://a.example/"] == ("rust", "Reading")
    assert results["https://c.example/"] == ()
    # A tag outranks the same word in the page text.
    assert store.search('"rust"')[0].url == "https://a.example/"
    assert [r.url for r in store.search("tags:rust")] == ["https://a.example/"]


def test_tag_filter(store):
    assert sorted(r.url for r in store.search('"page"', tag="READING")) == [
        "https://a.example/",
        "https://b.example/",
    ]
    assert [r.url for r in store.search('"page"', tag="rust")] == ["https://a.example/"]
    assert store.search('"page"', tag="rus") == []  # whole tags only
    assert [u for u, _, _ in store.list_bookmarks(tag="rust")] == ["https://a.example/"]
    assert [u for u, _, _ in store.list_bookmarks(folder="Dev", tag="reading")] == [
        "https://a.example/",
        "https://b.example/",
    ]
    assert [r.url for r in store.records(tag="Rust")] == ["https://a.example/"]


def test_tag_counts(store):
    # Same tag in different case counts once, under the most common spelling
    # (a tie goes to the first seen).
    assert store.tags() == [("Reading", 2), ("rust", 1)]


def test_reimport_keeps_or_replaces_tags(store):
    # A source without tags (Chrome, Safari) keeps the recorded ones...
    store.add_bookmarks([Bookmark("https://a.example/", "Alpha 2", "Dev")])
    assert store.records(tag="rust")[0].title == "Alpha 2"
    # ...one with tags replaces them, and an empty tuple clears them.
    store.add_bookmarks([Bookmark("https://a.example/", "Alpha", "Dev", tags=("lang",))])
    assert store.tags() == [("lang", 1), ("reading", 1)]
    assert store.search('"rust"', tag="lang") == []  # the old tag left the index
    store.add_bookmarks([Bookmark("https://a.example/", "Alpha", "Dev", tags=())])
    assert store.tags() == [("reading", 1)]
    # Page text survives all of it.
    assert [r.url for r in store.search('"compilers"')] == ["https://a.example/"]


def test_migration_adds_tags_to_existing_index(tmp_path):
    path = tmp_path / "db.sqlite"
    # A version 3 database, exactly as the previous release created it: the
    # trailing comment matters, since ADD COLUMN edits that definition.
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE bookmarks (
            id INTEGER PRIMARY KEY,
            url TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL DEFAULT '',
            folder TEXT NOT NULL DEFAULT '',
            added_at TEXT,
            fetched_at TEXT,
            fetch_error TEXT,
            error_kind TEXT,  -- one of fetch.ERROR_KINDS when fetch_error is set
            source TEXT  -- file the bookmark was last imported from, for pruning
        );
        CREATE VIRTUAL TABLE bookmarks_fts USING fts5(
            title, url, body, folder, tokenize = 'porter unicode61 remove_diacritics 2');
        INSERT INTO bookmarks (id, url, title, folder, fetched_at)
            VALUES (1, 'https://a.example/', 'Alpha', 'Dev', '2026-11-01T00:00:00+00:00');
        INSERT INTO bookmarks_fts (rowid, title, url, body, folder)
            VALUES (1, 'Alpha', 'https://a.example/', 'words about compilers', 'Dev');
        PRAGMA user_version = 3;
        """
    )
    db.close()

    with Store(path) as store:
        assert store.db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 4
        columns = [row[1] for row in store.db.execute("PRAGMA table_info(bookmarks)")]
        assert columns[-2:] == ["source", "tags"]
        assert [r.url for r in store.search('"compilers"')] == ["https://a.example/"]
        store.add_bookmarks([Bookmark("https://a.example/", "Alpha", "Dev", tags=("lang",))])
        assert [r.url for r in store.search('"lang"')] == ["https://a.example/"]
        assert [r.url for r in store.search('"compilers"')] == ["https://a.example/"]


def test_export_tags(store):
    records = store.records()
    html = to_html(records)
    assert 'TAGS="rust,Reading"' in html
    assert by_url(parse_netscape_html(html))["https://a.example/"] == ("rust", "Reading")
    assert by_url(parse_netscape_html(html))["https://c.example/"] is None

    rows = {r["url"]: r["tags"] for r in json.loads(to_json(records))}
    assert rows["https://a.example/"] == ["rust", "Reading"]
    assert rows["https://c.example/"] == []

    table = {r["url"]: r["tags"] for r in csv.DictReader(io.StringIO(to_csv(records)))}
    assert table["https://a.example/"] == "rust,Reading"


def test_server_result_has_tags(store):
    assert _result(store.search('"compilers"')[0])["tags"] == ["rust", "Reading"]


def test_cli(tmp_path, capsys):
    places = tmp_path / "places.sqlite"
    tagged_places(places)
    db = str(tmp_path / "db.sqlite")

    assert cli.main(["--db", db, "tags"]) == 0
    assert "no tags" in capsys.readouterr().out

    cli.main(["--db", db, "import", str(places)])
    capsys.readouterr()
    assert cli.main(["--db", db, "tags"]) == 0
    assert capsys.readouterr().out.splitlines() == ["1  databases", "1  python", "1  SQL"]

    assert cli.main(["--db", db, "search", "sqlite", "--tag", "sql"]) == 0
    out = capsys.readouterr().out
    assert "https://sqlite.org/fts5.html" in out and "tags: databases, SQL" in out
    assert cli.main(["--db", db, "search", "sqlite", "--tag", "python"]) == 1

    assert cli.main(["--db", db, "list", "--tag", "python"]) == 0
    assert "https://docs.python.org/3/" in capsys.readouterr().out

    assert cli.main(["--db", db, "export", "--tag", "databases", "-f", "json"]) == 0
    assert [r["url"] for r in json.loads(capsys.readouterr().out)] == [
        "https://sqlite.org/fts5.html"
    ]
