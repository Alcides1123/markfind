import csv
import io
import json
from datetime import datetime, timezone

import pytest

from markfind import cli
from markfind.formats import to_csv, to_html, to_json, to_markdown
from markfind.importers import Bookmark, parse_netscape_html
from markfind.store import Record, Store

ADDED = datetime(2021, 5, 4, 12, 30, tzinfo=timezone.utc)

BOOKMARKS = [
    Bookmark("https://sqlite.org/fts5.html", "SQLite FTS5", "Bar/Dev", ADDED),
    Bookmark("https://example.com/a?x=1&y=2", 'Tom & Jerry <"quoted">', "Bar/Dev/Deep"),
    Bookmark("https://gone.example/", "Dead page", "Bar"),
    Bookmark("https://nohost.invalid/", "No such host", "Other"),
    Bookmark("https://slow.example/", "Timed out", "Other"),
    Bookmark("https://top.example/", "Top level"),
    Bookmark("https://en.wikipedia.org/wiki/Foo_(bar)", "Foo [disambiguation] *star*", "Wiki"),
]


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "db.sqlite"
    with Store(path) as store:
        store.add_bookmarks(BOOKMARKS)
        store.set_page(1, "full-text search with sqlite")
        store.set_page(2, "a cartoon about a cat and a mouse")
        store.set_error(3, "HTTP 404 Not Found", "gone")
        store.set_error(4, "host not found", "dns")
        store.set_error(5, "timed out", "timeout")
    return str(path)


def records(db, folder=None):
    with Store(db) as store:
        return store.records(folder)


def test_records_and_status(db):
    by_url = {r.url: r for r in records(db)}
    assert by_url["https://sqlite.org/fts5.html"].status == "indexed"
    assert by_url["https://gone.example/"].status == "gone"
    assert by_url["https://top.example/"].status == "pending"
    assert by_url["https://sqlite.org/fts5.html"].added_at == ADDED.isoformat()
    assert [r.url for r in records(db, "Bar/Dev")] == [
        "https://sqlite.org/fts5.html",
        "https://example.com/a?x=1&y=2",
    ]


def test_html_round_trips_through_the_importer(db):
    exported = to_html(records(db))
    assert exported.startswith("<!DOCTYPE NETSCAPE-Bookmark-file-1>")
    assert 'HREF="https://example.com/a?x=1&amp;y=2"' in exported
    assert 'Tom &amp; Jerry &lt;"quoted"&gt;' in exported
    assert f'ADD_DATE="{int(ADDED.timestamp())}"' in exported

    parsed = parse_netscape_html(exported)
    assert sorted(parsed, key=lambda b: b.url) == sorted(BOOKMARKS, key=lambda b: b.url)


def test_markdown(db):
    md = to_markdown(records(db))
    assert md.startswith("# Bookmarks\n\n- [Top level](https://top.example/)\n\n## Bar\n")
    assert (
        '## Bar / Dev / Deep\n\n- [Tom & Jerry \\<"quoted"\\>](https://example.com/a?x=1&y=2)' in md
    )
    assert (
        "- [Foo \\[disambiguation\\] \\*star\\*](<https://en.wikipedia.org/wiki/Foo_(bar)>)" in md
    )


def test_markdown_ranked():
    rows = [
        Record("https://b.example/", "B", "Dev", None, None, None),
        Record("https://a.example/", "", "", None, None, None),
    ]
    assert to_markdown(rows, ranked=True) == (
        "# Bookmarks\n\n"
        "1. [B](https://b.example/) · *Dev*\n"
        "2. [https://a.example/](https://a.example/)\n"
    )


def test_json_and_csv(db):
    rows = json.loads(to_json(records(db)))
    gone = next(r for r in rows if r["url"] == "https://gone.example/")
    assert gone["status"] == "gone" and gone["folder"] == "Bar" and gone["fetched_at"]
    assert set(gone) == {
        "url",
        "title",
        "folder",
        "added_at",
        "fetched_at",
        "status",
        "tags",
        "note",
    }
    assert gone["tags"] == []
    assert gone["note"] == ""

    table = list(csv.DictReader(io.StringIO(to_csv(records(db)))))
    assert len(table) == len(BOOKMARKS)
    tricky = next(r for r in table if r["url"].startswith("https://example.com/a"))
    assert tricky["title"] == 'Tom & Jerry <"quoted">'
    assert tricky["status"] == "indexed" and tricky["added_at"] == ""


def test_empty():
    assert parse_netscape_html(to_html([])) == []
    assert to_markdown([]) == "# Bookmarks\n"
    assert to_json([]) == "[]\n"
    assert to_csv([]).splitlines() == ["url,title,folder,added_at,fetched_at,status,tags,note"]


def test_cli_skip_dead_to_file(db, tmp_path, capsys):
    out = tmp_path / "clean.html"
    assert cli.main(["--db", db, "export", "--skip-dead", "-o", str(out)]) == 0
    assert f"exported 5 bookmarks to {out}" in capsys.readouterr().err
    urls = {b.url for b in parse_netscape_html(out.read_text(encoding="utf-8"))}
    # gone and dns are dead; a timeout might be temporary, so it stays.
    assert "https://gone.example/" not in urls and "https://nohost.invalid/" not in urls
    assert "https://slow.example/" in urls and len(urls) == 5


def test_cli_query_folder_and_formats(db, tmp_path, capsys):
    assert cli.main(["--db", db, "export", "--query", "cat mouse", "-f", "json"]) == 0
    assert [r["url"] for r in json.loads(capsys.readouterr().out)] == [
        "https://example.com/a?x=1&y=2"
    ]

    assert (
        cli.main(["--db", db, "export", "--query", "sqlite OR cartoon", "--raw", "-f", "markdown"])
        == 0
    )
    out = capsys.readouterr().out
    assert out.startswith("# Bookmarks\n\n1. ") and "2. " in out

    assert cli.main(["--db", db, "export", "--folder", "Other", "-f", "csv"]) == 0
    assert len(capsys.readouterr().out.splitlines()) == 3  # header + 2

    csv_file = tmp_path / "out.csv"
    assert cli.main(["--db", db, "export", "-f", "csv", "-o", str(csv_file)]) == 0
    assert csv_file.read_bytes().startswith(b"\xef\xbb\xbfurl,title")

    assert cli.main(["--db", db, "export", "--query", "a AND", "--raw"]) == 2
    assert "invalid query" in capsys.readouterr().err

    assert cli.main(["--db", db, "export", "-o", str(tmp_path / "missing" / "x.html")]) == 1
    assert "could not write" in capsys.readouterr().err
