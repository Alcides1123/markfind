"""`markfind remove`: taking bookmarks out by hand."""

import io
import sqlite3

import pytest

from markfind import cli
from markfind.importers import Bookmark
from markfind.store import Store

BOOKMARKS = [
    Bookmark("https://a.example/one", "Page one", "Old/Dev", tags=("stale",)),
    Bookmark("https://a.example/two", "Page two", "Old/Dev"),
    Bookmark("https://b.example/three", "Page three", "Old", tags=("stale",)),
    Bookmark("https://c.example/keep", "Keep me", "Keep"),
]


class Terminal(io.StringIO):
    def isatty(self):
        return True


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "db.sqlite"
    with Store(path) as store:
        store.add_bookmarks(BOOKMARKS)
        # One of them has been fetched, so it has page text and a saved copy.
        store.set_page(1, "sourdough bread and a hot oven", "Page one", "<p>sourdough</p>")
    return str(path)


def answer(monkeypatch, text):
    prompts = []
    monkeypatch.setattr("sys.stdin", Terminal())
    monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or text)
    return prompts


def never_asks(monkeypatch):
    monkeypatch.setattr("sys.stdin", Terminal())
    monkeypatch.setattr("builtins.input", lambda prompt: pytest.fail(f"asked: {prompt}"))


def remove(db, *args):
    return cli.main(["--db", db, "remove", *args])


def left(db):
    with Store(db, readonly=True) as store:
        return sorted(url for url, _, _ in store.list_bookmarks())


# --- the addresses you name --------------------------------------------------------


def test_removes_one_bookmark(db, monkeypatch, capsys):
    never_asks(monkeypatch)  # naming it is confirmation enough
    assert remove(db, "https://a.example/two") == 0
    assert left(db) == [
        "https://a.example/one",
        "https://b.example/three",
        "https://c.example/keep",
    ]
    out = capsys.readouterr().out
    assert "Page two" in out and "removed 1 bookmark from markfind" in out
    assert "Your browser still has them" in out  # or the next sync brings it back


def test_removes_several(db, monkeypatch, capsys):
    never_asks(monkeypatch)
    assert remove(db, "https://a.example/one", "https://b.example/three") == 0
    assert left(db) == ["https://a.example/two", "https://c.example/keep"]
    assert "removed 2 bookmarks from markfind" in capsys.readouterr().out


def test_surrounding_space_is_ignored(db, monkeypatch):
    never_asks(monkeypatch)
    assert remove(db, "  https://a.example/two  ") == 0
    assert "https://a.example/two" not in left(db)


def test_an_unknown_address_removes_nothing(db, capsys):
    assert remove(db, "https://a.example/two", "https://nowhere.example/x") == 2
    assert len(left(db)) == 4  # not even the address that was fine
    assert "not in markfind" in capsys.readouterr().err


def test_the_index_forgets_it_too(db, monkeypatch):
    never_asks(monkeypatch)
    with Store(db, readonly=True) as store:
        assert store.search("sourdough")
    assert remove(db, "https://a.example/one") == 0
    with Store(db, readonly=True) as store:
        assert store.search("sourdough") == []
        with pytest.raises(KeyError):  # the bookmark itself is gone
            store.saved_copy("https://a.example/one")
    with sqlite3.connect(db) as raw:
        assert raw.execute("SELECT count(*) FROM archive").fetchone()[0] == 0


# --- what a filter matches ---------------------------------------------------------


def test_a_folder_and_its_subfolders_after_yes(db, monkeypatch, capsys):
    prompts = answer(monkeypatch, "y")
    assert remove(db, "--folder", "Old") == 0
    assert left(db) == ["https://c.example/keep"]
    assert prompts == ["Remove these 3 bookmarks from markfind? [y/N] "]
    out = capsys.readouterr().out
    assert "Page one" in out and "Page three" in out  # listed before the question
    assert "Removing a bookmark removes its saved copy too." in out


def test_a_subfolder_only(db, monkeypatch):
    answer(monkeypatch, "y")
    assert remove(db, "--folder", "Old/Dev") == 0
    assert left(db) == ["https://b.example/three", "https://c.example/keep"]


def test_one_match_is_asked_about_as_one(db, monkeypatch):
    prompts = answer(monkeypatch, "y")
    assert remove(db, "--folder", "Keep") == 0
    assert prompts == ["Remove this bookmark from markfind? [y/N] "]


def test_declining_removes_nothing(db, monkeypatch, capsys):
    answer(monkeypatch, "")
    assert remove(db, "--folder", "Old") == 0
    assert len(left(db)) == 4
    assert "nothing removed" in capsys.readouterr().out


def test_without_a_terminal_it_needs_yes(db, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("y\n"))  # piped input isn't a terminal
    assert remove(db, "--folder", "Old") == 1
    assert len(left(db)) == 4
    assert "--yes" in capsys.readouterr().err


def test_yes_does_not_ask(db, monkeypatch):
    never_asks(monkeypatch)
    assert remove(db, "--folder", "Old", "--yes") == 0
    assert left(db) == ["https://c.example/keep"]


def test_a_tag_ignoring_case(db, monkeypatch):
    answer(monkeypatch, "y")
    assert remove(db, "--tag", "STALE") == 0
    assert left(db) == ["https://a.example/two", "https://c.example/keep"]


def test_a_folder_and_a_tag_together(db, monkeypatch):
    answer(monkeypatch, "y")
    assert remove(db, "--folder", "Old/Dev", "--tag", "stale") == 0
    assert left(db) == [
        "https://a.example/two",
        "https://b.example/three",
        "https://c.example/keep",
    ]


def test_a_filter_that_matches_nothing(db, capsys):
    assert remove(db, "--tag", "nosuchtag", "--yes") == 1
    assert len(left(db)) == 4
    assert "no bookmarks match" in capsys.readouterr().out


# --- asking for something that can't be meant --------------------------------------


@pytest.mark.parametrize("empty", ["", "   "])
def test_an_empty_filter_is_refused(db, empty, capsys):
    for option in ("--folder", "--tag"):
        assert remove(db, option, empty, "--yes") == 2
        assert "would match every bookmark" in capsys.readouterr().err
    assert len(left(db)) == 4


def test_addresses_and_a_filter_together(db, capsys):
    assert remove(db, "https://a.example/one", "--tag", "stale", "--yes") == 2
    assert len(left(db)) == 4
    assert "not both" in capsys.readouterr().err


def test_nothing_to_remove(db, capsys):
    assert remove(db) == 2
    assert len(left(db)) == 4
    assert "name an address" in capsys.readouterr().err
