"""SQLite storage and FTS5 full-text index for bookmarks."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .importers import Bookmark

SCHEMA = """
CREATE TABLE IF NOT EXISTS bookmarks (
    id INTEGER PRIMARY KEY,
    url TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL DEFAULT '',
    folder TEXT NOT NULL DEFAULT '',
    added_at TEXT,
    fetched_at TEXT,
    fetch_error TEXT,
    error_kind TEXT  -- one of fetch.ERROR_KINDS when fetch_error is set
);

-- rowid matches bookmarks.id
CREATE VIRTUAL TABLE IF NOT EXISTS bookmarks_fts USING fts5(
    title,
    url,
    body,
    folder,
    tokenize = 'porter unicode61 remove_diacritics 2'
);
"""


def _add_error_kind(db: sqlite3.Connection) -> None:
    columns = {row[1] for row in db.execute("PRAGMA table_info(bookmarks)")}
    if "error_kind" not in columns:
        db.execute("ALTER TABLE bookmarks ADD COLUMN error_kind TEXT")
        db.execute("UPDATE bookmarks SET error_kind = 'other' WHERE fetch_error IS NOT NULL")


# Applied in order to databases whose PRAGMA user_version is lower than their
# position + 1; SCHEMA always describes the latest version, so every migration
# must also be a no-op on a freshly created database.
MIGRATIONS = [_add_error_kind]
SCHEMA_VERSION = len(MIGRATIONS)

# bm25() weights, in bookmarks_fts column order: title, url, body, folder.
RANK_WEIGHTS = (5.0, 1.0, 2.0, 1.0)


class QueryError(Exception):
    """The search query is not valid FTS5 syntax."""


@dataclass(frozen=True)
class SearchResult:
    url: str
    title: str
    folder: str
    snippet: str


@dataclass(frozen=True)
class Failure:
    url: str
    title: str
    folder: str
    kind: str
    error: str
    fetched_at: str


@dataclass(frozen=True)
class Stats:
    total: int
    fetched: int
    failed: int
    failed_by_kind: dict[str, int]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def to_fts_query(text: str) -> str:
    """Make a plain-words query safe for FTS5.

    Every word is quoted so punctuation like ``-`` or ``:`` is not treated as
    query syntax; FTS5 then matches entries containing all the words.
    """
    return " ".join('"' + word.replace('"', '""') + '"' for word in text.split())


class Store:
    def __init__(self, path: str | Path) -> None:
        self.db = sqlite3.connect(str(path))
        self.db.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError(
                f"database schema version {version} is newer than this markfind "
                f"supports ({SCHEMA_VERSION}); upgrade markfind"
            )
        with self.db:
            for migration in MIGRATIONS[version:]:
                migration(self.db)
            self.db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def add_bookmarks(self, bookmarks: Iterable[Bookmark]) -> tuple[int, int]:
        """Insert new bookmarks and refresh existing ones. Returns (added, updated)."""
        added = updated = 0
        with self.db:
            for bm in bookmarks:
                row = self.db.execute("SELECT id FROM bookmarks WHERE url = ?", (bm.url,)).fetchone()
                if row:
                    id_ = row[0]
                    self.db.execute(
                        "UPDATE bookmarks SET title = ?, folder = ? WHERE id = ?",
                        (bm.title, bm.folder, id_),
                    )
                    updated += 1
                else:
                    cursor = self.db.execute(
                        "INSERT INTO bookmarks (url, title, folder, added_at) VALUES (?, ?, ?, ?)",
                        (bm.url, bm.title, bm.folder, bm.added_at.isoformat() if bm.added_at else None),
                    )
                    id_ = cursor.lastrowid
                    added += 1
                self._reindex(id_)
        return added, updated

    def _reindex(self, id_: int, body: str | None = None) -> None:
        if body is None:
            row = self.db.execute("SELECT body FROM bookmarks_fts WHERE rowid = ?", (id_,)).fetchone()
            body = row[0] if row else ""
        self.db.execute("DELETE FROM bookmarks_fts WHERE rowid = ?", (id_,))
        self.db.execute(
            """
            INSERT INTO bookmarks_fts (rowid, title, url, body, folder)
            SELECT id, title, url, ?, folder FROM bookmarks WHERE id = ?
            """,
            (body, id_),
        )

    def pending(
        self,
        refetch: bool = False,
        older_than: timedelta | None = None,
        failed: bool = False,
    ) -> list[tuple[int, str]]:
        """Bookmarks whose pages need downloading.

        Never-fetched bookmarks are always included. ``refetch`` adds every
        other bookmark; ``older_than`` adds ones fetched longer ago than that;
        ``failed`` adds ones whose last fetch failed.
        """
        conditions = ["fetched_at IS NULL"]
        params: list[str] = []
        if refetch:
            conditions.append("1")
        if older_than is not None:
            cutoff = datetime.now(timezone.utc) - older_than
            # Timestamps share one ISO format, so they compare correctly as text.
            conditions.append("fetched_at < ?")
            params.append(cutoff.isoformat(timespec="seconds"))
        if failed:
            conditions.append("fetch_error IS NOT NULL")
        sql = f"SELECT id, url FROM bookmarks WHERE {' OR '.join(conditions)} ORDER BY id"
        return self.db.execute(sql, params).fetchall()

    def set_page(self, id_: int, text: str, page_title: str = "") -> None:
        with self.db:
            self.db.execute(
                """
                UPDATE bookmarks
                SET fetched_at = ?, fetch_error = NULL, error_kind = NULL,
                    title = CASE WHEN title = '' THEN ? ELSE title END
                WHERE id = ?
                """,
                (_now(), page_title, id_),
            )
            self._reindex(id_, text)

    def set_error(self, id_: int, message: str, kind: str = "other") -> None:
        with self.db:
            self.db.execute(
                "UPDATE bookmarks SET fetched_at = ?, fetch_error = ?, error_kind = ? WHERE id = ?",
                (_now(), message, kind, id_),
            )

    def failures(self, kinds: Iterable[str] | None = None) -> list[Failure]:
        """Bookmarks whose last fetch failed, optionally only some error kinds."""
        sql = """
            SELECT url, title, folder, error_kind, fetch_error, fetched_at
            FROM bookmarks WHERE fetch_error IS NOT NULL
        """
        params: list[str] = []
        if kinds is not None:
            params = list(kinds)
            sql += f" AND error_kind IN ({', '.join('?' * len(params))})"
        sql += " ORDER BY error_kind, folder, title, url"
        return [Failure(*row) for row in self.db.execute(sql, params)]

    def search(
        self, query: str, limit: int = 20, mark: tuple[str, str] = ("[", "]")
    ) -> list[SearchResult]:
        if not query.strip():
            return []
        try:
            rows = self.db.execute(
                """
                SELECT b.url, b.title, b.folder,
                       snippet(bookmarks_fts, 2, ?, ?, '...', 16)
                FROM bookmarks_fts
                JOIN bookmarks AS b ON b.id = bookmarks_fts.rowid
                WHERE bookmarks_fts MATCH ?
                ORDER BY bm25(bookmarks_fts, ?, ?, ?, ?)
                LIMIT ?
                """,
                (*mark, query, *RANK_WEIGHTS, limit),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            message = str(exc)
            if "fts5" in message or "syntax error" in message or "no such column" in message:
                raise QueryError(message) from exc
            raise
        return [SearchResult(*row) for row in rows]

    def list_bookmarks(self, folder: str | None = None) -> list[tuple[str, str, str]]:
        """(url, title, folder) rows, optionally limited to a folder and its subfolders."""
        sql = "SELECT url, title, folder FROM bookmarks"
        params: tuple[str, ...] = ()
        if folder:
            sql += " WHERE folder = ? OR folder LIKE ? ESCAPE '\\'"
            escaped = folder.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            params = (folder, escaped + "/%")
        return self.db.execute(sql + " ORDER BY folder, title", params).fetchall()

    def stats(self) -> Stats:
        row = self.db.execute(
            """
            SELECT count(*),
                   count(fetched_at) - count(fetch_error),
                   count(fetch_error)
            FROM bookmarks
            """
        ).fetchone()
        by_kind = self.db.execute(
            """
            SELECT error_kind, count(*) FROM bookmarks
            WHERE fetch_error IS NOT NULL
            GROUP BY error_kind ORDER BY count(*) DESC, error_kind
            """
        ).fetchall()
        return Stats(*row, failed_by_kind=dict(by_kind))
