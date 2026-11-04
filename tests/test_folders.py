import pytest

from markfind import cli
from markfind.importers import Bookmark
from markfind.store import Store

BOOKMARKS = [
    ("https://a.example/", "Bar/Dev"),
    ("https://b.example/", "Bar/Dev/Python"),
    ("https://c.example/", "Bar/Dev/Python"),
    ("https://d.example/", "Bar/Dev2"),  # shares a prefix with Bar/Dev, but isn't inside it
    ("https://e.example/", "Bar B"),  # sorts between "Bar" and "Bar/..." as plain text
    ("https://f.example/", "Deals/100%_off"),  # LIKE wildcards in the name
    ("https://g.example/", "Deals/100xxoff"),
    ("https://h.example/", ""),  # not in any folder
]


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "db.sqlite") as store:
        store.add_bookmarks(Bookmark(url, "Guide", folder) for url, folder in BOOKMARKS)
        for id_, _ in store.pending():
            store.set_page(id_, "a guide to indexing")
        yield store


def test_folders_tree_with_counts(store):
    assert store.folders() == [
        ("Bar", 4),
        ("Bar/Dev", 3),
        ("Bar/Dev/Python", 2),
        ("Bar/Dev2", 1),
        ("Bar B", 1),
        ("Deals", 2),
        ("Deals/100%_off", 1),
        ("Deals/100xxoff", 1),
    ]


@pytest.mark.parametrize(
    "folder, expected",
    [
        (None, "abcdefgh"),
        ("", "abcdefgh"),
        ("Bar", "abcd"),
        ("Bar/Dev", "abc"),
        ("Bar/Dev/Python", "bc"),
        ("Bar B", "e"),
        ("Deals/100%_off", "f"),  # % and _ match only themselves
        ("Nowhere", ""),
    ],
)
def test_search_in_folder(store, folder, expected):
    results = store.search('"guide"', limit=50, folder=folder)
    assert "".join(sorted(r.url[8] for r in results)) == expected


def test_search_in_folder_combines_with_exclude(store):
    results = store.search('"guide"', folder="Bar/Dev", exclude=1)
    assert sorted(r.url for r in results) == ["https://b.example/", "https://c.example/"]


def test_cli_search_folder(store, tmp_path, capsys):
    store.close()
    db = str(tmp_path / "db.sqlite")
    assert cli.main(["--db", db, "search", "guide", "--folder", "Bar/Dev/Python"]) == 0
    out = capsys.readouterr().out
    assert "https://b.example/" in out and "https://c.example/" in out
    assert "https://a.example/" not in out
    assert cli.main(["--db", db, "search", "guide", "--folder", "Nowhere"]) == 1
    assert "no matches" in capsys.readouterr().out
