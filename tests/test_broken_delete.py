"""``markfind broken --delete`` and ``Store.delete``."""

import io
import socket
import urllib.error

import pytest

from markfind import cli
from markfind.extract import Page
from markfind.store import Store

EXPORT = """<DL><p><DT><H3>Old</H3><DL><p>
<DT><A HREF="https://gone.example/page">Gone page</A>
<DT><A HREF="https://nxdomain.example/">No such host</A>
<DT><A HREF="https://slow.example/">Slow</A>
<DT><A HREF="https://fine.example/">Fine</A>
</DL><p></DL><p>"""


class Terminal(io.StringIO):
    def isatty(self):
        return True


def fetch(url, timeout=10.0):
    if "gone" in url:
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
    if "nxdomain" in url:
        raise urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed"))
    if "slow" in url:
        raise TimeoutError("timed out")
    return Page("", "a page about gardening")


@pytest.fixture
def db(tmp_path, monkeypatch, capsys):
    db = str(tmp_path / "db.sqlite")
    export = tmp_path / "bookmarks.html"
    export.write_text(EXPORT, encoding="utf-8")
    monkeypatch.setattr(cli, "fetch_page", fetch)
    cli.main(["--db", db, "import", str(export)])
    cli.main(["--db", db, "fetch", "--delay", "0", "-q"])
    capsys.readouterr()
    return db


def urls(db):
    with Store(db) as store:
        return sorted(url for url, _, _ in store.list_bookmarks())


def answer(monkeypatch, text):
    prompts = []
    monkeypatch.setattr("sys.stdin", Terminal())
    monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or text)
    return prompts


def test_delete_after_yes(db, monkeypatch, capsys):
    prompts = answer(monkeypatch, "y")
    assert cli.main(["--db", db, "broken", "--delete"]) == 0
    assert prompts == ["Delete these 2 bookmarks from markfind? [y/N] "]
    out = capsys.readouterr().out
    assert "gone (1):" in out and "dns (1):" in out  # the report comes first
    assert "deleted 2 bookmarks from markfind" in out
    assert "Your browser still has them" in out
    assert urls(db) == ["https://fine.example/", "https://slow.example/"]

    assert cli.main(["--db", db, "broken"]) == 0
    assert "no matching bookmarks" in capsys.readouterr().out


def test_delete_declined(db, monkeypatch, capsys):
    answer(monkeypatch, "")
    assert cli.main(["--db", db, "broken", "--delete"]) == 0
    assert "nothing deleted" in capsys.readouterr().out
    assert len(urls(db)) == 4


def test_delete_eof_counts_as_no(db, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", Terminal())

    def eof(prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    assert cli.main(["--db", db, "broken", "--delete"]) == 0
    assert "nothing deleted" in capsys.readouterr().out
    assert len(urls(db)) == 4


def test_delete_without_terminal_needs_yes(db, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("y\n"))  # piped input isn't a terminal
    assert cli.main(["--db", db, "broken", "--delete"]) == 1
    assert "add --yes" in capsys.readouterr().err
    assert len(urls(db)) == 4

    assert cli.main(["--db", db, "broken", "--delete", "--yes"]) == 0
    assert "deleted 2 bookmarks" in capsys.readouterr().out
    assert len(urls(db)) == 2


def test_delete_follows_kind_selection(db, capsys):
    assert cli.main(["--db", db, "broken", "--kind", "timeout", "--delete", "-y"]) == 0
    assert "deleted 1 bookmark from markfind" in capsys.readouterr().out
    assert "https://slow.example/" not in urls(db)
    assert "https://gone.example/page" in urls(db)

    assert cli.main(["--db", db, "broken", "--all", "--delete", "-y"]) == 0
    assert urls(db) == ["https://fine.example/"]


def test_delete_nothing_to_delete(db, capsys):
    assert cli.main(["--db", db, "broken", "--kind", "ssl", "--delete"]) == 0
    out = capsys.readouterr().out
    assert "no matching bookmarks" in out and "deleted" not in out


def test_delete_with_urls_is_an_error(db, capsys):
    assert cli.main(["--db", db, "broken", "--delete", "--urls", "-y"]) == 2
    assert "--urls" in capsys.readouterr().err
    assert len(urls(db)) == 4


def test_deleted_bookmarks_leave_the_search_index(db, capsys):
    cli.main(["--db", db, "broken", "--all", "--delete", "-y"])
    with Store(db) as store:
        assert [r.url for r in store.search("gardening")] == ["https://fine.example/"]
        assert store.search('"no such host"') == []
        assert store.stats().total == 1
        assert store.stats().failed == 0


def test_store_delete(tmp_path):
    from markfind.importers import Bookmark

    with Store(str(tmp_path / "db.sqlite")) as store:
        store.add_bookmarks([Bookmark("https://a.example/"), Bookmark("https://b.example/")])
        assert store.delete(["https://a.example/", "https://a.example/", "https://x.example/"]) == 1
        assert [url for url, _, _ in store.list_bookmarks()] == ["https://b.example/"]
        assert store.delete([]) == 0


def test_delete_one_asks_about_this_bookmark(db, monkeypatch, capsys):
    prompts = answer(monkeypatch, "yes")
    assert cli.main(["--db", db, "broken", "--kind", "gone", "--delete"]) == 0
    assert prompts == ["Delete this bookmark from markfind? [y/N] "]
    assert "deleted 1 bookmark from markfind" in capsys.readouterr().out
