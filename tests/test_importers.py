import json
from datetime import datetime, timezone

from markfind.importers import load_file, parse_chrome_json, parse_netscape_html

NETSCAPE = """<!DOCTYPE NETSCAPE-Bookmark-file-1>
<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">
<TITLE>Bookmarks</TITLE>
<H1>Bookmarks</H1>
<DL><p>
    <DT><H3 ADD_DATE="1700000000">Bookmarks bar</H3>
    <DL><p>
        <DT><A HREF="https://example.com/a" ADD_DATE="1700000000">Example &amp; A</A>
        <DT><H3>Dev</H3>
        <DL><p>
            <DT><A HREF="https://docs.python.org/3/">Python  docs</A>
            <DT><A HREF="javascript:alert(1)">bookmarklet</A>
        </DL><p>
        <DT><A HREF="https://example.com/b">B</A>
    </DL><p>
    <DT><A HREF="http://top.example/">Top level</A>
</DL><p>
"""


def test_netscape_folders_titles_and_dates():
    bms = parse_netscape_html(NETSCAPE)
    assert [(b.url, b.title, b.folder) for b in bms] == [
        ("https://example.com/a", "Example & A", "Bookmarks bar"),
        ("https://docs.python.org/3/", "Python docs", "Bookmarks bar/Dev"),
        ("https://example.com/b", "B", "Bookmarks bar"),
        ("http://top.example/", "Top level", ""),
    ]
    assert bms[0].added_at == datetime.fromtimestamp(1700000000, timezone.utc)
    assert bms[1].added_at is None


def chrome_doc():
    return {
        "checksum": "x",
        "roots": {
            "bookmark_bar": {
                "type": "folder",
                "name": "Bookmarks bar",
                "children": [
                    {
                        "type": "url",
                        "name": "SQLite FTS5",
                        "url": "https://sqlite.org/fts5.html",
                        "date_added": "13300000000000000",
                    },
                    {
                        "type": "folder",
                        "name": "Reading",
                        "children": [
                            {"type": "url", "name": "Blog", "url": "https://blog.example/"},
                            {"type": "url", "name": "Local", "url": "file:///C:/x.html"},
                        ],
                    },
                ],
            },
            "other": {"type": "folder", "name": "Other bookmarks", "children": []},
            "sync_transaction_version": "1",
        },
        "version": 1,
    }


def test_chrome_json():
    bms = parse_chrome_json(json.dumps(chrome_doc()))
    assert [(b.url, b.folder) for b in bms] == [
        ("https://sqlite.org/fts5.html", "Bookmarks bar"),
        ("https://blog.example/", "Bookmarks bar/Reading"),
    ]
    assert bms[0].added_at is not None and bms[0].added_at.year == 2022
    assert bms[1].added_at is None


def test_load_file_detects_format(tmp_path):
    html = tmp_path / "bookmarks.html"
    html.write_text(NETSCAPE, encoding="utf-8")
    js = tmp_path / "Bookmarks"
    js.write_text(json.dumps(chrome_doc()), encoding="utf-8")

    assert len(load_file(html)) == 4
    assert len(load_file(js)) == 2
