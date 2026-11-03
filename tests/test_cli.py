import socket
import ssl
import urllib.error

from markfind import cli
from markfind.extract import Page
from markfind.fetch import UnsupportedContentError

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
        raise UnsupportedContentError("unsupported content type: application/pdf")
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
    assert "bookmarks: 2" in out and "indexed:   1" in out
    assert "failed:    1 (unsupported 1)" in out


def test_broken_report(tmp_path, monkeypatch, capsys):
    db = str(tmp_path / "db.sqlite")
    export = tmp_path / "bookmarks.html"
    export.write_text(
        """<DL><p><DT><H3>Old</H3><DL><p>
        <DT><A HREF="https://gone.example/page">Gone page</A>
        <DT><A HREF="https://nxdomain.example/">No such host</A>
        <DT><A HREF="https://cert.example/">Bad cert</A>
        <DT><A HREF="https://fine.example/">Fine</A>
        </DL><p></DL><p>""",
        encoding="utf-8",
    )

    def fetch(url, timeout=10.0):
        if "gone" in url:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        if "nxdomain" in url:
            raise urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed"))
        if "cert" in url:
            raise urllib.error.URLError(ssl.SSLCertVerificationError("bad cert"))
        return Page("", "ok")

    monkeypatch.setattr(cli, "fetch_page", fetch)
    cli.main(["--db", db, "import", str(export)])
    cli.main(["--db", db, "fetch", "--delay", "0"])
    out = capsys.readouterr().out
    assert "failed, gone: HTTP 404 Not Found: https://gone.example/page" in out
    assert "markfind broken --all" in out
    assert "hint: some pages failed SSL certificate checks" in out

    assert cli.main(["--db", db, "broken"]) == 0
    out = capsys.readouterr().out
    assert "dns (1):" in out and "gone (1):" in out
    assert "host not found" in out and "folder: Old" in out
    assert "cert.example" not in out and "fine.example" not in out

    cli.main(["--db", db, "broken", "--all", "--urls"])
    # grouped by kind: dns, gone, ssl
    assert capsys.readouterr().out.split() == [
        "https://nxdomain.example/",
        "https://gone.example/page",
        "https://cert.example/",
    ]

    cli.main(["--db", db, "broken", "--kind", "ssl", "--urls"])
    assert capsys.readouterr().out.split() == ["https://cert.example/"]

    cli.main(["--db", db, "fetch", "--failed", "--delay", "0", "-q"])
    capsys.readouterr()
    cli.main(["--db", db, "stats"])
    assert "failed:    3 (dns 1, gone 1, ssl 1)" in capsys.readouterr().out


def test_import_missing_file(tmp_path, capsys):
    db = str(tmp_path / "db.sqlite")
    assert cli.main(["--db", db, "import", str(tmp_path / "nope.html")]) == 1
    assert "could not read" in capsys.readouterr().err
