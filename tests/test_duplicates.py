import pytest

from markfind import cli
from markfind.duplicates import canonical_url, find_duplicates, text_key
from markfind.importers import Bookmark
from markfind.store import Store

ARTICLE = " ".join(["A long article about migrating birds and where they go."] * 6)


@pytest.mark.parametrize(
    "a, b",
    [
        ("http://example.com/page", "https://example.com/page"),
        ("https://www.example.com/page", "https://example.com/page"),
        ("https://EXAMPLE.com./page", "https://example.com/page"),
        ("https://example.com:443/page", "https://example.com/page"),
        ("http://example.com:80/page", "https://example.com/page"),
        ("https://example.com/page/", "https://example.com/page"),
        ("https://example.com/", "https://example.com"),
        ("https://example.com/docs/index.html", "https://example.com/docs/"),
        ("https://example.com/INDEX.PHP", "https://example.com"),
        ("https://example.com/page#section-2", "https://example.com/page"),
        ("https://example.com/page?utm_source=x&utm_medium=y", "https://example.com/page"),
        ("https://example.com/page?fbclid=abc", "https://example.com/page"),
        ("https://example.com/p?id=1&UTM_Campaign=z&gclid=q", "https://example.com/p?id=1"),
        ("https://example.com/p?b=2&a=1", "https://example.com/p?a=1&b=2"),
        ("https://example.com/p?q=a%20b", "https://example.com/p?q=a+b"),
    ],
)
def test_same_page(a, b):
    assert canonical_url(a) == canonical_url(b)


@pytest.mark.parametrize(
    "a, b",
    [
        ("https://example.com/Page", "https://example.com/page"),  # paths are case-sensitive
        ("https://example.com/p?id=1", "https://example.com/p?id=2"),
        ("https://example.com/p?ref=main", "https://example.com/p"),  # GitHub uses ?ref=
        ("https://example.com:8080/", "https://example.com/"),
        ("https://app.example.com/#/inbox", "https://app.example.com/#/settings"),
        ("https://app.example.com/#!/inbox", "https://app.example.com/"),
        ("https://blog.example.com/", "https://example.com/"),
        ("https://example.com/a/b", "https://example.com/a"),
    ],
)
def test_different_pages(a, b):
    assert canonical_url(a) != canonical_url(b)


def test_text_key():
    assert text_key("too short") is None
    assert text_key(ARTICLE) == text_key("  " + ARTICLE.upper().replace(" ", "\n  "))
    assert text_key(ARTICLE) != text_key(ARTICLE + " Extra.")


def page(url, title="", folder="", text=""):
    return (url, title, folder, text)


def test_groups_and_preferred_url():
    groups = find_duplicates(
        [
            page("http://www.sqlite.org/fts5.html?utm_source=x", "", "Reading"),
            page("https://sqlite.org/fts5.html", "SQLite FTS5", "Dev"),
            page("http://sqlite.org/fts5.html"),
            page("https://blog.example/post?id=42", "My post", "Old", ARTICLE),
            page("https://blog.example/2021/05/my-post", "My post", "Blog", ARTICLE),
            page("https://unique.example/", "Unique", "", ARTICLE + " Different."),
        ],
        by=("url", "text"),  # "Unique" is a near duplicate; see test_near_duplicates.py
    )
    assert [(g.reason, g.title, [e.url for e in g.entries]) for g in groups] == [
        (
            "url",
            "SQLite FTS5",
            [
                "https://sqlite.org/fts5.html",
                "http://sqlite.org/fts5.html",
                "http://www.sqlite.org/fts5.html?utm_source=x",
            ],
        ),
        (
            "text",
            "My post",
            ["https://blog.example/post?id=42", "https://blog.example/2021/05/my-post"],
        ),
    ]
    assert groups[0].entries[0].folder == "Dev"


def test_same_url_and_text_is_reported_once():
    rows = [page("https://a.example/x", text=ARTICLE), page("http://a.example/x/", text=ARTICLE)]
    assert [g.reason for g in find_duplicates(rows)] == ["url"]
    assert [g.reason for g in find_duplicates(rows, by=("text",))] == ["text"]
    assert find_duplicates(rows, by=("url",))[0].reason == "url"


def test_short_pages_never_match_by_text():
    rows = [
        page("https://a.example/", text="Please log in"),
        page("https://b.example/", text="Please log in"),
    ]
    assert find_duplicates(rows) == []
    assert len(find_duplicates(rows, min_words=2)) == 1


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "db.sqlite"
    with Store(path) as store:
        store.add_bookmarks(
            [
                Bookmark("https://sqlite.org/fts5.html", "SQLite FTS5", "Dev"),
                Bookmark("http://www.sqlite.org/fts5.html#overview", "FTS5", "Reading"),
                Bookmark("https://blog.example/post?id=42", "My post"),
                Bookmark("https://blog.example/2021/05/my-post", "My post", "Blog"),
                Bookmark("https://blog.example/unfetched", "Not fetched"),
            ]
        )
        store.set_page(3, ARTICLE)
        store.set_page(4, ARTICLE)
    return str(path)


def test_cli(db, capsys):
    assert cli.main(["--db", db, "duplicates"]) == 0
    out = capsys.readouterr().out
    assert "Same page, different URLs (1):\n  SQLite FTS5\n  * https://sqlite.org/fts5.html" in out
    assert "    http://www.sqlite.org/fts5.html#overview  folder: Reading" in out
    assert "\nSame page text (1):\n  My post\n  * https://blog.example/post?id=42" in out
    assert "2 extra copies" in out

    assert cli.main(["--db", db, "duplicates", "--urls"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "http://www.sqlite.org/fts5.html#overview",
        "https://blog.example/2021/05/my-post",
    ]

    assert cli.main(["--db", db, "duplicates", "--by", "text"]) == 0
    out = capsys.readouterr().out
    assert "Same page text (1):" in out and "different URLs" not in out


def test_cli_none(tmp_path, capsys):
    db = str(tmp_path / "db.sqlite")
    assert cli.main(["--db", db, "duplicates"]) == 0
    assert capsys.readouterr().out == "no duplicates found\n"
    assert cli.main(["--db", db, "duplicates", "--urls"]) == 0
    assert capsys.readouterr().out == ""
