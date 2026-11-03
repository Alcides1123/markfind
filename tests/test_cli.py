from markfind import cli
from markfind.extract import Page
from markfind.fetch import FetchError

BOOKMARKS = """<!DOCTYPE NETSCAPE-Bookmark-file-1>
<DL><p>
    <DT><H3>Reading</H3>
    <DL><p>
        <DT><A HREF="https://good.example/">Good page</A>
        <DT><A HREF="https://bad.example/">Bad page</A>
    </DL><p>
</DL><p>
"""


def fake_fetch(url, timeout=10.0):
    if "bad" in url:
        raise FetchError("unsupported content type: application/pdf")
    return Page(title="Good", text="an essay about migrating birds")


def test_end_to_end(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_page", fake_fetch)
    db = str(tmp_path / "db.sqlite")
    export = tmp_path / "bookmarks.html"
    export.write_text(BOOKMARKS, encoding="utf-8")

    assert cli.main(["--db", db, "import", str(export)]) == 0
    assert "2 added, 0 updated" in capsys.readouterr().out

    assert cli.main(["--db", db, "fetch", "--workers", "2"]) == 0
    assert "fetched 1, failed 1" in capsys.readouterr().out

    assert cli.main(["--db", db, "fetch"]) == 0
    assert "nothing to fetch" in capsys.readouterr().out

    assert cli.main(["--db", db, "search", "migrating", "birds"]) == 0
    out = capsys.readouterr().out
    assert "https://good.example/" in out
    assert "folder: Reading" in out
    assert "[migrating] [birds]" in out

    assert cli.main(["--db", db, "search", "penguins"]) == 1
    assert "no matches" in capsys.readouterr().out

    assert cli.main(["--db", db, "search", "--raw", "birds AND"]) == 2
    assert "invalid query" in capsys.readouterr().err

    assert cli.main(["--db", db, "stats"]) == 0
    out = capsys.readouterr().out
    assert "bookmarks: 2" in out and "indexed:   1" in out and "failed:    1" in out


def test_import_missing_file(tmp_path, capsys):
    db = str(tmp_path / "db.sqlite")
    assert cli.main(["--db", db, "import", str(tmp_path / "nope.html")]) == 1
    assert "could not read" in capsys.readouterr().err
