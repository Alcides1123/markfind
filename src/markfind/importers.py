"""Read bookmarks exported from browsers.

Two formats are supported:

* The Netscape bookmark HTML file that every major browser can export
  (Chrome, Edge, Firefox, Safari: "Export bookmarks to HTML").
* Chrome / Edge's internal ``Bookmarks`` JSON file from the profile folder.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

_CHROME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class Bookmark:
    url: str
    title: str = ""
    folder: str = ""
    added_at: datetime | None = None


def _is_web_url(url: str) -> bool:
    return urlsplit(url).scheme in ("http", "https")


def _clean(text: str) -> str:
    return " ".join(text.split())


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
            if _is_web_url(url):
                self.bookmarks.append(
                    Bookmark(
                        url=url,
                        title=_clean("".join(self._link_text)),
                        folder="/".join(f for f in self._folders if f),
                        added_at=_unix_time(self._link.get("add_date")),
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
            if _is_web_url(url):
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


def load_file(path: str | Path) -> list[Bookmark]:
    """Parse a bookmark export, detecting the format from its contents."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    if text.lstrip().startswith("{"):
        return parse_chrome_json(text)
    return parse_netscape_html(text)
