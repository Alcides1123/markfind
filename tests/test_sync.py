import json
import sqlite3
from pathlib import Path

import pytest

from markfind import cli
from markfind.browsers import Source, find_sources
from markfind.extract import Page
from markfind.importers import Bookmark
from markfind.store import SCHEMA_VERSION, Store


def touch(path: Path, text: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def local_state(names: dict[str, str]) -> str:
    return json.dumps({"profile": {"info_cache": {k: {"name": v} for k, v in names.items()}}})


def found(sources):
    return [(s.browser, s.profile, s.path) for s in sources]


# Detection


def test_finds_linux_profiles(tmp_path):
    home = tmp_path
    chrome = home / ".config/google-chrome"
    touch(chrome / "Local State", local_state({"Default": "Personal", "Profile 1": "Work"}))
    default = touch(chrome / "Default/Bookmarks", "{}")
    work = touch(chrome / "Profile 1/Bookmarks", "{}")
    (chrome / "Guest Profile").mkdir()  # no Bookmarks file: ignored
    brave = touch(home / ".config/BraveSoftware/Brave-Browser/Default/Bookmarks", "{}")

    firefox = home / ".mozilla/firefox"
    touch(
        firefox / "profiles.ini",
        "[General]\nStartWithLastProfile=1\n\n"
        "[Profile0]\nName=default-release\nIsRelative=1\nPath=ab12.default-release\n",
    )
    release = touch(firefox / "ab12.default-release/places.sqlite")
    unlisted = touch(firefox / "zz99.old/places.sqlite")
    snap = touch(home / "snap/firefox/common/.mozilla/firefox/cd34.default/places.sqlite")

    assert found(find_sources("linux", {}, home)) == [
        ("Chrome", "Personal", default),
        ("Chrome", "Work", work),
        ("Brave", "Default", brave),
        ("Firefox", "default-release", release),
        ("Firefox", "zz99.old", unlisted),
        ("Firefox", "cd34.default", snap),
    ]


def test_finds_windows_profiles(tmp_path):
    local, roaming = tmp_path / "Local", tmp_path / "Roaming"
    edge = touch(local / "Microsoft/Edge/User Data/Default/Bookmarks", "{}")
    firefox = roaming / "Mozilla/Firefox"
    touch(
        firefox / "profiles.ini",
        "[Profile0]\nName=Main\nIsRelative=1\nPath=Profiles/ab12.default-release\n",
    )
    places = touch(firefox / "Profiles/ab12.default-release/places.sqlite")

    env = {"LOCALAPPDATA": str(local), "APPDATA": str(roaming)}
    assert found(find_sources("win32", env, tmp_path / "home")) == [
        ("Edge", "Default", edge),
        ("Firefox", "Main", places),
    ]


def test_finds_macos_profiles(tmp_path):
    support = tmp_path / "Library/Application Support"
    vivaldi = touch(support / "Vivaldi/Default/Bookmarks", "{}")
    places = touch(support / "Firefox/Profiles/ab12.default/places.sqlite")
    # Linux locations are not looked at on macOS.
    touch(tmp_path / ".config/google-chrome/Default/Bookmarks", "{}")

    assert found(find_sources("darwin", {}, tmp_path)) == [
        ("Vivaldi", "Default", vivaldi),
        ("Firefox", "ab12.default", places),
    ]


def test_absolute_firefox_profile_path(tmp_path):
    elsewhere = touch(tmp_path / "data/ff-profile/places.sqlite").parent
    touch(
        tmp_path / ".mozilla/firefox/profiles.ini",
        f"[Profile0]\nName=Elsewhere\nIsRelative=0\nPath={elsewhere}\n",
    )
    touch(tmp_path / ".mozilla/firefox/ab12.default/places.sqlite")
    # Only profile folders under the Firefox folder are scanned; the ini just names them.
    assert [s.profile for s in find_sources("linux", {}, tmp_path)] == ["ab12.default"]


def test_bad_profile_metadata_falls_back_to_folder_names(tmp_path):
    chrome = tmp_path / ".config/chromium"
    touch(chrome / "Local State", "not json")
    touch(chrome / "Default/Bookmarks", "{}")
    firefox = tmp_path / ".mozilla/firefox"
    touch(firefox / "profiles.ini", "this is [not an ini file")
    touch(firefox / "ab12.default/places.sqlite")

    assert [s.name for s in find_sources("linux", {}, tmp_path)] == [
        "Chromium (Default)",
        "Firefox (ab12.default)",
    ]


def test_xdg_config_home(tmp_path):
    config = tmp_path / "cfg"
    bookmarks = touch(config / "microsoft-edge/Default/Bookmarks", "{}")
    env = {"XDG_CONFIG_HOME": str(config)}
    assert found(find_sources("linux", env, tmp_path)) == [("Edge", "Default", bookmarks)]


def test_nothing_installed(tmp_path):
    for platform in ("linux", "win32", "darwin"):
        assert find_sources(platform, {}, tmp_path) == []


# Pruning in the store


def bm(url, title=""):
    return Bookmark(url=url, title=title)


def test_prune_only_touches_its_own_source(tmp_path):
    with Store(tmp_path / "db.sqlite") as store:
        store.add_bookmarks([bm("https://a.example/", "A"), bm("https://b.example/")], "chrome")
        store.add_bookmarks([bm("https://c.example/")], "firefox")
        store.add_bookmarks([bm("https://d.example/")])  # no source: never pruned
        store.set_page(1, "unique aardvark text")

        removed = store.prune("chrome", ["https://b.example/"])

        assert removed == [("https://a.example/", "A")]
        assert sorted(url for url, _, _ in store.list_bookmarks()) == [
            "https://b.example/",
            "https://c.example/",
            "https://d.example/",
        ]
        assert store.search('"aardvark"') == []
        assert store.prune("chrome", ["https://b.example/"]) == []


def test_reimport_moves_source_but_untagged_import_keeps_it(tmp_path):
    with Store(tmp_path / "db.sqlite") as store:
        store.add_bookmarks([bm("https://a.example/")], "chrome")
        store.add_bookmarks([bm("https://a.example/")], "firefox")
        assert store.prune("chrome", []) == []
        store.add_bookmarks([bm("https://a.example/", "renamed")])
        assert store.prune("firefox", []) == [("https://a.example/", "renamed")]


def test_migrates_version_1_database(tmp_path):
    path = tmp_path / "v1.sqlite"
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE bookmarks (
            id INTEGER PRIMARY KEY, url TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL DEFAULT '', folder TEXT NOT NULL DEFAULT '',
            added_at TEXT, fetched_at TEXT, fetch_error TEXT, error_kind TEXT
        );
        INSERT INTO bookmarks (url, title) VALUES ('https://a.example/', 'A');
        PRAGMA user_version = 1;
        """
    )
    db.close()

    with Store(path) as store:
        assert store.db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 2
        # Bookmarks from before sources were recorded are left alone...
        assert store.prune("chrome", []) == []
        # ...until a sync claims them.
        store.add_bookmarks([bm("https://a.example/", "A")], "chrome")
        assert store.prune("chrome", []) == [("https://a.example/", "A")]


# The sync and import commands


def chrome_json(*urls: str) -> str:
    children = [{"type": "url", "name": url.split("/")[2], "url": url} for url in urls]
    return json.dumps(
        {"roots": {"bookmark_bar": {"type": "folder", "name": "Bar", "children": children}}}
    )


def fake_fetch(url, timeout=10.0):
    return Page(title="", text=f"page text of {url}")


@pytest.fixture
def browsers(tmp_path, monkeypatch):
    """Two fake browser profiles; returns their Bookmarks files."""
    chrome = touch(tmp_path / "chrome/Bookmarks", chrome_json("https://a.example/"))
    edge = touch(tmp_path / "edge/Bookmarks", chrome_json("https://b.example/"))
    sources = [Source("Chrome", "Work", chrome), Source("Edge", "Default", edge)]
    monkeypatch.setattr(cli, "find_sources", lambda: sources)
    monkeypatch.setattr(cli, "fetch_page", fake_fetch)
    return chrome, edge


def run(db, *args):
    return cli.main(["--db", str(db), *args])


def urls(db):
    with Store(db) as store:
        return sorted(url for url, _, _ in store.list_bookmarks())


def test_sync_imports_and_fetches(tmp_path, browsers, capsys):
    db = tmp_path / "db.sqlite"
    assert run(db, "sync", "--delay", "0") == 0
    out = capsys.readouterr().out
    assert "Chrome (Work): 1 added, 0 updated" in out
    assert "Edge (Default): 1 added, 0 updated" in out
    assert "fetched 2, failed 0" in out

    assert run(db, "search", "page", "text") == 0
    assert run(db, "sync") == 0
    out = capsys.readouterr().out
    assert "Chrome (Work): 0 added, 1 updated" in out
    assert "nothing to fetch" in out


def test_sync_list_and_browser_filter(tmp_path, browsers, capsys):
    chrome, _ = browsers
    db = tmp_path / "db.sqlite"
    assert run(db, "sync", "--list") == 0
    assert capsys.readouterr().out.splitlines() == [
        f"Chrome (Work)\t{chrome}",
        f"Edge (Default)\t{browsers[1]}",
    ]
    assert urls(db) == []

    assert run(db, "sync", "--browser", "Chrome", "--no-fetch") == 0
    out = capsys.readouterr().out
    assert "Edge" not in out and "fetch" not in out
    assert urls(db) == ["https://a.example/"]

    assert run(db, "sync", "--browser", "firefox") == 1
    assert "no browser bookmarks found" in capsys.readouterr().err


def test_sync_prune(tmp_path, browsers, capsys):
    chrome, edge = browsers
    db = tmp_path / "db.sqlite"
    export = touch(tmp_path / "export.html", '<DT><A HREF="https://manual.example/">Manual</A>')
    assert run(db, "import", str(export)) == 0
    assert run(db, "sync", "--no-fetch") == 0

    # a.example is deleted from Chrome; b.example moves from Edge to Chrome.
    chrome.write_text(chrome_json("https://b.example/", "https://new.example/"), "utf-8")
    edge.write_text(chrome_json("https://c.example/"), "utf-8")
    capsys.readouterr()

    assert run(db, "sync", "--no-fetch") == 0  # without --prune, nothing is removed
    assert "removed" not in capsys.readouterr().out
    assert "https://a.example/" in urls(db)

    assert run(db, "sync", "--no-fetch", "--prune") == 0
    out = capsys.readouterr().out
    assert "Chrome (Work): 1 removed\n  - a.example <https://a.example/>" in out
    assert "Edge (Default): 0 removed" in out
    assert urls(db) == [
        "https://b.example/",
        "https://c.example/",
        "https://manual.example/",
        "https://new.example/",
    ]


def test_sync_never_prunes_unreadable_or_empty_files(tmp_path, browsers, capsys):
    chrome, edge = browsers
    db = tmp_path / "db.sqlite"
    assert run(db, "sync", "--no-fetch") == 0

    chrome.write_text("{ corrupted", "utf-8")
    edge.write_text(chrome_json(), "utf-8")
    capsys.readouterr()
    assert run(db, "sync", "--no-fetch", "--prune") == 1
    captured = capsys.readouterr()
    assert "error: could not read Chrome (Work)" in captured.err
    assert "Edge (Default): no bookmarks found, not pruning" in captured.out
    assert urls(db) == ["https://a.example/", "https://b.example/"]


def test_import_prune(tmp_path, capsys):
    db = tmp_path / "db.sqlite"
    one = touch(
        tmp_path / "one.html",
        '<DT><A HREF="https://a.example/">A</A><DT><A HREF="https://b.example/">B</A>',
    )
    two = touch(tmp_path / "two.html", '<DT><A HREF="https://c.example/">C</A>')
    assert run(db, "import", str(one), str(two)) == 0

    one.write_text('<DT><A HREF="https://b.example/">B</A>', "utf-8")
    assert run(db, "import", "--prune", str(one)) == 0
    assert f"{one}: 1 removed\n  - A <https://a.example/>" in capsys.readouterr().out
    assert urls(db) == ["https://b.example/", "https://c.example/"]
