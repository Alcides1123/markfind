"""A link whose text is its own address has no title.

``markfind export`` writes the address as a link's text when a bookmark has no
title, because a browser needs something clickable. Reading that back as a
title would invent one, and markfind would then show a title for a page it has
never opened -- permanently, for a link that can no longer be fetched.

So the rule belongs to every importer, and restoring markfind's own export has
to leave the database exactly as it was.
"""

import json
import plistlib
import sqlite3
from pathlib import Path

import pytest

from markfind.formats import to_html
from markfind.importers import (
    Bookmark,
    parse_chrome_json,
    parse_firefox_places,
    parse_netscape_html,
    parse_safari_plist,
)
from markfind.store import Record, Store

URL = "https://a.example/python-tutorial"


# --- the rule, through each importer in turn ---------------------------------------


def netscape(url, text):
    return parse_netscape_html(
        f'<!DOCTYPE NETSCAPE-Bookmark-file-1>\n<DL><p>\n<DT><A HREF="{url}">{text}</A>\n</DL><p>\n'
    )


def chrome(url, name):
    return parse_chrome_json(
        json.dumps(
            {
                "roots": {
                    "bookmark_bar": {
                        "type": "folder",
                        "name": "Bookmarks bar",
                        "children": [{"type": "url", "name": name, "url": url}],
                    }
                }
            }
        )
    )


FIREFOX_SCHEMA = """
CREATE TABLE moz_places (id INTEGER PRIMARY KEY, url LONGVARCHAR, title LONGVARCHAR);
CREATE TABLE moz_bookmarks (
    id INTEGER PRIMARY KEY, type INTEGER, fk INTEGER DEFAULT NULL, parent INTEGER,
    position INTEGER, title LONGVARCHAR, dateAdded INTEGER, guid TEXT
);
"""


def firefox(tmp_path, url, title):
    path = tmp_path / "places.sqlite"
    db = sqlite3.connect(path)
    db.executescript(FIREFOX_SCHEMA)
    db.execute("INSERT INTO moz_places (id, url, title) VALUES (1, ?, ?)", (url, title))
    db.executemany(
        "INSERT INTO moz_bookmarks (id, type, fk, parent, position, title, dateAdded, guid)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (1, 2, None, 0, 0, "", 0, "root________"),
            (2, 2, None, 1, 0, "toolbar", 0, "toolbar_____"),
            (3, 1, 1, 2, 0, None, 0, "bookmarkAAAA"),
        ],
    )
    db.commit()
    db.close()
    return parse_firefox_places(path)


def safari(url, title):
    return parse_safari_plist(
        plistlib.dumps(
            {
                "WebBookmarkFileVersion": 1,
                "WebBookmarkType": "WebBookmarkTypeList",
                "Title": "",
                "Children": [
                    {
                        "WebBookmarkType": "WebBookmarkTypeList",
                        "Title": "BookmarksBar",
                        "Children": [
                            {
                                "WebBookmarkType": "WebBookmarkTypeLeaf",
                                "URLString": url,
                                "URIDictionary": {"title": title},
                            }
                        ],
                    }
                ],
            }
        )
    )


@pytest.fixture(params=["netscape html", "chrome json", "firefox places", "safari plist"])
def importer(request, tmp_path):
    """Each importer as a function of (url, title) -> list[Bookmark]."""
    return {
        "netscape html": lambda url, title: netscape(url, title),
        "chrome json": lambda url, title: chrome(url, title),
        "firefox places": lambda url, title: firefox(tmp_path, url, title),
        "safari plist": lambda url, title: safari(url, title),
    }[request.param]


def test_a_title_that_is_the_address_is_no_title(importer):
    (bookmark,) = importer(URL, URL)
    assert bookmark.url == URL
    assert bookmark.title == ""


def test_a_real_title_is_kept(importer):
    (bookmark,) = importer(URL, "Python Tutorial")
    assert bookmark.title == "Python Tutorial"


def test_surrounding_space_does_not_hide_the_address(importer):
    (bookmark,) = importer(URL, f"  {URL}  ")
    assert bookmark.title == ""


@pytest.mark.parametrize(
    "title",
    [
        "https://a.example/python-tutorial and more",  # the address plus something
        "https://a.example/",  # a different address
        "https://a.example/python-tutorial/",  # one character longer
        "a.example/python-tutorial",  # the address without its scheme
    ],
)
def test_only_the_whole_address_counts(importer, title):
    """Anything that isn't exactly the address is a title someone chose."""
    (bookmark,) = importer(URL, title)
    assert bookmark.title == title


def test_no_importer_builds_a_title_without_the_rule():
    """A fifth importer, or a change to one of the four, must not go around _title."""
    source = (Path(__file__).parents[1] / "src/markfind/importers.py").read_text(encoding="utf-8")
    assert "title=_clean(" not in source, "build titles with _title(text, url), not _clean(text)"
    assert source.count("title=_title(") == 4


# --- what the rule is for: a restore that changes nothing --------------------------

RECORDS = [
    Record(URL, "", "Dev", None, None, None, tags=("later",), note=""),
    Record("https://sqlite.org/fts5.html", "SQLite FTS5", "Dev/Docs", None, None, None),
    Record("https://news.example/", "", "", None, None, None),
    Record("https://notes.example/", "Notes", "Reading", None, None, None, note="worth keeping"),
]


def restore(tmp_path, records):
    """Export these records, import the file, and report what the database holds."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "backup.html").write_text(to_html(records), encoding="utf-8")
    path = tmp_path / "restored.sqlite"
    with Store(path) as store:
        store.add_bookmarks(parse_netscape_html((tmp_path / "backup.html").read_text("utf-8")))
    with Store(path, readonly=True) as store:
        return {r.url: (r.title, r.folder, r.tags) for r in store.records()}


def test_restoring_an_export_keeps_every_title(tmp_path):
    got = restore(tmp_path, RECORDS)
    assert got == {
        URL: ("", "Dev", ("later",)),
        "https://sqlite.org/fts5.html": ("SQLite FTS5", "Dev/Docs", ()),
        "https://news.example/": ("", "", ()),
        "https://notes.example/": ("Notes", "Reading", ()),
    }


def test_an_untitled_bookmark_is_still_untitled_after_two_trips(tmp_path):
    """Exporting what came back must not drift either."""
    first = restore(tmp_path, RECORDS)
    again = [
        Record(url, title, folder, None, None, None, tags=tags)
        for url, (title, folder, tags) in first.items()
    ]
    assert restore(tmp_path / "second", again) == first


def test_the_export_still_gives_a_browser_something_to_click(tmp_path):
    """The address stays the link text, which is why the importer has to undo it."""
    html = to_html([Record(URL, "", "Dev", None, None, None)])
    assert f'<A HREF="{URL}">{URL}</A>' in html


def test_reimporting_leaves_a_fetched_title_alone(tmp_path):
    """A page markfind read keeps the title it read, across a backup."""
    path = tmp_path / "db.sqlite"
    with Store(path) as store:
        store.add_bookmarks([Bookmark(URL, "", "Dev")])
        (id_,) = (i for i, _ in store.pending())
        store.set_page(id_, "a page about tutorials", page_title="The Real Title")
        records = store.records()
    assert records[0].title == "The Real Title"

    backup = tmp_path / "backup.html"
    backup.write_text(to_html(records), encoding="utf-8")
    with Store(path) as store:
        store.add_bookmarks(parse_netscape_html(backup.read_text("utf-8")))
        assert store.bookmark(URL).title == "The Real Title"
