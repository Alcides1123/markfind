"""Fixtures for the browser tests: a markfind server with known data, and a browser page.

These tests drive the real web interface (web/app.js) in a browser with
Playwright. They're kept out of the main test run, which needs nothing beyond
pytest; see docs/development.md for how to run them.
"""

from __future__ import annotations

import os
import threading

import pytest

sync_api = pytest.importorskip("playwright.sync_api", reason="needs: pip install -e '.[browser]'")

from markfind.importers import Bookmark  # noqa: E402
from markfind.server import MarkfindServer  # noqa: E402
from markfind.store import Store  # noqa: E402

FTS = "https://sqlite.org/fts5.html"
BM25 = "https://en.wikipedia.org/wiki/Okapi_BM25"
PYDOCS = "https://docs.python.org/3/library/sqlite3.html"
GONE = "https://gone.example/article"
EVIL = "https://evil.example/"

BOOKMARKS = [
    Bookmark(FTS, "SQLite FTS5 Extension", "Dev/Databases", tags=("sqlite", "search")),
    Bookmark(BM25, "Okapi BM25", "Reading", tags=("search", "ranking")),
    Bookmark(PYDOCS, "sqlite3 module", "Dev", tags=("python", "sqlite")),
    Bookmark(GONE, "A vanished article", "Reading", tags=()),
    Bookmark(EVIL, '<img src=x onerror="window.pwned=1">Evil title', "Dev", tags=()),
]

TEXT = {
    FTS: "full-text search engine with bm25 ranking for sqlite databases",
    BM25: "bm25 is a ranking function used by search engines to score documents",
    PYDOCS: "the sqlite3 module provides an interface to sqlite databases from python",
    GONE: "an article about link rot and why bookmarks disappear over time",
    EVIL: "<script>window.pwned=1</script> ranking text from a hostile page",
}


def build_db(path, bookmarks=BOOKMARKS):
    with Store(path) as store:
        store.add_bookmarks(bookmarks)
        for id_, url in store.pending():
            html = f"<h2>Saved</h2><p>{TEXT[url]}</p>" if url == GONE else ""
            store.set_page(id_, TEXT[url], html=html)
        if any(b.url == GONE for b in bookmarks):
            store.set_note(FTS, "read the ranking section first")
            store.set_error(store._id(GONE), "HTTP 404 Not Found", "gone")


def serve(path):
    server = MarkfindServer(path, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02})
    thread.daemon = True
    thread.start()
    return server


@pytest.fixture(scope="session")
def browser():
    # MARKFIND_BROWSER_CHANNEL picks an installed browser instead of
    # Playwright's own Chromium, e.g. "msedge" or "chrome".
    channel = os.environ.get("MARKFIND_BROWSER_CHANNEL") or None
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=channel)
        yield browser
        browser.close()


@pytest.fixture(scope="session")
def server(tmp_path_factory):
    path = tmp_path_factory.mktemp("db") / "markfind.sqlite"
    build_db(path)
    server = serve(path)
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture
def context(browser):
    context = browser.new_context()
    # Result links point at real sites; never let the tests leave this machine.
    context.route(
        "http*://**",
        lambda route: (
            route.continue_()
            if route.request.url.startswith("http://127.0.0.1:")
            else route.fulfill(status=200, content_type="text/html", body="<p>external page</p>")
        ),
    )
    yield context
    context.close()


@pytest.fixture
def page(context, server):
    page = context.new_page()
    page.dialogs = []
    page.on("dialog", lambda dialog: (page.dialogs.append(dialog.message), dialog.dismiss()))
    page.base = server.url
    yield page
