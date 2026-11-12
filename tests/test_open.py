"""`markfind open`: from a search straight into the browser."""

import pytest

from markfind import cli
from markfind.importers import Bookmark
from markfind.store import Store, to_fts_query

PAGES = {
    "https://sqlite.org/fts5.html": (
        "SQLite FTS5 Extension",
        "Dev",
        ("docs",),
        "FTS5 is a virtual table module for full-text search. The tokenizer splits text"
        " into tokens and bm25 ranks the results.",
    ),
    "https://blog.example/django-fts": (
        "Full-text search in Django",
        "Dev",
        (),
        "Use an FTS5 virtual table and a custom tokenizer; rank the results with bm25.",
    ),
    "https://food.example/sourdough": (
        "Sourdough bread",
        "Cooking",
        ("recipes",),
        "Feed the starter, knead the dough, and bake the bread in a hot oven.",
    ),
}


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "db.sqlite"
    with Store(path) as store:
        store.add_bookmarks(
            Bookmark(url, title, folder, tags=tags)
            for url, (title, folder, tags, _) in PAGES.items()
        )
        for id_, (*_, text) in enumerate(PAGES.values(), 1):
            store.set_page(id_, text)
    return str(path)


@pytest.fixture
def opened(monkeypatch):
    """Every address handed to the browser, with the browser itself stubbed out."""
    seen = []
    monkeypatch.setattr("webbrowser.open", lambda url: seen.append(url) or True)
    return seen


def run(db, *args):
    return cli.main(["--db", db, "open", *args])


# --- opening what the search found -------------------------------------------------


def test_opens_the_best_match(db, opened, capsys):
    assert run(db, "sourdough") == 0
    assert opened == ["https://food.example/sourdough"]
    out = capsys.readouterr().out
    assert "opening: Sourdough bread" in out
    assert "https://food.example/sourdough" in out


def test_the_best_match_is_the_one_search_ranks_first(db, opened):
    assert run(db, "tokenizer", "bm25") == 0
    with Store(db, readonly=True) as store:
        first = store.search(to_fts_query("tokenizer bm25"))[0]
    assert opened == [first.url]


def test_says_how_many_other_matches_there_were(db, opened, capsys):
    assert run(db, "tokenizer") == 0
    assert len(opened) == 1  # only the best one is opened
    assert "1 other match; 'markfind search tokenizer' lists them" in capsys.readouterr().out


def test_a_single_match_says_nothing_about_others(db, opened, capsys):
    assert run(db, "sourdough") == 0
    assert "other match" not in capsys.readouterr().out


def test_no_count_is_claimed_when_the_limit_cut_the_matches_off(db, opened, capsys):
    assert run(db, "tokenizer", "--limit", "1") == 0
    out = capsys.readouterr().out
    assert "more matches; 'markfind search tokenizer' lists them" in out
    assert "1 other" not in out  # the search stopped early; the number is unknown


def test_all_says_so_when_it_did_not_open_everything(db, opened, capsys):
    assert run(db, "tokenizer", "--all", "--limit", "1") == 0
    assert len(opened) == 1
    assert "more matches" in capsys.readouterr().out


def test_all_opens_every_match(db, opened, capsys):
    assert run(db, "tokenizer", "--all") == 0
    assert sorted(opened) == [
        "https://blog.example/django-fts",
        "https://sqlite.org/fts5.html",
    ]
    assert "other match" not in capsys.readouterr().out


def test_print_opens_nothing(db, opened, capsys):
    assert run(db, "sourdough", "--print") == 0
    assert opened == []
    assert capsys.readouterr().out.splitlines()[0] == "https://food.example/sourdough"


def test_the_search_filters_work(db, opened):
    assert run(db, "tokenizer", "--folder", "Dev", "--all") == 0
    assert len(opened) == 2
    opened.clear()
    assert run(db, "dough", "--tag", "recipes") == 0
    assert opened == ["https://food.example/sourdough"]
    opened.clear()
    assert run(db, "tokenizer", "--tag", "docs") == 0
    assert opened == ["https://sqlite.org/fts5.html"]


def test_raw_queries_are_passed_through(db, opened):
    assert run(db, "tokenizer OR dough", "--raw", "--all") == 0
    assert len(opened) == 3


# --- opening an address directly ---------------------------------------------------


def test_an_address_markfind_has_is_opened_without_searching(db, opened, capsys):
    assert run(db, "https://food.example/sourdough") == 0
    assert opened == ["https://food.example/sourdough"]
    out = capsys.readouterr().out
    assert "opening: Sourdough bread" in out
    assert "other match" not in out  # nothing was searched for


def test_an_address_markfind_does_not_have(db, opened, capsys):
    assert run(db, "https://nowhere.example/page") == 2
    assert opened == []
    assert "markfind add" in capsys.readouterr().err


# --- nothing to open ----------------------------------------------------------------


def test_no_matches(db, opened, capsys):
    assert run(db, "helicopter") == 1
    assert opened == []
    assert "no matches" in capsys.readouterr().out


def test_an_invalid_query(db, opened, capsys):
    assert run(db, "AND OR", "--raw") == 2
    assert opened == []
    assert "invalid query" in capsys.readouterr().err


def test_no_browser_to_open_it_with(db, monkeypatch, capsys):
    monkeypatch.setattr("webbrowser.open", lambda url: False)
    assert run(db, "sourdough") == 1
    assert "no browser" in capsys.readouterr().err


# --- what is never handed to a browser ----------------------------------------------


@pytest.mark.parametrize(
    "url",
    ["javascript:alert(1)", "file:///C:/Windows/win.ini", "data:text/html,<script>x</script>"],
)
def test_only_web_addresses_are_opened(tmp_path, url, opened, capsys):
    # markfind's importers keep web addresses only, but a database written by
    # something else could hold anything.
    path = tmp_path / "odd.sqlite"
    with Store(path) as store:
        store.add_bookmarks([Bookmark(url, "Payload page")])
    assert cli.main(["--db", str(path), "open", "payload"]) == 1
    assert opened == []
    assert "not a web address" in capsys.readouterr().err
