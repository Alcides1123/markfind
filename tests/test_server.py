import http.client
import json
import threading

import pytest

from markfind import cli
from markfind.importers import Bookmark
from markfind.server import MARK, MarkfindServer, is_loopback
from markfind.store import Store


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "db.sqlite"
    with Store(path) as store:
        store.add_bookmarks(
            [
                Bookmark("https://sqlite.org/fts5.html", "SQLite FTS5", "Dev"),
                Bookmark("https://evil.example/", "<img src=x onerror=alert(1)>", "Dev"),
                Bookmark("https://gone.example/", "Gone page", "Old"),
                Bookmark("https://slow.example/", "Slow page", "Old"),
                Bookmark("https://pending.example/", "Not fetched yet"),
            ]
        )
        ids = {url: id_ for id_, url in store.pending()}
        store.set_page(ids["https://sqlite.org/fts5.html"], "full-text search with bm25 ranking")
        store.set_page(ids["https://evil.example/"], "<script>alert('search')</script> ranking")
        store.set_error(ids["https://gone.example/"], "HTTP 404 Not Found", "gone")
        store.set_error(ids["https://slow.example/"], "timed out", "timeout")
    return path


@pytest.fixture
def server(db_path):
    srv = MarkfindServer(db_path, "127.0.0.1", 0)
    # A short poll interval makes shutdown() quick between tests.
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def request(server, path, host=None):
    conn = http.client.HTTPConnection(*server.server_address[:2], timeout=5)
    headers = {"Host": host} if host is not None else {}
    conn.request("GET", path, headers=headers)
    response = conn.getresponse()
    body = response.read()
    conn.close()
    return response, body


def get_json(server, path, **kwargs):
    response, body = request(server, path, **kwargs)
    return response.status, json.loads(body)


def test_search(server):
    status, data = get_json(server, "/api/search?q=ranking")
    assert status == 200
    assert data["query"] == "ranking"
    urls = [r["url"] for r in data["results"]]
    assert set(urls) == {"https://sqlite.org/fts5.html", "https://evil.example/"}

    (fts,) = [r for r in data["results"] if r["url"] == "https://sqlite.org/fts5.html"]
    assert fts["title"] == "SQLite FTS5"
    assert fts["folder"] == "Dev"
    assert f"{MARK[0]}ranking{MARK[1]}" in fts["snippet"]


def test_search_returns_page_text_as_data_not_html(server):
    # Untrusted text is passed through verbatim in JSON; the page inserts it
    # with textContent. No HTML is ever generated server-side.
    _, data = get_json(server, "/api/search?q=alert")
    (hit,) = data["results"]
    assert hit["title"] == "<img src=x onerror=alert(1)>"
    assert "<script>" in hit["snippet"]


def test_search_options_and_errors(server):
    assert get_json(server, "/api/search?q=")[1] == {"query": "", "results": []}
    assert get_json(server, "/api/search")[1]["results"] == []
    assert len(get_json(server, "/api/search?q=ranking&limit=1")[1]["results"]) == 1
    assert len(get_json(server, "/api/search?q=ranking&limit=oops")[1]["results"]) == 2

    # Plain mode quotes punctuation; raw mode passes FTS5 syntax through.
    assert get_json(server, "/api/search?q=full-text")[0] == 200
    status, data = get_json(server, "/api/search?q=title%3Asqlite&raw=1")
    assert [r["url"] for r in data["results"]] == ["https://sqlite.org/fts5.html"]

    status, data = get_json(server, "/api/search?q=ranking+AND&raw=1")
    assert status == 400
    assert data["error"].startswith("invalid query")


def test_similar(server):
    status, data = get_json(server, "/api/similar?url=https%3A%2F%2Fsqlite.org%2Ffts5.html")
    assert status == 200
    assert data["url"] == "https://sqlite.org/fts5.html"
    assert data["title"] == "SQLite FTS5"
    # "ranking" and "search" are the words the two indexed pages share.
    assert set(data["keywords"]) == {"ranking", "search"}
    # The bookmark itself is left out; the other page is returned as data, with marks.
    (hit,) = data["results"]
    assert hit["url"] == "https://evil.example/"
    assert hit["title"] == "<img src=x onerror=alert(1)>"
    assert f"{MARK[0]}ranking{MARK[1]}" in hit["snippet"]


def test_similar_errors_and_limit(server):
    status, data = get_json(server, "/api/similar")
    assert (status, data) == (400, {"error": "missing url"})
    status, data = get_json(server, "/api/similar?url=https%3A%2F%2Fmissing.example%2F")
    assert status == 404 and "isn't in markfind" in data["error"]
    # Nothing in common with any other bookmark: an empty answer, not an error.
    status, data = get_json(server, "/api/similar?url=https%3A%2F%2Fpending.example%2F")
    assert status == 200 and data["results"] == [] and data["keywords"] == []
    _, data = get_json(server, "/api/similar?url=https%3A%2F%2Fsqlite.org%2Ffts5.html&limit=0")
    assert len(data["results"]) == 1  # limit is clamped to at least 1


def test_broken_and_stats(server):
    status, data = get_json(server, "/api/broken")
    assert status == 200
    assert [f["url"] for f in data["failures"]] == ["https://gone.example/"]
    assert data["failures"][0]["kind"] == "gone"
    assert data["failures"][0]["error"] == "HTTP 404 Not Found"

    _, data = get_json(server, "/api/broken?all=1")
    assert {f["kind"] for f in data["failures"]} == {"gone", "timeout"}

    _, stats = get_json(server, "/api/stats")
    assert stats == {
        "total": 5,
        "indexed": 2,
        "failed": 2,
        "pending": 1,
        "failed_by_kind": {"gone": 1, "timeout": 1},
    }


@pytest.mark.parametrize(
    "path, ctype",
    [("/", "text/html"), ("/app.js", "text/javascript"), ("/style.css", "text/css")],
)
def test_static_files_and_security_headers(server, path, ctype):
    response, body = request(server, path)
    assert response.status == 200
    assert response.getheader("Content-Type").startswith(ctype)
    assert body
    csp = response.getheader("Content-Security-Policy")
    assert "default-src 'none'" in csp and "script-src 'self'" in csp
    assert "unsafe-inline" not in csp
    assert response.getheader("X-Content-Type-Options") == "nosniff"
    assert response.getheader("Referrer-Policy") == "no-referrer"


def test_page_has_no_inline_scripts(server):
    _, body = request(server, "/")
    html = body.decode()
    assert '<script src="/app.js"' in html
    assert html.count("<script") == 1


@pytest.mark.parametrize(
    "path",
    [
        "/nope",
        "/web/app.js",
        "/../server.py",
        "/%2e%2e/server.py",
        "/app.js/../cli.py",
        "/API/stats",
    ],
)
def test_unknown_paths_are_404(server, path):
    response, _ = request(server, path)
    assert response.status == 404


@pytest.mark.parametrize(
    "host", ["evil.example", "evil.example:8737", "127.0.0.1.evil.example", ""]
)
def test_foreign_host_header_is_rejected(server, host):
    response, _ = request(server, "/api/stats", host=host)
    assert response.status == 403


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1:8737", "localhost:1234", "[::1]:8737"])
def test_local_host_headers_are_allowed(server, host):
    response, _ = request(server, "/api/stats", host=host)
    assert response.status == 200


def test_post_is_not_supported(server):
    conn = http.client.HTTPConnection(*server.server_address[:2], timeout=5)
    conn.request("POST", "/api/search", body=b"q=x")
    assert conn.getresponse().status == 501
    conn.close()


def test_server_never_writes_to_the_database(server, db_path):
    before = db_path.read_bytes()
    for path in [
        "/api/search?q=ranking",
        "/api/similar?url=https%3A%2F%2Fsqlite.org%2Ffts5.html",
        "/api/broken?all=1",
        "/api/stats",
    ]:
        request(server, path)
    assert db_path.read_bytes() == before


def test_non_loopback_bind_skips_host_check(db_path):
    srv = MarkfindServer(db_path, "127.0.0.1", 0)
    srv.bind_host = "0.0.0.0"  # as if started with --host 0.0.0.0
    try:
        assert srv.host_allowed("my-laptop.lan:8737")
    finally:
        srv.server_close()


def test_is_loopback():
    assert is_loopback("127.0.0.1") and is_loopback("::1") and is_loopback("localhost")
    assert not is_loopback("0.0.0.0") and not is_loopback("192.168.1.5")
    assert not is_loopback("example.com")


def test_cli_serve(db_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli, "serve", lambda *args, **kwargs: calls.append((args, kwargs)))
    assert cli.main(["--db", str(db_path), "serve", "--port", "9999", "--open"]) == 0
    assert calls == [((db_path,), {"host": "127.0.0.1", "port": 9999, "open_browser": True})]


def test_cli_serve_port_in_use(db_path, server, capsys):
    port = server.server_address[1]
    assert cli.main(["--db", str(db_path), "serve", "--port", str(port)]) == 1
    assert "could not start server" in capsys.readouterr().err
