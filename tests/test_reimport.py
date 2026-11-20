"""Importing the same file again should change nothing, and say so.

``add_bookmarks`` used to write every bookmark the file and the database had in
common, count it as updated, and delete and reinsert its FTS row. So the second
and every later ``markfind sync`` reported every bookmark as updated, and the
index grew by megabytes each time. These tests pin down what an update is.
"""

import sqlite3

import pytest

from markfind import cli
from markfind.importers import Bookmark
from markfind.store import Store

URL = "https://a.example/page"


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "db.sqlite") as store:
        yield store


def imported(store, *, title="Guide", folder="Dev", tags=None, source="export.html"):
    return store.add_bookmarks([Bookmark(URL, title, folder, tags=tags)], source=source)


# --- what is and isn't an update ---------------------------------------------------


def test_the_first_import_adds(store):
    assert imported(store) == (1, 0)


def test_the_same_file_again_changes_nothing(store):
    imported(store)
    assert imported(store) == (0, 0)
    assert imported(store) == (0, 0)


@pytest.mark.parametrize(
    "change",
    [
        pytest.param({"title": "A New Title"}, id="title"),
        pytest.param({"folder": "Dev/Python"}, id="folder"),
        pytest.param({"source": "other.html"}, id="source"),
        pytest.param({"tags": ("python",)}, id="tags"),
    ],
)
def test_one_changed_field_is_one_update(store, change):
    fields = {"tags": (), **change}
    imported(store, tags=())
    assert imported(store, **fields) == (0, 1)
    # ...and then it has changed, so importing it again doesn't.
    assert imported(store, **fields) == (0, 0)


def test_a_source_with_no_name_for_it_does_not_erase_a_fetched_title(store):
    """A browser bookmark with no name of its own; see #95."""
    imported(store, title="")
    (id_,) = (i for i, _ in store.pending())
    store.set_page(id_, "sourdough starter instructions", page_title="Sourdough Starter")
    assert store.bookmark(URL).title == "Sourdough Starter"

    assert imported(store, title="") == (0, 0)
    assert store.bookmark(URL).title == "Sourdough Starter"
    assert [r.title for r in store.search("sourdough")] == ["Sourdough Starter"]


def test_a_name_the_browser_does_have_still_wins(store):
    """Only silence is kept out; a title the source states is applied as before."""
    imported(store, title="")
    (id_,) = (i for i, _ in store.pending())
    store.set_page(id_, "the page text", page_title="The Page's Own Title")
    assert imported(store, title="Renamed in the Browser") == (0, 1)
    assert store.bookmark(URL).title == "Renamed in the Browser"


def test_a_source_that_cannot_say_leaves_tags_and_source_alone(store):
    """tags=None means "this browser has no tags", not "it has none"."""
    imported(store, tags=("python",))
    assert store.add_bookmarks([Bookmark(URL, "Guide", "Dev", tags=None)], source=None) == (0, 0)
    assert store.bookmark(URL).tags == ("python",)


def test_dropping_a_browser_tag_is_an_update(store):
    imported(store, tags=("python", "search"))
    assert imported(store, tags=("python",)) == (0, 1)
    assert store.bookmark(URL).tags == ("python",)


def test_your_own_tags_are_not_disturbed_and_not_an_update(store):
    imported(store, tags=("python",))
    store.add_tags(URL, ["mine"])
    assert imported(store, tags=("python",)) == (0, 0)
    assert set(store.bookmark(URL).tags) == {"python", "mine"}


def test_a_tag_in_other_capitals_is_not_a_change(store):
    """clean_tags keeps the first spelling, so the stored value is unchanged."""
    imported(store, tags=("Python",))
    assert imported(store, tags=("Python",)) == (0, 0)


# --- the index, which is what the needless writes cost ------------------------------


def fts_rows(path):
    db = sqlite3.connect(path)
    try:
        return db.execute("SELECT count(*) FROM bookmarks_fts").fetchone()[0]
    finally:
        db.close()


def test_an_unchanged_import_leaves_the_index_alone(tmp_path):
    """Rewriting an FTS row costs real index space, so an unchanged row keeps its own."""
    path = tmp_path / "db.sqlite"
    with Store(path) as store:
        store.add_bookmarks([Bookmark(URL, "Guide", "Dev")], source="export.html")
        (id_,) = (i for i, _ in store.pending())
        store.set_page(id_, " ".join(f"word{n}" for n in range(400)))

    before = path.stat().st_size
    with Store(path) as store:
        for _ in range(5):
            assert store.add_bookmarks([Bookmark(URL, "Guide", "Dev")], source="export.html") == (
                0,
                0,
            )
    assert path.stat().st_size == before
    assert fts_rows(path) == 1


def test_the_page_text_survives_a_re_import(tmp_path):
    """Skipping the rewrite must not cost the body, which only the FTS row holds."""
    path = tmp_path / "db.sqlite"
    with Store(path) as store:
        store.add_bookmarks([Bookmark(URL, "Guide", "Dev")], source="export.html")
        (id_,) = (i for i, _ in store.pending())
        store.set_page(id_, "sourdough starter instructions")
    with Store(path) as store:
        store.add_bookmarks([Bookmark(URL, "Guide", "Dev")], source="export.html")
        assert [r.url for r in store.search("sourdough")] == [URL]


def test_a_changed_title_reaches_the_index(tmp_path):
    """And an update still has to be indexed, or search would miss it."""
    path = tmp_path / "db.sqlite"
    with Store(path) as store:
        store.add_bookmarks([Bookmark(URL, "Old Title", "Dev")], source="export.html")
        (id_,) = (i for i, _ in store.pending())
        store.set_page(id_, "the page text")
        assert store.add_bookmarks([Bookmark(URL, "Sourdough Guide", "Dev")], source="x.html") == (
            0,
            1,
        )
        assert [r.url for r in store.search("sourdough")] == [URL]
        assert store.search("Old Title") == []


# --- through the command line ------------------------------------------------------


def netscape(path, title="Guide"):
    path.write_text(
        "<!DOCTYPE NETSCAPE-Bookmark-file-1>\n<DL><p>\n"
        f'<DT><H3>Dev</H3>\n<DL><p>\n<DT><A HREF="{URL}">{title}</A>\n</DL><p>\n</DL><p>\n',
        encoding="utf-8",
    )
    return path


def test_cli_import_says_what_it_did(tmp_path, capsys):
    db = tmp_path / "db.sqlite"
    marks = netscape(tmp_path / "marks.html")

    assert cli.main(["--db", str(db), "import", str(marks)]) == 0
    assert "1 added, 0 updated" in capsys.readouterr().out

    assert cli.main(["--db", str(db), "import", str(marks)]) == 0
    assert "0 added, 0 updated" in capsys.readouterr().out

    netscape(marks, title="A New Title")
    assert cli.main(["--db", str(db), "import", str(marks)]) == 0
    assert "0 added, 1 updated" in capsys.readouterr().out


def listing(path, *urls):
    links = "".join(f'<DT><A HREF="{u}">Guide</A>\n' for u in urls)
    path.write_text(
        "<!DOCTYPE NETSCAPE-Bookmark-file-1>\n<DL><p>\n"
        f"<DT><H3>Dev</H3>\n<DL><p>\n{links}</DL><p>\n</DL><p>\n",
        encoding="utf-8",
    )
    return path


def test_cli_a_second_import_keeps_a_fetched_title(tmp_path, capsys, monkeypatch):
    """The whole path from #95: an unnamed bookmark, fetched, then imported again."""
    from markfind.extract import Page

    db = tmp_path / "db.sqlite"
    # A link whose text is its own address has no title, so this is a bookmark
    # the browser has no name for.
    marks = tmp_path / "marks.html"
    marks.write_text(
        "<!DOCTYPE NETSCAPE-Bookmark-file-1>\n<DL><p>\n"
        f'<DT><H3>Dev</H3>\n<DL><p>\n<DT><A HREF="{URL}">{URL}</A>\n</DL><p>\n</DL><p>\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        cli, "fetch_page", lambda url, timeout=10.0: Page(title="Sourdough Starter", text="starter")
    )

    assert cli.main(["--db", str(db), "import", str(marks)]) == 0
    assert cli.main(["--db", str(db), "fetch", "--delay", "0"]) == 0
    capsys.readouterr()
    with Store(db, readonly=True) as store:
        assert store.bookmark(URL).title == "Sourdough Starter"

    assert cli.main(["--db", str(db), "import", str(marks)]) == 0
    assert "0 added, 0 updated" in capsys.readouterr().out
    with Store(db, readonly=True) as store:
        assert store.bookmark(URL).title == "Sourdough Starter"


def test_cli_prune_still_knows_where_a_bookmark_came_from(tmp_path, capsys):
    """The skipped write must not lose `source`, which --prune needs."""
    db = tmp_path / "db.sqlite"
    other = "https://b.example/page"
    marks = listing(tmp_path / "marks.html", URL, other)
    assert cli.main(["--db", str(db), "import", str(marks)]) == 0
    assert "2 added, 0 updated" in capsys.readouterr().out
    # The import that writes nothing is the one whose `source` has to survive.
    assert cli.main(["--db", str(db), "import", str(marks)]) == 0
    assert "0 added, 0 updated" in capsys.readouterr().out

    listing(marks, other)  # URL is no longer in the file
    assert cli.main(["--db", str(db), "import", str(marks), "--prune"]) == 0
    assert "1 removed" in capsys.readouterr().out
    with Store(db, readonly=True) as store:
        assert [r.url for r in store.records()] == [other]
