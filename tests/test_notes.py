"""Your own tags and notes: kept apart from imports, searchable and exported."""

import json
import sqlite3

import pytest

from markfind import cli
from markfind.formats import to_html, to_json, to_markdown
from markfind.importers import Bookmark, parse_netscape_html
from markfind.server import _result
from markfind.store import SCHEMA_VERSION, BookmarkTags, Store

A = "https://a.example/"
B = "https://b.example/"


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "db.sqlite") as store:
        store.add_bookmarks(
            [
                Bookmark(A, "Alpha", "Dev", tags=("rust",)),  # tags from Firefox
                Bookmark(B, "Beta", "Reading"),  # from a browser without tags
            ]
        )
        ids = dict((url, id_) for id_, url in store.pending())
        store.set_page(ids[A], "a page about compilers")
        store.set_page(ids[B], "a page about gardening")
        yield store


def test_add_and_remove_tags(store):
    assert store.add_tags(A, ["Lang", "rust", "lang", " to read "]) == ("Lang", "to read")
    assert store.bookmark_tags(A) == BookmarkTags(browser=("rust",), mine=("Lang", "to read"))
    assert store.bookmark_tags(A).all == ("rust", "Lang", "to read")
    assert store.add_tags(A, ["LANG"]) == ()  # already there, ignoring case

    assert store.remove_tags(A, ["lang", "rust", "nope"]) == ("Lang",)  # rust is the browser's
    assert store.bookmark_tags(A) == BookmarkTags(browser=("rust",), mine=("to read",))

    with pytest.raises(KeyError):
        store.add_tags("https://missing.example/", ["x"])
    with pytest.raises(KeyError):
        store.bookmark_tags("https://missing.example/")


def test_your_tags_are_searched_filtered_and_counted(store):
    store.add_tags(B, ["Botany", "rust"])
    assert [r.url for r in store.search('"botany"')] == [B]
    assert [r.url for r in store.search('"gardening"', tag="botany")] == [B]
    assert sorted(r.url for r in store.search("page", tag="rust")) == [A, B]
    assert [url for url, *_ in store.list_bookmarks(tag="BOTANY")] == [B]
    assert store.tags() == [("rust", 2), ("Botany", 1)]
    assert store.search('"gardening"')[0].tags == ("Botany", "rust")

    store.remove_tags(B, ["botany"])
    assert store.search('"botany"') == []
    assert store.tags() == [("rust", 2)]


def test_imports_never_change_your_tags(store):
    store.add_tags(A, ["mine"])
    # Firefox now has different tags for the page.
    store.add_bookmarks([Bookmark(A, "Alpha", "Dev", tags=("systems",))])
    assert store.bookmark_tags(A) == BookmarkTags(browser=("systems",), mine=("mine",))
    assert store.search('"rust"') == []
    assert [r.url for r in store.search('"systems"', tag="mine")] == [A]
    # A browser without tags leaves both alone.
    store.add_bookmarks([Bookmark(A, "Alpha", "Dev")])
    assert store.bookmark_tags(A).all == ("systems", "mine")
    # The browser can drop its tags; yours stay.
    store.add_bookmarks([Bookmark(A, "Alpha", "Dev", tags=())])
    assert store.bookmark_tags(A) == BookmarkTags(browser=(), mine=("mine",))
    # Page text survives all of it.
    assert [r.url for r in store.search('"compilers"')] == [A]


def test_notes(store):
    assert store.note(A) == ""
    store.set_note(A, "  Read section 4 before the migration.\n")
    assert store.note(A) == "Read section 4 before the migration."

    results = store.search('"migration"')
    assert [(r.url, r.note) for r in results] == [(A, "Read section 4 before the migration.")]
    assert [r.note for r in store.records() if r.url == A] == [
        "Read section 4 before the migration."
    ]

    # Re-importing and re-fetching keep the note searchable.
    store.add_bookmarks([Bookmark(A, "Alpha", "Dev", tags=("rust",))])
    store.set_page(store._id(A), "new page text")
    assert [r.url for r in store.search('"migration"')] == [A]

    store.set_note(A, "")
    assert store.note(A) == "" and store.search('"migration"') == []
    with pytest.raises(KeyError):
        store.set_note("https://missing.example/", "x")


def test_note_ranks_above_page_text(store):
    store.set_note(B, "compilers")
    assert [r.url for r in store.search('"compilers"')] == [B, A]


def test_migration_from_version_4(tmp_path):
    path = tmp_path / "db.sqlite"
    # A version 4 database exactly as the previous release created it.
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
            source TEXT,  -- file the bookmark was last imported from, for pruning
            tags TEXT NOT NULL DEFAULT ''  -- comma-separated
        );
        CREATE VIRTUAL TABLE bookmarks_fts USING fts5(
            title, url, body, folder, tags,
            tokenize = 'porter unicode61 remove_diacritics 2');
        INSERT INTO bookmarks (id, url, title, folder, fetched_at, tags)
            VALUES (1, 'https://a.example/', 'Alpha', 'Dev', '2026-11-01T00:00:00+00:00', 'rust');
        INSERT INTO bookmarks_fts (rowid, title, url, body, folder, tags)
            VALUES (1, 'Alpha', 'https://a.example/', 'words about compilers', 'Dev', 'rust');
        PRAGMA user_version = 4;
        """
    )
    db.close()

    with Store(path) as store:
        assert store.db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        fts = [row[1] for row in store.db.execute("PRAGMA table_info(bookmarks_fts)")]
        assert fts == ["title", "url", "body", "folder", "tags", "note"]
        # Tags so far came from the browser, so an import may replace them.
        assert store.bookmark_tags(A) == BookmarkTags(browser=("rust",), mine=())
        assert [r.url for r in store.search('"compilers"', tag="rust")] == [A]
        store.set_note(A, "a note")
        store.add_tags(A, ["mine"])
        assert [r.url for r in store.search('"note"', tag="mine")] == [A]
        assert [r.url for r in store.search('"compilers"')] == [A]


def test_export_and_server(store):
    store.add_tags(B, ["garden"])
    store.set_note(B, "Ask about <the> plum\ntree")
    records = store.records()

    html = to_html(records)
    assert "<DD>Ask about &lt;the&gt; plum tree" in html
    assert 'TAGS="garden"' in html
    # The description doesn't leak into titles when the file is read back.
    assert [(b.url, b.title) for b in parse_netscape_html(html)] == [(A, "Alpha"), (B, "Beta")]

    rows = {r["url"]: r for r in json.loads(to_json(records))}
    assert rows[B]["note"] == "Ask about <the> plum\ntree" and rows[A]["note"] == ""
    assert "[Beta](https://b.example/) — Ask about \\<the\\> plum tree" in to_markdown(records)

    result = store.search('"plum"')[0]
    assert _result(result)["note"] == "Ask about <the> plum\ntree"


def run(capsys, *args):
    code = cli.main(list(args))
    out, err = capsys.readouterr()
    return code, out, err


def test_cli_tag(store, capsys):
    db = store.db.execute("PRAGMA database_list").fetchone()[2]
    store.close()

    assert run(capsys, "--db", db, "tag", A) == (0, "from your browser: rust\n", "")
    code, out, _ = run(capsys, "--db", db, "tag", A, "Lang", "later")
    assert (code, out) == (0, "your tags: Lang, later\nfrom your browser: rust\n")

    code, out, err = run(capsys, "--db", db, "tag", A, "--remove", "later", "rust", "nope")
    assert code == 1
    assert out == "your tags: Lang\nfrom your browser: rust\n"
    assert "rust: comes from your browser; remove it there" in err
    assert "nope: you haven't added this tag" in err

    assert run(capsys, "--db", db, "tag", B) == (0, "no tags\n", "")
    with pytest.raises(SystemExit):  # --remove needs at least one tag
        cli.main(["--db", db, "tag", B, "--remove"])
    capsys.readouterr()
    # Adding and removing in one go: the tags to add come first.
    code, out, _ = run(capsys, "--db", db, "tag", A, "new", "--remove", "Lang")
    assert (code, out) == (0, "your tags: new\nfrom your browser: rust\n")
    code, _, err = run(capsys, "--db", db, "tag", "https://missing.example/", "x")
    assert code == 2 and "is not in markfind" in err

    code, out, _ = run(capsys, "--db", db, "tags")
    assert out.split() == ["1", "new", "1", "rust"]  # yours count too; ties by name


def test_cli_note(store, capsys):
    db = store.db.execute("PRAGMA database_list").fetchone()[2]
    store.close()

    code, out, err = run(capsys, "--db", db, "note", A)
    assert (code, out) == (1, "") and "no note" in err
    assert run(capsys, "--db", db, "note", A, "check", "the", "errata") == (0, "", "")
    assert run(capsys, "--db", db, "note", A) == (0, "check the errata\n", "")

    code, out, _ = run(capsys, "--db", db, "search", "errata")
    assert code == 0 and "   note: check the errata\n" in out

    code, _, err = run(capsys, "--db", db, "note", A, "text", "--clear")
    assert code == 2 and "--clear takes no text" in err
    assert run(capsys, "--db", db, "note", A, "--clear") == (0, "", "")
    assert run(capsys, "--db", db, "note", A)[0] == 1
    code, _, err = run(capsys, "--db", db, "note", "https://missing.example/", "x")
    assert code == 2 and "is not in markfind" in err


def test_long_notes_are_shortened_in_search_results(store, capsys):
    store.set_note(A, "word " * 60)
    db = store.db.execute("PRAGMA database_list").fetchone()[2]
    store.close()
    _, out, _ = run(capsys, "--db", db, "search", "compilers")
    line = next(line for line in out.splitlines() if "note:" in line)
    assert line.endswith("word...") and len(line) <= len("   note: ") + cli.NOTE_PREVIEW
