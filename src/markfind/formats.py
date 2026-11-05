"""Write bookmarks out as an importable HTML file, Markdown, JSON or CSV."""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Callable
from datetime import datetime
from html import escape

from .store import Record

_NETSCAPE_HEADER = """<!DOCTYPE NETSCAPE-Bookmark-file-1>
<!-- This is an automatically generated file.
     It will be read and overwritten.
     DO NOT EDIT! -->
<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">
<TITLE>Bookmarks</TITLE>
<H1>Bookmarks</H1>
"""

FIELDS = ("url", "title", "folder", "added_at", "fetched_at", "status", "tags", "note")


def _unix(timestamp: str | None) -> str:
    if not timestamp:
        return ""
    try:
        return str(int(datetime.fromisoformat(timestamp).timestamp()))
    except ValueError:
        return ""


class _Folder:
    def __init__(self) -> None:
        self.records: list[Record] = []
        self.folders: dict[str, _Folder] = {}


def _tree(records: list[Record]) -> _Folder:
    root = _Folder()
    for record in records:
        node = root
        for name in filter(None, record.folder.split("/")):
            node = node.folders.setdefault(name, _Folder())
        node.records.append(record)
    return root


def to_html(records: list[Record]) -> str:
    """A Netscape bookmark file, the format every browser imports and exports."""
    lines = [_NETSCAPE_HEADER.rstrip("\n"), "<DL><p>"]

    def write(node: _Folder, depth: int) -> None:
        indent = "    " * depth
        for r in node.records:
            added = _unix(r.added_at)
            date = f' ADD_DATE="{added}"' if added else ""
            # The attribute Firefox writes, and reads back on import.
            tags = f' TAGS="{escape(",".join(r.tags))}"' if r.tags else ""
            title = escape(r.title or r.url, quote=False)
            lines.append(f'{indent}<DT><A HREF="{escape(r.url)}"{date}{tags}>{title}</A>')
            if r.note:
                # A bookmark's description; Firefox imports and exports it this way.
                lines.append(f"{indent}<DD>{escape(' '.join(r.note.split()), quote=False)}")
        for name, child in node.folders.items():
            lines.append(f"{indent}<DT><H3>{escape(name, quote=False)}</H3>")
            lines.append(f"{indent}<DL><p>")
            write(child, depth + 1)
            lines.append(f"{indent}</DL><p>")

    write(_tree(records), 1)
    lines.append("</DL><p>")
    return "\n".join(lines) + "\n"


def _md_text(text: str) -> str:
    for char in "\\`*_[]<>":
        text = text.replace(char, "\\" + char)
    return " ".join(text.split())


def _md_link(record: Record) -> str:
    url = record.url
    if any(char in url for char in " ()<>"):
        url = "<" + url.replace(" ", "%20").replace("<", "%3C").replace(">", "%3E") + ">"
    return f"[{_md_text(record.title or record.url)}]({url})"


def _md_note(record: Record) -> str:
    return f" — {_md_text(record.note)}" if record.note else ""


def to_markdown(records: list[Record], ranked: bool = False) -> str:
    """A reading list: one section per folder, or a numbered list if ``ranked``."""
    if ranked:
        lines = ["# Bookmarks", ""]
        for n, r in enumerate(records, 1):
            folder = f" · *{_md_text(r.folder)}*" if r.folder else ""
            lines.append(f"{n}. {_md_link(r)}{folder}{_md_note(r)}")
        return "\n".join(lines) + "\n"

    lines = ["# Bookmarks"]
    current: str | None = None
    for r in sorted(records, key=lambda r: r.folder):  # stable: keeps order within a folder
        if r.folder != current:
            current = r.folder
            lines.append("")
            if r.folder:  # bookmarks outside any folder come first, without a heading
                lines += [f"## {_md_text(r.folder.replace('/', ' / '))}", ""]
        lines.append(f"- {_md_link(r)}{_md_note(r)}")
    return "\n".join(lines) + "\n"


def _rows(records: list[Record], tags: Callable[[tuple[str, ...]], object]) -> list[dict]:
    rows = []
    for r in records:
        row: dict[str, object] = {field: getattr(r, field) for field in FIELDS}
        row["tags"] = tags(r.tags)
        rows.append(row)
    return rows


def to_json(records: list[Record]) -> str:
    return json.dumps(_rows(records, list), indent=2, ensure_ascii=False) + "\n"


def to_csv(records: list[Record]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=FIELDS)
    writer.writeheader()
    writer.writerows(_rows(records, ",".join))
    return out.getvalue()


FORMATS: dict[str, Callable[[list[Record]], str]] = {
    "html": to_html,
    "markdown": to_markdown,
    "json": to_json,
    "csv": to_csv,
}
