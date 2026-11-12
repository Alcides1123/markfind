"""Read bookmarks exported from browsers.

Four formats are supported:

* The Netscape bookmark HTML file that every major browser can export
  (Chrome, Edge, Firefox, Safari: "Export bookmarks to HTML").
* Chrome / Edge's internal ``Bookmarks`` JSON file from the profile folder.
* Firefox's internal ``places.sqlite`` database from the profile folder.
* Safari's internal ``Bookmarks.plist`` property list.
"""

from __future__ import annotations

import json
import plistlib
import shutil
import sqlite3
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
from xml.parsers.expat import ExpatError

_CHROME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)
_UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_SQLITE_HEADER = b"SQLite format 3\x00"

# Firefox's built-in folders, by guid. The database stores internal names
# ("toolbar", "unfiled", ...); these are the names Firefox shows.
_FIREFOX_ROOTS = {
    "menu________": "Bookmarks Menu",
    "toolbar_____": "Bookmarks Toolbar",
    "unfiled_____": "Other Bookmarks",
    "mobile______": "Mobile Bookmarks",
}
_FIREFOX_ROOT_GUID = "root________"
# Tags are stored as bookmarks inside this hidden folder: one folder per tag,
# holding a copy of each tagged bookmark. They are read as tags, not bookmarks.
_FIREFOX_TAGS_GUID = "tags________"
# Safari's built-in folders: internal title -> the name Safari shows.
_SAFARI_FOLDERS = {
    "BookmarksBar": "Favorites",
    "BookmarksMenu": "Bookmarks Menu",
    "com.apple.ReadingList": "Reading List",
}
_FIREFOX_TYPE_BOOKMARK = 1
_FIREFOX_TYPE_FOLDER = 2


@dataclass(frozen=True)
class Bookmark:
    url: str
    title: str = ""
    folder: str = ""
    added_at: datetime | None = None
    # None when the source can't hold tags, so importing it keeps the tags a
    # bookmark already has; () when the source says it has none.
    tags: tuple[str, ...] | None = None


def is_web_url(url: str) -> bool:
    """Whether markfind can fetch this: an http(s) address, not a bookmarklet or a file."""
    return urlsplit(url).scheme in ("http", "https")


def _clean(text: str) -> str:
    return " ".join(text.split())


def clean_tags(tags: Iterable[str]) -> tuple[str, ...]:
    """Tags without extra spaces, commas, blanks or repeats (ignoring case), in order."""
    seen: set[str] = set()
    result = []
    for tag in tags:
        tag = _clean(tag.replace(",", " "))
        if tag and tag.lower() not in seen:
            seen.add(tag.lower())
            result.append(tag)
    return tuple(result)


def _unix_time(value: str | None) -> datetime | None:
    try:
        seconds = int(value or "")
    except ValueError:
        return None
    if seconds <= 0:
        return None
    try:
        return datetime.fromtimestamp(seconds, timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _chrome_time(value: str | int | None) -> datetime | None:
    # Chrome stores microseconds since 1601-01-01 UTC, as a string.
    try:
        micros = int(value or 0)
    except ValueError:
        return None
    if micros <= 0:
        return None
    try:
        return _CHROME_EPOCH + timedelta(microseconds=micros)
    except OverflowError:
        return None


class _NetscapeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.bookmarks: list[Bookmark] = []
        # One entry per open <DL>; the folder name, or None for the top level.
        self._folders: list[str | None] = []
        self._pending_folder: str | None = None
        self._in_h3 = False
        self._h3_text: list[str] = []
        self._link: dict[str, str | None] | None = None
        self._link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "h3":
            self._in_h3 = True
            self._h3_text = []
        elif tag == "dl":
            self._folders.append(self._pending_folder)
            self._pending_folder = None
        elif tag == "a":
            self._link = dict(attrs)
            self._link_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "h3":
            self._in_h3 = False
            self._pending_folder = _clean("".join(self._h3_text))
        elif tag == "dl":
            if self._folders:
                self._folders.pop()
        elif tag == "a" and self._link is not None:
            url = (self._link.get("href") or "").strip()
            # Firefox writes TAGS="a,b" on tagged bookmarks only, so a missing
            # attribute can't tell "no tags" from "this browser has no tags".
            tags = self._link.get("tags")
            if is_web_url(url):
                self.bookmarks.append(
                    Bookmark(
                        url=url,
                        title=_clean("".join(self._link_text)),
                        folder="/".join(f for f in self._folders if f),
                        added_at=_unix_time(self._link.get("add_date")),
                        tags=clean_tags(tags.split(",")) if tags is not None else None,
                    )
                )
            self._link = None

    def handle_data(self, data: str) -> None:
        if self._in_h3:
            self._h3_text.append(data)
        elif self._link is not None:
            self._link_text.append(data)


def parse_netscape_html(text: str) -> list[Bookmark]:
    parser = _NetscapeParser()
    parser.feed(text)
    parser.close()
    return parser.bookmarks


def parse_chrome_json(text: str) -> list[Bookmark]:
    data = json.loads(text)
    bookmarks: list[Bookmark] = []

    def walk(node: dict, path: list[str]) -> None:
        if node.get("type") == "url":
            url = (node.get("url") or "").strip()
            if is_web_url(url):
                bookmarks.append(
                    Bookmark(
                        url=url,
                        title=_clean(node.get("name") or ""),
                        folder="/".join(path),
                        added_at=_chrome_time(node.get("date_added")),
                    )
                )
            return
        name = _clean(node.get("name") or "")
        child_path = [*path, name] if name else path
        for child in node.get("children") or []:
            if isinstance(child, dict):
                walk(child, child_path)

    for root in (data.get("roots") or {}).values():
        # "roots" also holds non-folder values such as sync metadata.
        if isinstance(root, dict):
            walk(root, [])
    return bookmarks


def _firefox_time(value: int | None) -> datetime | None:
    # Firefox stores microseconds since 1970-01-01 UTC.
    if not value or value <= 0:
        return None
    try:
        return _UNIX_EPOCH + timedelta(microseconds=value)
    except OverflowError:
        return None


def _read_firefox_db(db: sqlite3.Connection) -> list[Bookmark]:
    folders: dict[int, tuple[int, str, str]] = {}  # id -> (parent, title, guid)
    for id_, parent, title, guid in db.execute(
        "SELECT id, parent, title, guid FROM moz_bookmarks WHERE type = ?",
        (_FIREFOX_TYPE_FOLDER,),
    ):
        folders[id_] = (parent, title or "", guid or "")

    paths: dict[int, str | None] = {}

    def folder_path(folder_id: int) -> str | None:
        """'A/B/C' for a folder, or None if it's inside the tags folder."""
        if folder_id in paths:
            return paths[folder_id]
        parts: list[str] = []
        seen: set[int] = set()
        current = folder_id
        result: str | None = None
        while current in folders and current not in seen:
            seen.add(current)
            parent, title, guid = folders[current]
            if guid == _FIREFOX_TAGS_GUID:
                break
            if guid == _FIREFOX_ROOT_GUID:
                result = "/".join(reversed(parts))
                break
            name = _FIREFOX_ROOTS.get(guid) or _clean(title)
            if name:
                parts.append(name)
            current = parent
        else:
            # Orphaned folder (parent missing) or a cycle; keep what we have.
            result = "/".join(reversed(parts))
        paths[folder_id] = result
        return result

    tags: dict[str, list[str]] = {}  # url -> tag names
    tag_folders = {
        id_: title
        for id_, (parent, title, _) in folders.items()
        if folders.get(parent, (0, "", ""))[2] == _FIREFOX_TAGS_GUID
    }
    for parent, url in db.execute(
        """
        SELECT b.parent, p.url FROM moz_bookmarks AS b JOIN moz_places AS p ON p.id = b.fk
        WHERE b.type = ? ORDER BY b.parent, b.position
        """,
        (_FIREFOX_TYPE_BOOKMARK,),
    ):
        if parent in tag_folders and url:
            tags.setdefault(url.strip(), []).append(tag_folders[parent])

    bookmarks: list[Bookmark] = []
    rows = db.execute(
        """
        SELECT b.parent, COALESCE(NULLIF(b.title, ''), p.title, ''), p.url, b.dateAdded
        FROM moz_bookmarks AS b
        JOIN moz_places AS p ON p.id = b.fk
        WHERE b.type = ?
        ORDER BY b.parent, b.position
        """,
        (_FIREFOX_TYPE_BOOKMARK,),
    )
    for parent, title, url, date_added in rows:
        url = (url or "").strip()
        if not is_web_url(url):
            continue
        folder = folder_path(parent)
        if folder is None:
            continue
        bookmarks.append(
            Bookmark(
                url=url,
                title=_clean(title),
                folder=folder,
                added_at=_firefox_time(date_added),
                tags=tuple(sorted(clean_tags(tags.get(url, ())), key=str.lower)),
            )
        )
    return bookmarks


def parse_firefox_places(path: str | Path) -> list[Bookmark]:
    """Read bookmarks from a Firefox ``places.sqlite`` database.

    Firefox keeps the database open while it runs, with recent changes in a
    ``places.sqlite-wal`` file next to it. To see those changes without ever
    touching the user's profile, the database and its WAL are copied to a
    temporary directory and the copy is read.
    """
    source = Path(path)
    with tempfile.TemporaryDirectory(prefix="markfind-") as tmp:
        copy = Path(tmp) / "places.sqlite"
        shutil.copyfile(source, copy)
        wal = source.with_name(source.name + "-wal")
        if wal.exists():
            shutil.copyfile(wal, copy.with_name(copy.name + "-wal"))

        db = sqlite3.connect(copy)
        try:
            return _read_firefox_db(db)
        except sqlite3.DatabaseError as exc:
            raise ValueError(f"not a readable Firefox places database: {exc}") from exc
        finally:
            db.close()


def _is_plist(data: bytes) -> bool:
    return data.startswith(b"bplist00") or b"<plist" in data[:1024]


def parse_safari_plist(data: bytes) -> list[Bookmark]:
    """Parse Safari's ``Bookmarks.plist`` (binary or XML property list)."""
    try:
        root = plistlib.loads(data)
    # LookupError: the XML declaration names an encoding Python doesn't have.
    except (
        plistlib.InvalidFileException,
        ExpatError,
        ValueError,
        OverflowError,
        LookupError,
    ) as exc:
        raise ValueError(f"not a readable Safari bookmarks file: {exc}") from exc
    if not isinstance(root, dict):
        raise ValueError("not a readable Safari bookmarks file: unexpected contents")

    bookmarks: list[Bookmark] = []

    def walk(node: dict, path: list[str]) -> None:
        kind = node.get("WebBookmarkType")
        if kind == "WebBookmarkTypeLeaf":
            url = str(node.get("URLString") or "").strip()
            if not is_web_url(url):
                return
            uri = node.get("URIDictionary")
            title = uri.get("title") if isinstance(uri, dict) else None
            reading = node.get("ReadingList")
            added = reading.get("DateAdded") if isinstance(reading, dict) else None
            if isinstance(added, datetime) and added.tzinfo is None:
                added = added.replace(tzinfo=timezone.utc)  # plistlib dates are UTC
            bookmarks.append(
                Bookmark(
                    url=url,
                    title=_clean(str(title or "")),
                    folder="/".join(path),
                    added_at=added if isinstance(added, datetime) else None,
                )
            )
        elif kind == "WebBookmarkTypeList":
            children = node.get("Children")
            if not isinstance(children, list):
                return
            # The root list has no title; built-in folders get Safari's display names.
            title = _clean(str(node.get("Title") or ""))
            name = _SAFARI_FOLDERS.get(title, title)
            for child in children:
                if isinstance(child, dict):
                    walk(child, [*path, name] if name else path)
        # WebBookmarkTypeProxy (the History shortcut) and anything else: skipped.

    walk(root, [])
    return bookmarks


def load_file(path: str | Path) -> list[Bookmark]:
    """Parse a bookmark export, detecting the format from its contents."""
    with open(path, "rb") as f:
        header = f.read(len(_SQLITE_HEADER))
    if header == _SQLITE_HEADER:
        return parse_firefox_places(path)
    data = Path(path).read_bytes()
    if _is_plist(data):
        return parse_safari_plist(data)
    text = data.decode("utf-8", errors="replace")
    if text.lstrip().startswith("{"):
        return parse_chrome_json(text)
    return parse_netscape_html(text)
