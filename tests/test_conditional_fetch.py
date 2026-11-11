"""Conditional re-fetches: asking the server whether a page changed before re-reading it."""

import sqlite3
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from markfind import cli
from markfind.extract import Page
from markfind.fetch import NotModified, fetch_many, fetch_page
from markfind.importers import Bookmark
from markfind.store import SCHEMA_VERSION, Store

ETAG = '"v1"'
MODIFIED = "Wed, 05 Nov 2026 12:00:00 GMT"
BODY = b"<html><head><title>Notes</title></head><body><p>The first version.</p></body></html>"
CHANGED = b"<html><head><title>Notes</title></head><body><p>The second version.</p></body></html>"

seen: list[tuple[str, str | None, str | None]] = []  # (path, If-None-Match, If-Modified-Since)
changed = False  # flips /etag to a new version


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's name
        none_match = self.headers.get("If-None-Match")
        since = self.headers.get("If-Modified-Since")
        seen.append((self.path, none_match, since))

        if self.path == "/etag":
            tag = '"v2"' if changed else ETAG
            if none_match == tag:
                self.send_response(304)
                self.end_headers()
                return
            self._body(CHANGED if changed else BODY, {"ETag": tag})
        elif self.path == "/modified":
            if since == MODIFIED:
                self.send_response(304)
                self.end_headers()
                return
            self._body(BODY, {"Last-Modified": MODIFIED})
        elif self.path == "/plain":  # no validators offered at all
            self._body(BODY, {})
        else:
            self.send_response(404)
            self.end_headers()

    def _body(self, body, headers):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for name, value in headers.items():
            self.send_header(name, value)
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


@pytest.fixture(autouse=True)
def fresh():
    global changed
    seen.clear()
    changed = False
    yield
    changed = False


# --- fetch_page ------------------------------------------------------------------


def test_validators_come_back_with_the_page(base_url):
    page = fetch_page(base_url + "/etag")
    assert page.text == "The first version." and page.etag == ETAG
    assert fetch_page(base_url + "/modified").last_modified == MODIFIED


def test_a_page_offering_nothing_has_no_validators(base_url):
    page = fetch_page(base_url + "/plain")
    assert page.etag == "" and page.last_modified == ""


def test_an_unchanged_page_raises_not_modified(base_url):
    with pytest.raises(NotModified):
        fetch_page(base_url + "/etag", validators=(ETAG, ""))
    assert seen[-1][1] == ETAG  # If-None-Match was sent

    with pytest.raises(NotModified):
        fetch_page(base_url + "/modified", validators=("", MODIFIED))
    assert seen[-1][2] == MODIFIED  # If-Modified-Since was sent


def test_a_changed_page_is_read_again(base_url):
    global changed
    changed = True
    page = fetch_page(base_url + "/etag", validators=(ETAG, ""))
    assert page.text == "The second version." and page.etag == '"v2"'


def test_no_validators_means_no_conditional_headers(base_url):
    fetch_page(base_url + "/etag")
    assert seen[-1] == ("/etag", None, None)


def test_other_http_errors_still_raise(base_url):
    with pytest.raises(urllib.error.HTTPError):
        fetch_page(base_url + "/missing", validators=(ETAG, ""))


# --- fetch_many ------------------------------------------------------------------


def test_fetch_many_uses_each_bookmark_s_own_validators(base_url):
    items = [(1, base_url + "/etag"), (2, base_url + "/plain")]
    results = {
        id_: result for id_, _, result in fetch_many(items, workers=1, validators={1: (ETAG, "")})
    }
    assert isinstance(results[1], NotModified)
    assert results[2].text == "The first version."  # no validators, so fetched normally


def test_fetch_many_without_validators_calls_a_plain_fetch():
    # Stubs elsewhere take (url, timeout) only; they must keep working.
    def fetch(url, timeout):
        return Page("", url)

    ((_, _, result),) = fetch_many([(1, "https://a.example/")], fetch=fetch, workers=1)
    assert result.text == "https://a.example/"


# --- the store -------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "db.sqlite"
    with Store(path) as store:
        store.add_bookmarks(
            [Bookmark(f"https://{n}.example/", n.title()) for n in ("good", "bare", "broken")]
        )
    return path


def ids_of(store):
    return {url: id_ for id_, url in store.pending()}


def test_store_keeps_and_returns_validators(db):
    with Store(db) as store:
        ids = ids_of(store)
        store.set_page(ids["https://good.example/"], "text", "Good", etag=ETAG)
        store.set_page(ids["https://bare.example/"], "text", "Bare")  # nothing to revalidate
        store.set_error(ids["https://broken.example/"], "HTTP 500", "server")
        found = store.validators(ids.values())
    assert found == {ids["https://good.example/"]: (ETAG, "")}


def test_validators_of_nothing(db):
    with Store(db) as store:
        assert store.validators([]) == {}


def test_set_unchanged_keeps_the_text_and_the_copy(db):
    with Store(db) as store:
        id_ = ids_of(store)["https://good.example/"]
        store.set_page(id_, "the indexed words", "Good", html="<p>the indexed words</p>")
        store.set_error(id_, "temporary", "server")  # a later failure, to prove it is cleared
        store.set_unchanged(id_)

        row = store.db.execute(
            "SELECT fetched_at, fetch_error, error_kind FROM bookmarks WHERE id = ?", (id_,)
        ).fetchone()
        assert row[0] and row[1] is None and row[2] is None
        assert [r.url for r in store.search("indexed")] == ["https://good.example/"]
        assert store.saved_copy("https://good.example/").html


@pytest.mark.skipif(
    sqlite3.sqlite_version_info < (3, 35), reason="ALTER TABLE ... DROP COLUMN needs SQLite 3.35"
)
def test_migration_adds_the_validator_columns(tmp_path):
    path = tmp_path / "db.sqlite"
    Store(path).close()
    db = sqlite3.connect(path)
    db.executescript(
        "ALTER TABLE bookmarks DROP COLUMN etag;"
        "ALTER TABLE bookmarks DROP COLUMN last_modified;"
        "PRAGMA user_version = 6;"
    )
    db.close()
    with Store(path) as store:
        assert store.db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        columns = {row[1] for row in store.db.execute("PRAGMA table_info(bookmarks)")}
        assert {"etag", "last_modified"} <= columns


# --- the command -----------------------------------------------------------------


def test_fetch_if_changed_skips_unchanged_pages(base_url, tmp_path, capsys):
    path = str(tmp_path / "cli.sqlite")
    marks = tmp_path / "marks.html"
    marks.write_text(f'<DT><A HREF="{base_url}/etag">Notes</A>', encoding="utf-8")
    assert cli.main(["--db", path, "import", str(marks)]) == 0
    assert cli.main(["--db", path, "fetch"]) == 0
    assert "fetched 1, failed 0" in capsys.readouterr().out

    assert cli.main(["--db", path, "fetch", "--all", "--if-changed"]) == 0
    out = capsys.readouterr().out
    assert "unchanged" in out and "fetched 0, unchanged 1, failed 0" in out
    with Store(path, readonly=True) as store:
        assert [r.url for r in store.search("first")] == [base_url + "/etag"]


def test_fetch_all_without_the_flag_downloads_again(base_url, tmp_path, capsys):
    path = str(tmp_path / "cli2.sqlite")
    marks = tmp_path / "marks.html"
    marks.write_text(f'<DT><A HREF="{base_url}/etag">Notes</A>', encoding="utf-8")
    cli.main(["--db", path, "import", str(marks)])
    cli.main(["--db", path, "fetch"])
    capsys.readouterr()
    assert cli.main(["--db", path, "fetch", "--all"]) == 0
    assert "fetched 1, failed 0" in capsys.readouterr().out
    assert seen[-1][1] is None  # no If-None-Match was sent


def test_fetch_if_changed_reindexes_a_changed_page(base_url, tmp_path, capsys):
    global changed
    path = str(tmp_path / "cli3.sqlite")
    marks = tmp_path / "marks.html"
    marks.write_text(f'<DT><A HREF="{base_url}/etag">Notes</A>', encoding="utf-8")
    cli.main(["--db", path, "import", str(marks)])
    cli.main(["--db", path, "fetch"])
    capsys.readouterr()

    changed = True
    assert cli.main(["--db", path, "fetch", "--all", "--if-changed"]) == 0
    assert "fetched 1, failed 0" in capsys.readouterr().out
    with Store(path, readonly=True) as store:
        assert [r.url for r in store.search("second")] == [base_url + "/etag"]
        assert store.search("first") == []
