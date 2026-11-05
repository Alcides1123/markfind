"""Local web interface: ``markfind serve``.

A small read-only web app over the bookmark database: search as you type,
the dead-link report, and stats. It uses only the standard library; the page,
stylesheet and script ship as package data in ``markfind/web``.

Page text comes from arbitrary websites, so it is treated as untrusted: the
API marks snippet matches with control characters instead of HTML, and the
page inserts all such text with ``textContent``.
"""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from . import __version__
from .fetch import DEAD_KINDS
from .store import QueryError, SearchResult, Store, to_fts_query

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8737

# Snippet highlight markers; the page turns them into <mark> elements.
MARK = ("\x02", "\x03")

MAX_LIMIT = 100

_STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}

_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
        "img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}

_LOCAL_NAMES = frozenset({"localhost", "127.0.0.1", "::1"})


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _limit(params: dict[str, str], default: int = 30) -> int:
    """The ``limit`` parameter, clamped to 1..MAX_LIMIT."""
    try:
        return min(max(int(params.get("limit", default)), 1), MAX_LIMIT)
    except ValueError:
        return default


def _result(r: SearchResult) -> dict[str, object]:
    return {
        "url": r.url,
        "title": r.title,
        "folder": r.folder,
        "snippet": r.snippet,
        "tags": list(r.tags),
    }


def _load_static() -> dict[str, tuple[bytes, str]]:
    web = resources.files("markfind") / "web"
    return {path: ((web / name).read_bytes(), ctype) for path, (name, ctype) in _STATIC.items()}


class MarkfindServer(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows, SO_REUSEADDR (HTTPServer's default) lets a second server
    # bind a port that is already in use, so two `markfind serve` processes
    # would silently share it. Ask for exclusive use there instead.
    allow_reuse_address = os.name != "nt"

    def server_bind(self) -> None:
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def __init__(self, db_path: Path, host: str, port: int, quiet: bool = True) -> None:
        if ":" in host:
            self.address_family = socket.AF_INET6
        self.db_path = db_path
        self.bind_host = host
        self.quiet = quiet
        self.static = _load_static()
        super().__init__((host, port), _Handler)

    @property
    def url(self) -> str:
        host, port = self.server_address[:2]
        if host in ("0.0.0.0", "::"):
            host = "localhost"
        elif ":" in str(host):
            host = f"[{host}]"
        return f"http://{host}:{port}/"

    def host_allowed(self, header: str | None) -> bool:
        """Reject foreign Host headers on loopback, to block DNS rebinding.

        A malicious website can point its own domain at 127.0.0.1 and then
        read this server's responses from the victim's browser; the Host
        header still carries that domain, so it gives the attack away.
        """
        if not is_loopback(self.bind_host):
            return True
        if not header:
            return False
        try:
            hostname = urlsplit("//" + header).hostname
        except ValueError:
            return False
        return hostname in _LOCAL_NAMES


class _Handler(BaseHTTPRequestHandler):
    server: MarkfindServer
    server_version = f"markfind/{__version__}"

    def do_GET(self) -> None:  # noqa: N802 - name required by BaseHTTPRequestHandler
        if not self.server.host_allowed(self.headers.get("Host")):
            self._send_json({"error": "forbidden host"}, HTTPStatus.FORBIDDEN)
            return

        url = urlsplit(self.path)
        params = {k: v[-1] for k, v in parse_qs(url.query).items()}
        routes = {
            "/api/search": self._search,
            "/api/similar": self._similar,
            "/api/folders": self._folders,
            "/api/broken": self._broken,
            "/api/stats": self._stats,
        }
        if url.path in routes:
            routes[url.path](params)
        elif url.path in self.server.static:
            body, ctype = self.server.static[url.path]
            self._send(body, ctype)
        else:
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def _store(self) -> Store:
        return Store(self.server.db_path, readonly=True)

    def _search(self, params: dict[str, str]) -> None:
        text = params.get("q", "").strip()
        limit = _limit(params)
        raw = params.get("raw") == "1"
        if not text:
            self._send_json({"query": text, "results": []})
            return
        query = text if raw else to_fts_query(text)
        with self._store() as store:
            try:
                results = store.search(
                    query, limit=limit, mark=MARK, folder=params.get("folder") or None
                )
            except QueryError as exc:
                self._send_json({"error": f"invalid query: {exc}"}, HTTPStatus.BAD_REQUEST)
                return
        self._send_json({"query": text, "results": [_result(r) for r in results]})

    def _folders(self, params: dict[str, str]) -> None:
        with self._store() as store:
            folders = store.folders()
        self._send_json({"folders": [{"path": path, "count": n} for path, n in folders]})

    def _similar(self, params: dict[str, str]) -> None:
        url = params.get("url", "")
        if not url:
            self._send_json({"error": "missing url"}, HTTPStatus.BAD_REQUEST)
            return
        with self._store() as store:
            try:
                found = store.similar(url, limit=_limit(params), mark=MARK)
            except KeyError:
                self._send_json({"error": "this bookmark isn't in markfind"}, HTTPStatus.NOT_FOUND)
                return
        self._send_json(
            {
                "url": url,
                "title": found.title,
                "keywords": found.keywords,
                "results": [_result(r) for r in found.results],
            }
        )

    def _broken(self, params: dict[str, str]) -> None:
        show_all = params.get("all") == "1"
        with self._store() as store:
            failures = store.failures(None if show_all else DEAD_KINDS)
        self._send_json(
            {
                "all": show_all,
                "failures": [
                    {
                        "url": f.url,
                        "title": f.title,
                        "folder": f.folder,
                        "kind": f.kind,
                        "error": f.error,
                        "checked": f.fetched_at,
                    }
                    for f in failures
                ],
            }
        )

    def _stats(self, params: dict[str, str]) -> None:
        with self._store() as store:
            s = store.stats()
        self._send_json(
            {
                "total": s.total,
                "indexed": s.fetched,
                "failed": s.failed,
                "pending": s.total - s.fetched - s.failed,
                "failed_by_kind": s.failed_by_kind,
            }
        )

    def _send_json(self, data: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._send(body, "application/json; charset=utf-8", status)

    def _send(self, body: bytes, ctype: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for name, value in _HEADERS.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        if not self.server.quiet:
            super().log_message(format, *args)


def serve(
    db_path: Path, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, open_browser: bool = False
) -> None:
    """Run the web interface until interrupted with Ctrl+C."""
    server = MarkfindServer(db_path, host, port, quiet=False)
    if not is_loopback(host):
        print(
            f"warning: listening on {host}; anyone who can reach this machine "
            "on that address can search your bookmarks"
        )
    print(f"markfind is serving {db_path} at {server.url} (Ctrl+C to stop)", flush=True)
    if open_browser:
        webbrowser.open(server.url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
