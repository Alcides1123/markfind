import sqlite3

import pytest

from markfind import cli
from markfind.importers import Bookmark
from markfind.store import Store

PAGES = {
    "https://sqlite.org/fts5.html": (
        "SQLite FTS5 Extension",
        "FTS5 is an SQLite virtual table module that provides full-text search. "
        "The tokenizer splits text into tokens, and bm25 ranks the results. "
        "Indexing large documents is fast.",
    ),
    "https://blog.example/django-fts": (
        "Full-text search in Django",
        "Use an FTS5 virtual table and a custom tokenizer; rank results with bm25.",
    ),
    "https://blog.example/postgres-tsvector": (
        "Postgres text search",
        "Postgres indexes documents as tsvector values; a dictionary acts as the tokenizer.",
    ),
    "https://food.example/pasta": (
        "Fresh pasta at home",
        "Knead the dough, rest it, then roll the pasta thin. Cook it in salted water.",
    ),
    "https://food.example/bread": (
        "Sourdough bread",
        "Feed the starter, knead the dough, and bake the bread in a hot oven.",
    ),
    "https://food.example/pizza": (
        "Neapolitan pizza",
        "A pizza dough rests overnight; bake it in the hottest oven you have.",
    ),
}


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "db.sqlite") as store:
        store.add_bookmarks(Bookmark(url, title) for url, (title, _) in PAGES.items())
        for id_, (_, text) in enumerate(PAGES.values(), 1):
            store.set_page(id_, text)
        yield store


def urls(results):
    return [r.url for r in results]


def test_same_topic_first_and_self_excluded(store):
    found = store.similar("https://sqlite.org/fts5.html")
    assert found.title == "SQLite FTS5 Extension"
    assert urls(found.results)[:2] == [
        "https://blog.example/django-fts",
        "https://blog.example/postgres-tsvector",
    ]
    assert "https://sqlite.org/fts5.html" not in urls(found.results)
    assert not any("food.example" in url for url in urls(found.results))
    assert {"fts5", "bm25", "tokenizer"} <= set(found.keywords)

    cooking = store.similar("https://food.example/pasta")
    assert set(urls(cooking.results)) == {
        "https://food.example/bread",
        "https://food.example/pizza",
    }
    assert "dough" in cooking.keywords


def test_keywords_are_stemmed_and_skip_noise(store):
    keywords = store.similar("https://sqlite.org/fts5.html").keywords
    # "Indexing" here and "indexes" in the Postgres page share a stem.
    assert "indexing" in keywords
    # Common words, and words no other page uses, are left out.
    assert "the" not in keywords and "and" not in keywords
    assert "fast" not in keywords
    assert len(keywords) == len(set(keywords))


def test_unfetched_bookmark_uses_its_title(store):
    store.add_bookmarks([Bookmark("https://new.example/", "Baking bread with a sourdough starter")])
    found = store.similar("https://new.example/")
    # "Baking" also finds the pizza page, which says "bake".
    assert urls(found.results) == ["https://food.example/bread", "https://food.example/pizza"]


def test_nothing_in_common(store):
    store.add_bookmarks([Bookmark("https://odd.example/", "Quasar telescopes")])
    found = store.similar("https://odd.example/")
    assert (found.keywords, found.results) == ([], [])


def test_unknown_url(store):
    with pytest.raises(KeyError):
        store.similar("https://missing.example/")


def test_read_only_connection(store, tmp_path):
    store.db.close()
    path = tmp_path / "db.sqlite"
    before = path.read_bytes()
    store.db = sqlite3.connect((path.resolve().as_uri() + "?mode=ro"), uri=True)
    assert store.similar("https://food.example/bread").results
    store.similar("https://food.example/bread")  # temp tables are reused
    assert path.read_bytes() == before


def test_cli(store, tmp_path, capsys):
    store.close()
    db = str(tmp_path / "db.sqlite")

    assert cli.main(["--db", db, "similar", "https://food.example/pasta", "-n", "1"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Similar to: Fresh pasta at home\nbased on: ")
    assert out.count("https://") == 1  # one result with -n 1
    assert "[dough]" in out

    assert cli.main(["--db", db, "similar", "https://missing.example/"]) == 2
    assert "is not in markfind" in capsys.readouterr().err


def test_cli_no_similar(tmp_path, capsys):
    db = str(tmp_path / "db.sqlite")
    with Store(db) as store:
        store.add_bookmarks([Bookmark("https://only.example/", "Lonely page")])
    assert cli.main(["--db", db, "similar", "https://only.example/"]) == 1
    assert capsys.readouterr().out == "Similar to: Lonely page\nno similar bookmarks\n"
