"""Saved copies: a readable copy of each page, kept after the page is gone."""

import base64
import hashlib
import http.client
import json
import re
import sqlite3
import threading
import zlib

import pytest

from markfind import cli
from markfind.archive import STYLE, STYLE_HASH, render
from markfind.extract import Page, extract_text
from markfind.importers import Bookmark
from markfind.server import MarkfindServer
from markfind.store import SCHEMA_VERSION, Store

A = "https://a.example/post"
B = "https://b.example/"
C = "https://c.example/never-fetched"

PROSE = " ".join(f"word{i}" for i in range(40))


def copy_of(body, base_url="https://site.example/blog/post"):
    return extract_text(
        f"<html><head><title>T</title></head><body>{body}</body></html>", base_url
    ).html


# --- making the copy ------------------------------------------------------


def test_structure_is_kept():
    html = copy_of(
        f"<main><h2>Setup</h2><p>{PROSE} <em>really</em></p>"
        "<ul><li>one</li><li>two</li></ul>"
        "<pre>  indented\n    code</pre>"
        '<table><tr><th colspan="2">Head</th></tr><tr><td>a</td><td rowspan="x">b</td></tr></table>'
        '<p>Read <a href="/docs?a=1&amp;b=2">the docs</a>.</p></main>'
    )
    assert "<h2>Setup</h2>" in html
    assert "<em>really</em>" in html
    assert "<ul><li>one</li><li>two</li></ul>" in html
    assert "<pre>  indented\n    code</pre>" in html  # whitespace in <pre> survives
    assert '<th colspan="2">Head</th>' in html and "<td>b</td>" in html
    assert '<a href="https://site.example/docs?a=1&amp;b=2">the docs</a>' in html


def test_nothing_unsafe_gets_through():
    html = copy_of(
        f'<main><p onclick="alert(1)" style="color:red" class=x id=y>{PROSE}</p>'
        "<script>alert(1)</script><style>p{}</style><iframe src=//evil></iframe>"
        '<img src=x onerror="alert(1)"><svg><script>alert(1)</script></svg>'
        '<a href="javascript:alert(1)">js</a> <a href="data:text/html,x">data</a>'
        '<a href=" JAVASCRIPT:alert(1)">js2</a>'
        "<object data=x></object><form><input value=secret></form>"
        "<p>1 &lt; 2 &amp;&amp; &lt;script&gt;</p></main>"
    )
    assert re.findall(r"<[a-z0-9]+\s+([a-z-]+)=", html) == []  # no attributes at all here
    assert "<script" not in html and "alert(1)</" not in html
    assert "<a>js</a>" in html and "<a>data</a>" in html and "<a>js2</a>" in html
    assert "secret" not in html
    # Text that looks like markup stays text.
    assert "<p>1 &lt; 2 &amp;&amp; &lt;script&gt;</p>" in html


def test_copy_is_the_indexed_content():
    menu = " ".join(f'<a href="/m{i}">menu{i}</a>' for i in range(15))
    # With <main>: only that.
    page = extract_text(f"<nav>{menu}</nav><main><p>{PROSE}</p></main><footer>foot</footer>")
    assert page.html == f"<div><p>{PROSE}</p></div>"
    # Without: the block picked by text density, same as the text.
    page = extract_text(f'<div id="top">{menu}</div><div class="post"><p>{PROSE}</p></div>')
    assert "menu0" not in page.text and "menu0" not in page.html
    assert page.html == f"<div><p>{PROSE}</p></div>"


def test_broken_markup_is_balanced():
    html = copy_of(f"<main></li><p><b>{PROSE}<ul><li>x</p></em></main><p>after")
    assert html.count("<b>") == html.count("</b>")
    assert html.count("<ul>") == html.count("</ul>")
    assert "</li></ul>" in html
    assert "</em>" not in html


def test_empty_wrappers_are_dropped():
    html = copy_of(f'<main><div><div><img src="x"></div></div><p>{PROSE}</p><p> </p></main>')
    assert html == f"<div><p>{PROSE}</p></div>"


def test_html_doesnt_affect_page_equality():
    assert extract_text(f"<p>{PROSE}</p>") == Page("", PROSE)


# --- storing it -----------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "db.sqlite") as store:
        store.add_bookmarks([Bookmark(A, "A post"), Bookmark(B, "B"), Bookmark(C, "C")])
        ids = {url: id_ for id_, url in store.pending()}
        store.ids = ids
        store.set_page(ids[A], "the post text", html="<h2>Post</h2><p>the post text</p>")
        store.set_page(ids[B], "old page text")  # fetched before copies existed
        yield store


def test_saved_copy(store):
    copy = store.saved_copy(A)
    assert (copy.title, copy.html, copy.text, copy.text_only) == (
        "A post",
        "<h2>Post</h2><p>the post text</p>",
        "the post text",
        False,
    )
    assert copy.saved_at
    # Stored compressed.
    blob = store.db.execute("SELECT html FROM archive").fetchone()[0]
    assert zlib.decompress(blob).decode() == copy.html


def test_text_only_and_missing_copies(store):
    copy = store.saved_copy(B)
    assert (copy.html, copy.text_only, copy.saved_at) == ("<p>old page text</p>", True, None)
    assert store.saved_copy(C) is None  # never fetched
    with pytest.raises(KeyError):
        store.saved_copy("https://unknown.example/")


def test_failures_keep_the_copy_and_new_fetches_replace_it(store):
    id_ = store.ids[A]
    store.set_error(id_, "HTTP 404 Not Found", "gone")
    assert store.saved_copy(A).html == "<h2>Post</h2><p>the post text</p>"
    assert [(f.url, f.saved) for f in store.failures()] == [(A, True)]

    store.set_page(id_, "new text")  # fetched without a copy: the old one stays
    assert store.saved_copy(A).html == "<h2>Post</h2><p>the post text</p>"
    store.set_page(id_, "new text", html="<p>new text</p>")
    assert store.saved_copy(A).html == "<p>new text</p>"


def test_deleting_deletes_the_copy(store):
    store.delete([A])
    assert store.db.execute("SELECT count(*) FROM archive").fetchone()[0] == 0


def test_saved_flags_and_stats(store):
    assert {r.url: r.saved for r in store.search("text")} == {A: True, B: True}
    assert {r.url: r.saved for r in store.records()} == {A: True, B: True, C: False}
    s = store.stats()
    assert s.saved_copies == 1 and s.saved_bytes > 0


def test_migration_to_version_6(tmp_path):
    path = tmp_path / "db.sqlite"
    Store(path).close()
    db = sqlite3.connect(path)
    db.executescript("DROP TABLE archive; PRAGMA user_version = 5;")
    db.close()
    with Store(path) as store:
        assert store.db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert store.db.execute("SELECT count(*) FROM archive").fetchone()[0] == 0


# --- showing it -----------------------------------------------------------


def test_render():
    page = render(
        "https://x.example/?a=1&b=<2>", 'Title "<x>"', "2026-11-05T10:00:00", "<p>hi</p>", False
    )
    assert 'Saved copy of <a href="https://x.example/?a=1&amp;b=&lt;2&gt;">' in page
    assert "on 2026-11-05" in page and "<p>hi</p>" in page
    assert '<h1 class="title">Title "&lt;x&gt;"</h1>' in page
    # A copy with its own heading doesn't get a second one.
    assert 'class="title"' not in render("u", "T", None, "<h1>Own</h1>", True)
    assert "only its text was kept" in render("u", "T", None, "<p>x</p>", True)
    # The CSP hash covers exactly the stylesheet in the page.
    digest = base64.b64encode(hashlib.sha256(STYLE.encode()).digest()).decode()
    assert STYLE_HASH == f"sha256-{digest}" and f"<style>{STYLE}</style>" in page


def run(capsys, *args):
    code = cli.main(list(args))
    out, err = capsys.readouterr()
    return code, out, err


@pytest.fixture
def db(store):
    path = store.db.execute("PRAGMA database_list").fetchone()[2]
    store.close()
    return path


def test_cli_archive(db, capsys, monkeypatch, tmp_path):
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda uri: opened.append(uri) or True)

    code, out, _ = run(capsys, "--db", db, "archive", A)
    assert code == 0 and "opened the saved copy" in out
    assert opened and opened[0].startswith("file:")
    written = list(tmp_path.glob("markfind-copy-*.html"))
    assert len(written) == 1 and "<h2>Post</h2>" in written[0].read_text(encoding="utf-8")

    target = tmp_path / "out.html"
    code, out, err = run(capsys, "--db", db, "archive", B, "-o", str(target))
    assert code == 0 and out == "" and "written to" in err
    assert "<p>old page text</p>" in target.read_text(encoding="utf-8")

    assert run(capsys, "--db", db, "archive", A, "--text") == (0, "the post text\n", "")
    code, _, err = run(capsys, "--db", db, "archive", C)
    assert code == 1 and "no saved copy" in err
    code, _, err = run(capsys, "--db", db, "archive", "https://unknown.example/")
    assert code == 2 and "is not in markfind" in err


def test_cli_broken_and_stats(tmp_path, capsys):
    path = str(tmp_path / "db.sqlite")
    with Store(path) as store:
        store.add_bookmarks([Bookmark(A, "A post"), Bookmark(B, "B")])
        ids = {url: id_ for id_, url in store.pending()}
        store.set_page(ids[A], "text", html="<p>text</p>")
        store.set_error(ids[A], "HTTP 404 Not Found", "gone")
        store.set_error(ids[B], "HTTP 404 Not Found", "gone")
    _, out, _ = run(capsys, "--db", path, "broken")
    assert "checked " in out and ", saved copy" in out
    assert out.count("saved copy") == 2  # A's line, and the hint
    assert "1 of these has a saved copy; read it with 'markfind archive URL'." in out
    _, out, _ = run(capsys, "--db", path, "stats")
    assert re.search(r"saved copies: 1 \(\d+ B\)", out)


# --- the web server ---------------------------------------------------------


@pytest.fixture
def server(db):
    srv = MarkfindServer(db, "127.0.0.1", 0)
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def get(server, path):
    conn = http.client.HTTPConnection(*server.server_address[:2], timeout=5)
    conn.request("GET", path)
    response = conn.getresponse()
    body = response.read().decode()
    conn.close()
    return response, body


def test_server_archive(server):
    response, body = get(server, "/archive?url=https%3A%2F%2Fa.example%2Fpost")
    assert response.status == 200
    assert response.getheader("Content-Type").startswith("text/html")
    assert "<h2>Post</h2><p>the post text</p>" in body
    csp = response.getheader("Content-Security-Policy")
    assert csp.startswith("sandbox;") and "default-src 'none'" in csp
    assert f"style-src '{STYLE_HASH}'" in csp and "script-src" not in csp
    assert response.getheader("X-Content-Type-Options") == "nosniff"

    for path in (
        "/archive?url=https%3A%2F%2Fc.example%2Fnever-fetched",
        "/archive?url=nope",
        "/archive",
    ):
        response, body = get(server, path)
        assert response.status == 404 and "no saved copy" in body


def test_server_marks_results_with_copies(server):
    _, body = get(server, "/api/search?q=text")
    assert {r["url"]: r["saved"] for r in json.loads(body)["results"]} == {A: True, B: True}
    # The rest of the app keeps its own, stricter policy.
    response, _ = get(server, "/")
    assert "sandbox" not in response.getheader("Content-Security-Policy")
