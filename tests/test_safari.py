import plistlib
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from markfind import cli
from markfind.browsers import Source, find_sources
from markfind.importers import Bookmark, load_file, parse_safari_plist
from markfind.store import Store

READING_ADDED = datetime(2024, 3, 1, 12, 30)  # plist dates are UTC, without a zone


def leaf(url, title=None, **extra):
    node = {"WebBookmarkType": "WebBookmarkTypeLeaf", "URLString": url, **extra}
    if title is not None:
        node["URIDictionary"] = {"title": title}
    return node


def folder(title, *children):
    return {"WebBookmarkType": "WebBookmarkTypeList", "Title": title, "Children": list(children)}


# Shaped like a real ~/Library/Safari/Bookmarks.plist.
TREE = {
    "WebBookmarkFileVersion": 1,
    "WebBookmarkType": "WebBookmarkTypeList",
    "Title": "",
    "Children": [
        {"WebBookmarkType": "WebBookmarkTypeProxy", "Title": "History"},
        folder(
            "BookmarksBar",
            leaf("https://sqlite.org/fts5.html", "SQLite FTS5"),
            folder("Dev", leaf("https://docs.python.org/3/", "Python \n docs")),
            leaf("javascript:alert(1)", "A bookmarklet"),
            leaf("https://untitled.example/"),
        ),
        folder("BookmarksMenu", leaf("https://news.example/", "News")),
        folder(
            "com.apple.ReadingList",
            leaf(
                "https://later.example/article",
                "Read me later",
                ReadingList={"DateAdded": READING_ADDED, "PreviewText": "..."},
            ),
        ),
        {"WebBookmarkType": "WebBookmarkTypeList", "Title": "No children key"},
    ],
}

EXPECTED = [
    Bookmark("https://sqlite.org/fts5.html", "SQLite FTS5", "Favorites"),
    Bookmark("https://docs.python.org/3/", "Python docs", "Favorites/Dev"),
    Bookmark("https://untitled.example/", "", "Favorites"),
    Bookmark("https://news.example/", "News", "Bookmarks Menu"),
    Bookmark(
        "https://later.example/article",
        "Read me later",
        "Reading List",
        READING_ADDED.replace(tzinfo=timezone.utc),
    ),
]


@pytest.mark.parametrize("fmt", [plistlib.FMT_BINARY, plistlib.FMT_XML])
def test_parse(fmt):
    assert parse_safari_plist(plistlib.dumps(TREE, fmt=fmt)) == EXPECTED


@pytest.mark.parametrize("fmt", [plistlib.FMT_BINARY, plistlib.FMT_XML])
def test_load_file_detects_plists(tmp_path, fmt):
    path = tmp_path / "Bookmarks.plist"
    path.write_bytes(plistlib.dumps(TREE, fmt=fmt))
    assert load_file(path) == EXPECTED


@pytest.mark.parametrize(
    "data",
    [
        b"bplist00 this is not really a binary plist",
        b'<?xml version="1.0"?><plist version="1.0"><dict><key>x</key>',
        # An encoding Python doesn't have: plistlib raises LookupError, not ValueError.
        b'<?xml version="1.0" encoding="zTF-8"?><plist version="1.0"><dict/></plist>',
        plistlib.dumps(["a list, not a dict"]),
    ],
)
def test_corrupt_plist(data):
    with pytest.raises(ValueError, match="not a readable Safari bookmarks file"):
        parse_safari_plist(data)


@pytest.mark.skipif(shutil.which("plutil") is None, reason="plutil comes with macOS")
def test_binary_plist_written_by_apples_own_tool(tmp_path):
    # plistlib wrote every other binary plist here; this one is from macOS.
    path = tmp_path / "Bookmarks.plist"
    path.write_bytes(plistlib.dumps(TREE, fmt=plistlib.FMT_XML))
    subprocess.run(["plutil", "-convert", "binary1", str(path)], check=True)
    assert path.read_bytes().startswith(b"bplist00")
    assert load_file(path) == EXPECTED


def test_a_corrupt_plist_file_is_reported_not_raised(tmp_path, capsys):
    path = tmp_path / "Bookmarks.plist"
    path.write_bytes(b'<?xml version="1.0" encoding="zTF-8"?><plist><dict/></plist>')
    assert cli.main(["--db", str(tmp_path / "db.sqlite"), "import", str(path)]) == 1
    assert "could not read" in capsys.readouterr().err


def test_other_formats_still_detected(tmp_path):
    html = tmp_path / "export.html"
    html.write_text('<DT><A HREF="https://a.example/">A</A>', encoding="utf-8")
    assert [b.url for b in load_file(html)] == ["https://a.example/"]


# Browser detection


def safari_file(home):
    path = home / "Library" / "Safari" / "Bookmarks.plist"
    path.parent.mkdir(parents=True)
    path.write_bytes(plistlib.dumps(TREE, fmt=plistlib.FMT_BINARY))
    return path


def test_found_on_macos_only(tmp_path):
    path = safari_file(tmp_path)
    firefox = tmp_path / "Library/Application Support/Firefox/Profiles/ab12.default/places.sqlite"
    firefox.parent.mkdir(parents=True)
    firefox.touch()
    sources = find_sources("darwin", {}, tmp_path)
    assert [(s.browser, s.profile) for s in sources] == [
        ("Firefox", "ab12.default"),
        ("Safari", "Default"),
    ]
    assert sources[-1].path == path
    assert find_sources("linux", {}, tmp_path) == []
    assert find_sources("win32", {}, tmp_path) == []


def test_found_when_macos_hides_it(tmp_path, monkeypatch):
    # Without Full Disk Access, even checking whether the file exists fails.
    real_is_file = Path.is_file

    def is_file(self):
        if "Safari" in self.parts:
            raise PermissionError(1, "Operation not permitted")
        return real_is_file(self)

    monkeypatch.setattr(Path, "is_file", is_file)
    assert [s.name for s in find_sources("darwin", {}, tmp_path)] == ["Safari (Default)"]


# Commands


def test_sync_imports_safari(tmp_path, monkeypatch, capsys):
    path = safari_file(tmp_path)
    monkeypatch.setattr(cli, "find_sources", lambda: [Source("Safari", "Default", path)])
    db = tmp_path / "db.sqlite"
    assert cli.main(["--db", str(db), "sync", "--browser", "safari", "--no-fetch"]) == 0
    assert "Safari (Default): 5 added, 0 updated" in capsys.readouterr().out
    with Store(db) as store:
        assert ("https://news.example/", "News", "Bookmarks Menu") in store.list_bookmarks()


def no_access(path):
    raise PermissionError(1, "Operation not permitted", str(path))


def test_permission_hint_on_sync(tmp_path, monkeypatch, capsys):
    path = tmp_path / "Library" / "Safari" / "Bookmarks.plist"
    monkeypatch.setattr(cli, "find_sources", lambda: [Source("Safari", "Default", path)])
    monkeypatch.setattr(cli, "load_file", no_access)
    assert cli.main(["--db", str(tmp_path / "db.sqlite"), "sync", "--no-fetch"]) == 1
    err = capsys.readouterr().err
    assert "error: could not read Safari (Default)" in err
    assert "Full Disk Access" in err and "File > Export > Bookmarks" in err


def test_permission_hint_on_import_only_for_safari(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_file", no_access)
    db = str(tmp_path / "db.sqlite")
    safari = tmp_path / "Library" / "Safari" / "Bookmarks.plist"
    assert cli.main(["--db", db, "import", str(safari)]) == 1
    assert "Full Disk Access" in capsys.readouterr().err

    other = tmp_path / "Downloads" / "bookmarks.html"
    assert cli.main(["--db", db, "import", str(other)]) == 1
    err = capsys.readouterr().err
    assert "could not read" in err and "Full Disk Access" not in err
