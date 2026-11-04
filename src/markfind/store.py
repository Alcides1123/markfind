"""SQLite storage and FTS5 full-text index for bookmarks."""

from __future__ import annotations

import math
import re
import sqlite3
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .duplicates import Group, find_duplicates
from .fetch import EMPTY_MESSAGE
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
    error_kind TEXT,  -- one of fetch.ERROR_KINDS when fetch_error is set
    source TEXT  -- file the bookmark was last imported from, for pruning
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


def _add_source(db: sqlite3.Connection) -> None:
    columns = {row[1] for row in db.execute("PRAGMA table_info(bookmarks)")}
    if "source" not in columns:
        db.execute("ALTER TABLE bookmarks ADD COLUMN source TEXT")


def _mark_empty_pages(db: sqlite3.Connection) -> None:
    # Pages fetched with no text used to count as indexed; the "empty" kind
    # is what fetch records for them now.
    db.execute(
        """
        UPDATE bookmarks SET fetch_error = ?, error_kind = 'empty'
        WHERE fetched_at IS NOT NULL AND fetch_error IS NULL
          AND id IN (SELECT rowid FROM bookmarks_fts WHERE trim(coalesce(body, '')) = '')
        """,
        (EMPTY_MESSAGE,),
    )


# Applied in order to databases whose PRAGMA user_version is lower than their
# position + 1; SCHEMA always describes the latest version, so every migration
# must also be a no-op on a freshly created database.
MIGRATIONS = [_add_error_kind, _add_source, _mark_empty_pages]
SCHEMA_VERSION = len(MIGRATIONS)

# bm25() weights, in bookmarks_fts column order: title, url, body, folder.
RANK_WEIGHTS = (5.0, 1.0, 2.0, 1.0)

# Must match the bookmarks_fts tokenizer, so words stem the same way.
_TOKENIZER = "porter unicode61 remove_diacritics 2"

# similar(): words of 3+ letters or digits that start with a letter.
_WORD = re.compile(r"[^\W\d_][^\W_]{2,}")
SIMILAR_KEYWORDS = 15
_SIMILAR_CANDIDATES = 300  # most frequent stems whose rarity is looked up
_TITLE_WEIGHT = 3  # a word in the title counts as this many in the body
# IDF alone can't tell that "the" is noise in a small collection.
_STOPWORDS = frozenset(
    """
    about above after again against all also and any are because been before being
    below between both but can could did does doing down during each few for from
    further had has have having her here hers herself him himself his how into its
    itself just more most myself nor not now off once only other our ours ourselves
    out over own same she should some such than that the their theirs them
    themselves then there these they this those through too under until very was
    were what when where which while who whom why will with would you your yours
    yourself yourselves
    """.split()
)


class QueryError(Exception):
    """The search query is not valid FTS5 syntax."""


@dataclass(frozen=True)
class SearchResult:
    url: str
    title: str
    folder: str
    snippet: str


@dataclass(frozen=True)
class Similar:
    title: str  # of the bookmark the others are similar to
    keywords: list[str]  # the words the match was based on, most telling first
    results: list[SearchResult]


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


@dataclass(frozen=True)
class Record:
    """A bookmark with its fetch status, for exporting."""

    url: str
    title: str
    folder: str
    added_at: str | None
    fetched_at: str | None
    error_kind: str | None

    @property
    def status(self) -> str:
        """``indexed``, ``pending`` (never fetched), or the failure kind."""
        if self.error_kind:
            return self.error_kind
        return "indexed" if self.fetched_at else "pending"


def _folder_filter(folder: str | None) -> tuple[str, tuple[str, ...]]:
    """A WHERE clause matching a folder and its subfolders, or nothing if no folder."""
    if not folder:
        return "", ()
    escaped = folder.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return "WHERE folder = ? OR folder LIKE ? ESCAPE '\\'", (folder, escaped + "/%")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def to_fts_query(text: str) -> str:
    """Make a plain-words query safe for FTS5.

    Every word is quoted so punctuation like ``-`` or ``:`` is not treated as
    query syntax; FTS5 then matches entries containing all the words.
    """
    return " ".join('"' + word.replace('"', '""') + '"' for word in text.split())


class Store:
    def __init__(self, path: str | Path, readonly: bool = False) -> None:
        """Open (and create or migrate) a database.

        With ``readonly``, the database must already exist at the current
        schema version; it is opened without any writes, so readers such as
        ``markfind serve`` never contend with a running ``markfind fetch``.
        """
        if readonly:
            uri = Path(path).resolve().as_uri() + "?mode=ro"
            self.db = sqlite3.connect(uri, uri=True)
            return
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

    def add_bookmarks(
        self, bookmarks: Iterable[Bookmark], source: str | None = None
    ) -> tuple[int, int]:
        """Insert new bookmarks and refresh existing ones. Returns (added, updated).

        ``source`` names the file they came from, which ``prune`` uses later;
        when it's None, existing bookmarks keep their recorded source.
        """
        added = updated = 0
        with self.db:
            for bm in bookmarks:
                row = self.db.execute(
                    "SELECT id FROM bookmarks WHERE url = ?", (bm.url,)
                ).fetchone()
                if row:
                    id_ = row[0]
                    self.db.execute(
                        """
                        UPDATE bookmarks SET title = ?, folder = ?, source = coalesce(?, source)
                        WHERE id = ?
                        """,
                        (bm.title, bm.folder, source, id_),
                    )
                    updated += 1
                else:
                    cursor = self.db.execute(
                        """
                        INSERT INTO bookmarks (url, title, folder, added_at, source)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            bm.url,
                            bm.title,
                            bm.folder,
                            bm.added_at.isoformat() if bm.added_at else None,
                            source,
                        ),
                    )
                    id_ = cursor.lastrowid
                    added += 1
                self._reindex(id_)
        return added, updated

    def prune(self, source: str, keep: Iterable[str]) -> list[tuple[str, str]]:
        """Delete bookmarks last imported from ``source`` whose URL isn't in ``keep``.

        Returns the removed (url, title) pairs. Bookmarks from other sources,
        or with no recorded source, are never touched.
        """
        keep = set(keep)
        rows = self.db.execute(
            "SELECT id, url, title FROM bookmarks WHERE source = ? ORDER BY folder, title, url",
            (source,),
        ).fetchall()
        gone = [(id_, url, title) for id_, url, title in rows if url not in keep]
        with self.db:
            for id_, _, _ in gone:
                self.db.execute("DELETE FROM bookmarks WHERE id = ?", (id_,))
                self.db.execute("DELETE FROM bookmarks_fts WHERE rowid = ?", (id_,))
        return [(url, title) for _, url, title in gone]

    def _reindex(self, id_: int, body: str | None = None) -> None:
        if body is None:
            row = self.db.execute(
                "SELECT body FROM bookmarks_fts WHERE rowid = ?", (id_,)
            ).fetchone()
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
        self,
        query: str,
        limit: int = 20,
        mark: tuple[str, str] = ("[", "]"),
        exclude: int | None = None,
    ) -> list[SearchResult]:
        """Run an FTS5 query, best matches first, leaving out bookmark id ``exclude``."""
        if not query.strip():
            return []
        try:
            rows = self.db.execute(
                """
                SELECT b.url, b.title, b.folder,
                       snippet(bookmarks_fts, 2, ?, ?, '...', 16)
                FROM bookmarks_fts
                JOIN bookmarks AS b ON b.id = bookmarks_fts.rowid
                WHERE bookmarks_fts MATCH ? AND bookmarks_fts.rowid IS NOT ?
                ORDER BY bm25(bookmarks_fts, ?, ?, ?, ?)
                LIMIT ?
                """,
                (*mark, query, exclude, *RANK_WEIGHTS, limit),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            message = str(exc)
            if "fts5" in message or "syntax error" in message or "no such column" in message:
                raise QueryError(message) from exc
            raise
        return [SearchResult(*row) for row in rows]

    def similar(self, url: str, limit: int = 10, mark: tuple[str, str] = ("[", "]")) -> Similar:
        """Bookmarks most like the one with this URL: "more like this".

        Raises KeyError if the URL isn't in the database.
        """
        row = self.db.execute(
            """
            SELECT b.id, b.title, f.body FROM bookmarks AS b
            LEFT JOIN bookmarks_fts AS f ON f.rowid = b.id
            WHERE b.url = ?
            """,
            (url,),
        ).fetchone()
        if row is None:
            raise KeyError(url)
        id_, title, body = row
        keywords = self._keywords(title, body or "")
        if not keywords:
            return Similar(title, [], [])
        query = " OR ".join(to_fts_query(word) for word in keywords)
        return Similar(title, keywords, self.search(query, limit=limit, mark=mark, exclude=id_))

    def _keywords(self, title: str, body: str) -> list[str]:
        """The words that best characterise a page, by TF-IDF over the index."""
        weights: Counter[str] = Counter()
        for word in _WORD.findall(title.lower()):
            weights[word] += _TITLE_WEIGHT
        for word in _WORD.findall(body.lower()):
            weights[word] += 1
        for word in _STOPWORDS & weights.keys():
            del weights[word]
        if not weights:
            return []

        # Group words by stem (index, indexes, indexing), using the index's
        # own tokenizer, and name each group by its most frequent word.
        groups: dict[str, list[str]] = {}
        for stem, word in self._stem(list(weights)):
            groups.setdefault(stem, []).append(word)
        stems = sorted(groups, key=lambda stem: -sum(weights[w] for w in groups[stem]))

        self.db.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS temp.index_vocab"
            " USING fts5vocab(main, bookmarks_fts, row)"
        )
        count = self.db.execute("SELECT count(*) FROM bookmarks").fetchone()[0]
        scored = []
        # Each lookup costs time, and a word used once or twice on a long page
        # rarely outscores its frequent words, so only the most frequent count.
        for stem in stems[:_SIMILAR_CANDIDATES]:
            row = self.db.execute(
                "SELECT doc FROM temp.index_vocab WHERE term = ?", (stem,)
            ).fetchone()
            docs = row[0] if row else 0
            # In one other bookmark at least, or it can't match anything;
            # in every bookmark, and it can't tell them apart.
            if 2 <= docs < count:
                words = groups[stem]
                weight = sum(weights[w] for w in words)
                word = min(words, key=lambda w: (-weights[w], w))
                scored.append((weight * math.log(count / docs), word))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [word for _, word in scored[:SIMILAR_KEYWORDS]]

    def _stem(self, words: list[str]) -> list[tuple[str, str]]:
        """(stem, word) pairs, stemmed exactly as the index stems them."""
        self.db.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS temp.stem_words"
            f" USING fts5(word, tokenize = '{_TOKENIZER}')"
        )
        self.db.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS temp.stem_vocab"
            " USING fts5vocab(stem_words, instance)"
        )
        with self.db:
            self.db.execute("DELETE FROM temp.stem_words")
            self.db.executemany(
                "INSERT INTO temp.stem_words (rowid, word) VALUES (?, ?)", enumerate(words)
            )
            rows = self.db.execute(
                "SELECT doc, term FROM temp.stem_vocab WHERE offset = 0"
            ).fetchall()
        return [(term, words[doc]) for doc, term in rows]

    def list_bookmarks(self, folder: str | None = None) -> list[tuple[str, str, str]]:
        """(url, title, folder) rows, optionally limited to a folder and its subfolders."""
        where, params = _folder_filter(folder)
        sql = f"SELECT url, title, folder FROM bookmarks {where} ORDER BY folder, title"
        return self.db.execute(sql, params).fetchall()

    def records(self, folder: str | None = None) -> list[Record]:
        """Every bookmark, or one folder and its subfolders, ordered by folder and title."""
        where, params = _folder_filter(folder)
        rows = self.db.execute(
            f"""
            SELECT url, title, folder, added_at, fetched_at,
                   CASE WHEN fetch_error IS NOT NULL THEN coalesce(error_kind, 'other') END
            FROM bookmarks {where}
            ORDER BY folder, title, url
            """,
            params,
        )
        return [Record(*row) for row in rows]

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

    def pages(self) -> list[tuple[str, str, str, str]]:
        """(url, title, folder, page text) for every bookmark; text is '' if not fetched."""
        return self.db.execute(
            """
            SELECT b.url, b.title, b.folder, coalesce(f.body, '')
            FROM bookmarks AS b LEFT JOIN bookmarks_fts AS f ON f.rowid = b.id
            ORDER BY b.id
            """
        ).fetchall()

    def duplicates(self, by: tuple[str, ...] = ("url", "text")) -> list[Group]:
        """Bookmarks saved more than once; see ``markfind.duplicates``."""
        return find_duplicates(self.pages(), by=by)
