import socket
import sqlite3
import ssl
import urllib.error

import pytest

from markfind.fetch import ERROR_KINDS, FetchError, UnsupportedContentError, classify_error
from markfind.importers import Bookmark
from markfind.store import SCHEMA_VERSION, Store


def http(code, reason="Reason"):
    return urllib.error.HTTPError("https://x.example/", code, reason, {}, None)


@pytest.mark.parametrize(
    "exc, kind, message",
    [
        (http(404, "Not Found"), "gone", "HTTP 404 Not Found"),
        (http(410, "Gone"), "gone", "HTTP 410 Gone"),
        (http(403, "Forbidden"), "blocked", "HTTP 403 Forbidden"),
        (http(401), "blocked", "HTTP 401 Reason"),
        (http(429), "server", "HTTP 429 Reason"),
        (http(503), "server", "HTTP 503 Reason"),
        (http(418), "http", "HTTP 418 Reason"),
        (
            urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed")),
            "dns",
            "host not found",
        ),
        (socket.gaierror(-2, "Name or service not known"), "dns", "host not found"),
        (urllib.error.URLError(TimeoutError()), "timeout", "timed out"),
        (TimeoutError("read timed out"), "timeout", "timed out"),
        (urllib.error.URLError("timed out"), "timeout", "timed out"),
        (
            urllib.error.URLError(ConnectionRefusedError()),
            "connection",
            "connection failed: ConnectionRefusedError",
        ),
        (ConnectionResetError(), "connection", "connection failed: ConnectionResetError"),
        (
            UnsupportedContentError("unsupported content type: image/png"),
            "unsupported",
            "unsupported content type: image/png",
        ),
        (FetchError("weird"), "other", "FetchError: weird"),
        (ValueError("unknown url type"), "other", "ValueError: unknown url type"),
    ],
)
def test_classify_error(exc, kind, message):
    assert classify_error(exc) == (kind, message)
    assert kind in ERROR_KINDS


def test_classify_ssl_errors():
    kind, message = classify_error(urllib.error.URLError(ssl.SSLCertVerificationError("bad cert")))
    assert kind == "ssl" and message.startswith("certificate verification failed")
    assert classify_error(urllib.error.URLError(ssl.SSLError("handshake")))[0] == "ssl"


def test_migrates_0_1_0_database(tmp_path):
    path = tmp_path / "old.sqlite"
    db = sqlite3.connect(path)
    # The bookmarks table as created by markfind 0.1.0.
    db.executescript(
        """
        CREATE TABLE bookmarks (
            id INTEGER PRIMARY KEY,
            url TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL DEFAULT '',
            folder TEXT NOT NULL DEFAULT '',
            added_at TEXT,
            fetched_at TEXT,
            fetch_error TEXT
        );
        INSERT INTO bookmarks (url, fetched_at, fetch_error)
        VALUES ('https://a.example/', '2026-01-01T00:00:00+00:00', 'URLError: boom'),
               ('https://b.example/', '2026-01-01T00:00:00+00:00', NULL);
        """
    )
    db.commit()
    db.close()

    with Store(path) as store:
        assert store.db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        (failure,) = store.failures()
        assert (failure.url, failure.kind, failure.error) == (
            "https://a.example/",
            "other",
            "URLError: boom",
        )

    # Opening again is a no-op.
    with Store(path) as store:
        assert len(store.failures()) == 1


def test_refuses_newer_database(tmp_path):
    path = tmp_path / "new.sqlite"
    with Store(path) as store:
        store.db.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    with pytest.raises(RuntimeError, match="newer"):
        Store(path)


def test_failures_and_success_clears_kind(tmp_path):
    with Store(tmp_path / "db.sqlite") as store:
        store.add_bookmarks([Bookmark(f"https://{n}.example/") for n in "abc"])
        (a, _), (b, _), (c, _) = store.pending()
        store.set_error(a, "HTTP 404 Not Found", "gone")
        store.set_error(b, "timed out", "timeout")
        store.set_error(c, "host not found", "dns")

        assert [f.kind for f in store.failures()] == ["dns", "gone", "timeout"]
        assert [f.url for f in store.failures(["gone"])] == ["https://a.example/"]
        assert store.failures([]) == []
        assert store.stats().failed_by_kind == {"dns": 1, "gone": 1, "timeout": 1}

        store.set_page(b, "back online")
        assert [f.kind for f in store.failures()] == ["dns", "gone"]
        assert store.stats().failed == 2
