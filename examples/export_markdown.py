"""Export markfind search results or broken links as a Markdown list.

Useful for pasting into notes, a wiki, or an issue:

    python examples/export_markdown.py "sqlite index" > sqlite.md
    python examples/export_markdown.py --broken > dead-links.md

Reads the same database as the ``markfind`` command (``--db``,
``$MARKFIND_DB``, or ``~/.markfind.sqlite``).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from markfind.cli import default_db_path
from markfind.fetch import DEAD_KINDS
from markfind.store import QueryError, Store, to_fts_query


def md_link(title: str, url: str) -> str:
    text = (title or url).replace("[", "\\[").replace("]", "\\]")
    return f"[{text}]({url})"


def search_markdown(store: Store, query: str, limit: int) -> str:
    # Markdown bold for the matched words.
    results = store.search(to_fts_query(query), limit=limit, mark=("**", "**"))
    lines = [f"# Bookmarks matching “{query}”", ""]
    if not results:
        lines.append("_No matches._")
    for r in results:
        lines.append(f"- {md_link(r.title, r.url)}" + (f" — _{r.folder}_" if r.folder else ""))
        if r.snippet:
            lines.append(f"  > {r.snippet}")
    return "\n".join(lines) + "\n"


def broken_markdown(store: Store) -> str:
    failures = store.failures(DEAD_KINDS)
    lines = ["# Dead bookmarks", ""]
    if not failures:
        lines.append("_None found._")
    for f in failures:
        # A task list, so the file doubles as a clean-up checklist.
        lines.append(f"- [ ] {md_link(f.title, f.url)} — {f.error} (checked {f.fetched_at[:10]})")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("query", nargs="*", help="words to search for")
    parser.add_argument("--broken", action="store_true", help="export dead bookmarks instead")
    parser.add_argument("-n", "--limit", type=int, default=50)
    parser.add_argument("--db", type=Path, default=None)
    args = parser.parse_args(argv)

    if not args.broken and not args.query:
        parser.error("give some search words, or --broken")

    with Store(args.db or default_db_path()) as store:
        try:
            if args.broken:
                text = broken_markdown(store)
            else:
                text = search_markdown(store, " ".join(args.query), args.limit)
        except QueryError as exc:
            print(f"error: invalid query: {exc}", file=sys.stderr)
            return 2
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
