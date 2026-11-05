from datetime import timedelta

import pytest

from markfind.importers import Bookmark
from markfind.store import QueryError, Store, to_fts_query


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "db.sqlite") as s:
        yield s


def test_add_and_update(store):
    assert store.add_bookmarks([Bookmark("https://a.example/", "A", "x")]) == (1, 0)
    assert store.add_bookmarks(
        [Bookmark("https://a.example/", "A2", "y"), Bookmark("https://b.example/")]
    ) == (1, 1)
    assert store.list_bookmarks() == [
        ("https://b.example/", "", ""),
        ("https://a.example/", "A2", "y"),
    ]


def test_search_title_and_body(store):
    store.add_bookmarks(
        [
            Bookmark("https://a.example/", "Gardening tips", "Home"),
            Bookmark("https://b.example/", "Untitled", "Dev"),
        ]
    )
    ((b_id, _),) = [p for p in store.pending() if p[1] == "https://b.example/"]
    store.set_page(b_id, "How to tune SQLite full-text indexes for speed")

    assert [r.url for r in store.search("gardening")] == ["https://a.example/"]

    (hit,) = store.search("indexes", mark=("<", ">"))
    assert hit.url == "https://b.example/"
    assert "<indexes>" in hit.snippet

    # porter stemming: "index" matches "indexes"
    assert [r.url for r in store.search("index")] == ["https://b.example/"]


def test_reimport_keeps_page_text(store):
    store.add_bookmarks([Bookmark("https://a.example/", "A")])
    ((id_, _),) = store.pending()
    store.set_page(id_, "kangaroo facts")
    store.add_bookmarks([Bookmark("https://a.example/", "A renamed")])

    (hit,) = store.search("kangaroo")
    assert hit.title == "A renamed"
    assert store.pending() == []
    assert store.pending(refetch=True) == [(id_, "https://a.example/")]


def test_pending_older_than_and_failed(store):
    store.add_bookmarks([Bookmark(f"https://{n}.example/") for n in "abcd"])
    (a, _), (b, _), (c, _), (d, _) = store.pending()
    store.set_page(a, "fresh")
    store.set_page(b, "stale")
    store.set_error(c, "HTTPError: 500")
    store.db.execute(
        "UPDATE bookmarks SET fetched_at = '2020-01-01T00:00:00+00:00' WHERE id = ?", (b,)
    )

    def ids(**kwargs):
        return [id_ for id_, _ in store.pending(**kwargs)]

    assert ids() == [d]
    assert ids(older_than=timedelta(days=30)) == [b, d]
    assert ids(failed=True) == [c, d]
    assert ids(older_than=timedelta(days=30), failed=True) == [b, c, d]
    assert ids(refetch=True) == [a, b, c, d]


def test_page_title_used_only_when_bookmark_has_none(store):
    store.add_bookmarks([Bookmark("https://a.example/"), Bookmark("https://b.example/", "Mine")])
    for id_, _ in store.pending():
        store.set_page(id_, "", page_title="From page")
    assert {url: title for url, title, _ in store.list_bookmarks()} == {
        "https://a.example/": "From page",
        "https://b.example/": "Mine",
    }


def test_errors_and_stats(store):
    store.add_bookmarks([Bookmark(f"https://{n}.example/") for n in "abc"])
    (a, _), (b, _), _ = store.pending()
    store.set_page(a, "text")
    store.set_error(b, "HTTPError: 404")
    s = store.stats()
    assert (s.total, s.fetched, s.failed) == (3, 1, 1)


def test_list_folder_prefix(store):
    store.add_bookmarks(
        [
            Bookmark("https://1.example/", folder="Dev"),
            Bookmark("https://2.example/", folder="Dev/Python"),
            Bookmark("https://3.example/", folder="Devices"),
        ]
    )
    assert [r[0] for r in store.list_bookmarks("Dev")] == [
        "https://1.example/",
        "https://2.example/",
    ]


def test_query_quoting(store):
    store.add_bookmarks([Bookmark("https://a.example/", "full-text search: a primer")])
    assert to_fts_query('full-text "x"') == '"full-text" """x"""'
    assert len(store.search(to_fts_query("full-text primer"))) == 1
    with pytest.raises(QueryError):
        store.search("full-text AND")


@pytest.mark.parametrize(
    "query", ['"unbalanced', '"', "*", "a AND", "(x", "x)", "nosuch:x", "-x", "x OR OR y"]
)
def test_every_kind_of_bad_query_is_a_query_error(store, query):
    # Each is a different SQLite message; all must reach the user as
    # "invalid query", not as a crash.
    store.add_bookmarks([Bookmark("https://a.example/", "x y")])
    with pytest.raises(QueryError):
        store.search(query)
