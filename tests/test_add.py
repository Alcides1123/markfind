"""`markfind add URL`: putting a bookmark in by hand and indexing it at once."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from markfind import cli
from markfind.importers import Bookmark
from markfind.store import Store

PROSE = " ".join(f"word{i}" for i in range(60))
PAGES = {
    "/one": b"<title>The First Page</title><main><p>Sourdough " + PROSE.encode() + b"</p></main>",
    "/two": b"<title>The Second Page</title><main><p>Telescope " + PROSE.encode() + b"</p></main>",
    "/empty": b"<title>Nothing</title><main></main>",
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's name
        if self.path not in PAGES:
            self.send_response(404)
            self.end_headers()
            return
        body = PAGES[self.path]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def base_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    thread.join()
    server.server_close()


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "db.sqlite")


def add(db, *args):
    return cli.main(["--db", db, "add", *args])


# --- adding and indexing ----------------------------------------------------------


def test_add_fetches_and_indexes_the_page(db, base_url, capsys):
    assert add(db, base_url + "/one") == 0
    out = capsys.readouterr().out
    assert "added: The First Page" in out and base_url + "/one" in out

    with Store(db, readonly=True) as store:
        assert [r.title for r in store.search("sourdough")] == ["The First Page"]
        record = store.bookmark(base_url + "/one")
        assert record.status == "indexed" and record.saved and record.added_at


def test_several_at_once(db, base_url, capsys):
    assert add(db, base_url + "/one", base_url + "/two") == 0
    out = capsys.readouterr().out
    assert out.count("added: ") == 2
    with Store(db, readonly=True) as store:
        assert len(store.records()) == 2


def test_tags_notes_and_folder(db, base_url, capsys):
    assert (
        add(
            db,
            base_url + "/one",
            "--tag",
            "sqlite",
            "--tag",
            "docs",
            "--note",
            "read me",
            "--folder",
            "Reference",
        )
        == 0
    )
    with Store(db, readonly=True) as store:
        record = store.bookmark(base_url + "/one")
        assert record.folder == "Reference"
        assert record.tags == ("docs", "sqlite") or set(record.tags) == {"docs", "sqlite"}
        assert record.note == "read me"
        assert [r.url for r in store.search("sourdough", tag="sqlite")] == [base_url + "/one"]


def test_no_fetch_adds_without_downloading(db, base_url, capsys):
    assert add(db, base_url + "/one", "--no-fetch") == 0
    assert "ok:" not in capsys.readouterr().out
    with Store(db, readonly=True) as store:
        record = store.bookmark(base_url + "/one")
        assert record.status == "pending" and record.title == ""


# --- adding something already there -----------------------------------------------


def test_adding_again_keeps_what_the_bookmark_has(db, base_url, capsys):
    add(db, base_url + "/one", "--folder", "Reference", "--tag", "first")
    capsys.readouterr()

    assert add(db, base_url + "/one", "--folder", "Somewhere else", "--tag", "second") == 0
    out = capsys.readouterr().out
    assert "already in markfind: The First Page" in out
    assert "ok:" not in out  # an indexed page isn't downloaded again

    with Store(db, readonly=True) as store:
        record = store.bookmark(base_url + "/one")
        assert record.folder == "Reference"  # the folder it was filed under stays
        assert set(record.tags) == {"first", "second"}  # tags accumulate


def test_adding_again_can_fill_in_a_page_that_was_never_fetched(db, base_url, capsys):
    add(db, base_url + "/one", "--no-fetch")
    capsys.readouterr()
    assert add(db, base_url + "/one") == 0
    with Store(db, readonly=True) as store:
        assert store.bookmark(base_url + "/one").status == "indexed"


def test_an_imported_bookmark_is_not_disturbed(db, base_url, capsys):
    with Store(db) as store:
        store.add_bookmarks(
            [Bookmark(base_url + "/one", "From the browser", "Bookmarks bar")], source="export.html"
        )
    assert add(db, base_url + "/one") == 0
    with Store(db, readonly=True) as store:
        record = store.bookmark(base_url + "/one")
        assert record.title == "From the browser" and record.folder == "Bookmarks bar"


# --- things that go wrong ----------------------------------------------------------


@pytest.mark.parametrize("url", ["not a url", "ftp://a.example/x", "javascript:alert(1)", "", "/x"])
def test_only_web_addresses(db, url, capsys):
    assert add(db, url) == 2
    assert "not a web address" in capsys.readouterr().err
    with Store(db, readonly=True) as store:
        assert store.records() == []


def test_nothing_is_added_when_one_url_is_bad(db, base_url, capsys):
    assert add(db, base_url + "/one", "mailto:someone@example.com") == 2
    with Store(db, readonly=True) as store:
        assert store.records() == []


def test_a_page_that_cannot_be_read_is_still_kept(db, base_url, capsys):
    assert add(db, base_url + "/missing") == 1  # exit code says something went wrong
    out = capsys.readouterr().out
    assert "failed, gone" in out and "already in markfind" not in out
    with Store(db, readonly=True) as store:
        record = store.bookmark(base_url + "/missing")
        assert record is not None and record.error_kind == "gone"


def test_an_empty_page_is_reported(db, base_url, capsys):
    assert add(db, base_url + "/empty") == 1
    with Store(db, readonly=True) as store:
        assert store.bookmark(base_url + "/empty").error_kind == "empty"


# --- the store methods behind it ---------------------------------------------------


def test_add_bookmark_returns_whether_it_was_new(tmp_path):
    with Store(tmp_path / "s.sqlite") as store:
        assert store.add_bookmark("https://a.example/", folder="Dev") is True
        assert store.add_bookmark("https://a.example/") is False
        assert store.bookmark("https://a.example/").folder == "Dev"


def test_bookmark_returns_none_for_an_unknown_url(tmp_path):
    with Store(tmp_path / "s.sqlite") as store:
        assert store.bookmark("https://nowhere.example/") is None


def test_a_hand_added_bookmark_is_searchable_by_title_and_url(tmp_path):
    with Store(tmp_path / "s.sqlite") as store:
        store.add_bookmark("https://a.example/sourdough-guide")
        assert [r.url for r in store.search("sourdough")] == ["https://a.example/sourdough-guide"]


def test_prune_leaves_hand_added_bookmarks_alone(tmp_path):
    with Store(tmp_path / "s.sqlite") as store:
        store.add_bookmarks([Bookmark("https://imported.example/")], source="export.html")
        store.add_bookmark("https://byhand.example/")
        store.prune("export.html", keep=[])
        assert [r.url for r in store.records()] == ["https://byhand.example/"]
