"""Command-line interface: ``markfind import | sync | fetch | search | list | ...``."""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import timedelta
from pathlib import Path

from . import __version__
from .browsers import BROWSERS, find_sources
from .extract import Page
from .fetch import (
    DEAD_KINDS,
    ERROR_KINDS,
    HostLimiter,
    classify_error,
    fetch_many,
    fetch_page,
)
from .formats import FORMATS, to_markdown
from .importers import load_file
from .server import DEFAULT_HOST, DEFAULT_PORT, serve
from .store import QueryError, Store, to_fts_query

_DURATION_UNITS = {"h": "hours", "d": "days", "w": "weeks"}

SSL_HINT = (
    "hint: some pages failed SSL certificate checks. If this happens for most\n"
    "HTTPS sites, Python can't find your system's certificates; see\n"
    "https://github.com/Alcides1123/markfind#ssl-certificate-errors"
)


def default_db_path() -> Path:
    return Path(os.environ.get("MARKFIND_DB") or Path.home() / ".markfind.sqlite")


def cmd_export(store: Store, args: argparse.Namespace) -> int:
    records = store.records(args.folder)
    if args.query:
        query = args.query if args.raw else to_fts_query(args.query)
        try:
            ranked = [r.url for r in store.search(query, limit=-1)]
        except QueryError as exc:
            print(f"error: invalid query: {exc}", file=sys.stderr)
            return 2
        rank = {url: n for n, url in enumerate(ranked)}
        records = sorted((r for r in records if r.url in rank), key=lambda r: rank[r.url])
    if args.skip_dead:
        records = [r for r in records if r.error_kind not in DEAD_KINDS]

    if args.format == "markdown" and args.query:
        text = to_markdown(records, ranked=True)
    else:
        text = FORMATS[args.format](records)
    if args.output is None:
        sys.stdout.write(text)
        return 0
    # The BOM tells Excel the CSV is UTF-8.
    encoding = "utf-8-sig" if args.format == "csv" else "utf-8"
    try:
        with open(args.output, "w", encoding=encoding, newline="") as f:
            f.write(text)
    except OSError as exc:
        print(f"error: could not write {args.output}: {exc}", file=sys.stderr)
        return 1
    print(f"exported {len(records)} bookmarks to {args.output}", file=sys.stderr)
    return 0


def parse_duration(text: str) -> timedelta:
    """Parse durations like ``12h``, ``90d`` or ``2w``."""
    match = re.fullmatch(r"\s*(\d+)\s*([hdw])\s*", text.lower())
    if not match:
        raise argparse.ArgumentTypeError(f"invalid duration {text!r} (examples: 12h, 90d, 2w)")
    amount, unit = match.groups()
    return timedelta(**{_DURATION_UNITS[unit]: int(amount)})


def _source_key(path: Path) -> str:
    """How a bookmark file is recorded as a bookmark's source."""
    return str(Path(path).resolve())


def _prune(store: Store, loaded: list[tuple[str, Path, list[str]]], quiet: bool) -> None:
    """Remove bookmarks that are no longer in the files they were imported from.

    Prune only after every file has been imported, so a bookmark that moved
    from one file to another has already been claimed by its new file.
    """
    for label, path, urls in loaded:
        if not urls:
            # More likely a broken or wrong file than a deliberate clear-out.
            print(f"{label}: no bookmarks found, not pruning")
            continue
        removed = store.prune(_source_key(path), urls)
        print(f"{label}: {len(removed)} removed")
        if not quiet:
            for url, title in removed:
                print(f"  - {title or url} <{url}>")


def cmd_import(store: Store, args: argparse.Namespace) -> int:
    loaded = []
    for path in args.files:
        try:
            bookmarks = load_file(path)
        except (OSError, ValueError) as exc:
            print(f"error: could not read {path}: {exc}", file=sys.stderr)
            return 1
        added, updated = store.add_bookmarks(bookmarks, source=_source_key(path))
        print(f"{path}: {added} added, {updated} updated")
        loaded.append((str(path), path, [bm.url for bm in bookmarks]))
    if args.prune:
        _prune(store, loaded, quiet=False)
    return 0


def cmd_sync(store: Store, args: argparse.Namespace) -> int:
    sources = find_sources()
    if args.browser:
        sources = [s for s in sources if s.browser.lower() in args.browser]
    if not sources:
        print(
            "error: no browser bookmarks found; import an exported file with"
            " 'markfind import FILE' instead",
            file=sys.stderr,
        )
        return 1
    if args.list:
        for source in sources:
            print(f"{source.name}\t{source.path}")
        return 0

    status = 0
    loaded = []
    for source in sources:
        try:
            bookmarks = load_file(source.path)
        except (OSError, ValueError) as exc:
            print(f"error: could not read {source.name}: {exc}", file=sys.stderr)
            status = 1
            continue
        added, updated = store.add_bookmarks(bookmarks, source=_source_key(source.path))
        print(f"{source.name}: {added} added, {updated} updated")
        loaded.append((source.name, source.path, [bm.url for bm in bookmarks]))
    if args.prune:
        _prune(store, loaded, args.quiet)
    if not args.no_fetch:
        _fetch(store, store.pending(), args)
    return status


def cmd_fetch(store: Store, args: argparse.Namespace) -> int:
    pending = store.pending(refetch=args.all, older_than=args.older_than, failed=args.failed)
    _fetch(store, pending, args)
    return 0


def _fetch(store: Store, pending: list[tuple[int, str]], args: argparse.Namespace) -> None:
    if not pending:
        print("nothing to fetch")
        return

    ok = failed = 0
    kinds: set[str] = set()
    results = fetch_many(
        pending,
        fetch=fetch_page,
        workers=args.workers,
        timeout=args.timeout,
        limiter=HostLimiter(delay=args.delay),
    )
    for done, (id_, url, result) in enumerate(results, 1):
        if isinstance(result, Page):
            store.set_page(id_, result.text, result.title)
            ok += 1
            status = "ok"
        else:
            kind, message = classify_error(result)
            store.set_error(id_, message, kind)
            kinds.add(kind)
            failed += 1
            status = f"failed, {kind}: {message}"
        if not args.quiet:
            print(f"[{done}/{len(pending)}] {status}: {url}")
    print(f"fetched {ok}, failed {failed}")
    if failed:
        print("run 'markfind broken --all' to see what failed")
    if "ssl" in kinds:
        print(SSL_HINT)


def cmd_search(store: Store, args: argparse.Namespace) -> int:
    text = " ".join(args.query)
    query = text if args.raw else to_fts_query(text)
    try:
        results = store.search(query, limit=args.limit, mark=_mark())
    except QueryError as exc:
        print(f"error: invalid query: {exc}", file=sys.stderr)
        return 2

    if not results:
        print("no matches")
        return 1
    _print_results(results)
    return 0


def cmd_similar(store: Store, args: argparse.Namespace) -> int:
    try:
        found = store.similar(args.url, limit=args.limit, mark=_mark())
    except KeyError:
        print(
            f"error: {args.url} is not in markfind; copy the URL from"
            " 'markfind search' or 'markfind list'",
            file=sys.stderr,
        )
        return 2

    print(f"Similar to: {found.title or args.url}")
    if not found.results:
        print("no similar bookmarks")
        return 1
    print(f"based on: {', '.join(found.keywords)}")
    print()
    _print_results(found.results)
    return 0


def _mark() -> tuple[str, str]:
    """How to highlight matched words: bold on a terminal, [brackets] otherwise."""
    return ("\033[1m", "\033[0m") if sys.stdout.isatty() else ("[", "]")


def _print_results(results: list) -> None:
    for n, r in enumerate(results, 1):
        print(f"{n}. {r.title or r.url}")
        print(f"   {r.url}")
        if r.folder:
            print(f"   folder: {r.folder}")
        if r.snippet:
            print(f"   {r.snippet}")


def cmd_list(store: Store, args: argparse.Namespace) -> int:
    for url, title, folder in store.list_bookmarks(args.folder):
        print(f"{folder or '-'}\t{title or '-'}\t{url}")
    return 0


def cmd_duplicates(store: Store, args: argparse.Namespace) -> int:
    groups = store.duplicates(by=(args.by,) if args.by else ("url", "text"))

    if args.urls:
        seen = set()
        for group in groups:
            for entry in group.entries[1:]:
                if entry.url not in seen:
                    seen.add(entry.url)
                    print(entry.url)
        return 0

    if not groups:
        print("no duplicates found")
        return 0
    headings = {"url": "Same page, different URLs", "text": "Same page text"}
    for reason, heading in headings.items():
        section = [g for g in groups if g.reason == reason]
        if not section:
            continue
        if reason != groups[0].reason:
            print()
        print(f"{heading} ({len(section)}):")
        for group in section:
            print(f"  {group.title}")
            width = min(max(len(e.url) for e in group.entries), 70)
            for n, entry in enumerate(group.entries):
                line = f"  {'*' if n == 0 else ' '} {entry.url:<{width}}"
                if entry.folder:
                    line += f"  folder: {entry.folder}"
                print(line.rstrip())
    copies = sum(len(g.entries) - 1 for g in groups)
    print()
    print(f"* = cleanest URL. {copies} extra copies; 'markfind duplicates --urls' lists them.")
    return 0


def cmd_stats(store: Store, args: argparse.Namespace) -> int:
    s = store.stats()
    print(f"bookmarks: {s.total}")
    print(f"indexed:   {s.fetched}")
    breakdown = ", ".join(f"{kind} {n}" for kind, n in s.failed_by_kind.items())
    print(f"failed:    {s.failed}" + (f" ({breakdown})" if breakdown else ""))
    print(f"pending:   {s.total - s.fetched - s.failed}")
    return 0


def cmd_serve(store: Store, args: argparse.Namespace) -> int:
    # The CLI's own Store has already created / migrated the database; the
    # server opens its own read-only connections per request.
    store.close()
    try:
        serve(args.db_path, host=args.host, port=args.port, open_browser=args.open)
    except OSError as exc:
        print(f"error: could not start server on {args.host}:{args.port}: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_broken(store: Store, args: argparse.Namespace) -> int:
    if args.kind:
        kinds: tuple[str, ...] | None = tuple(args.kind)
    elif args.all:
        kinds = None
    else:
        kinds = DEAD_KINDS
    failures = store.failures(kinds)

    if args.urls:
        for f in failures:
            print(f.url)
        return 0

    if not failures:
        what = "failed" if kinds is None else "matching"
        print(f"no {what} bookmarks")
        return 0
    current = None
    for f in failures:
        if f.kind != current:
            current = f.kind
            count = sum(1 for g in failures if g.kind == current)
            if current != failures[0].kind:
                print()
            print(f"{current} ({count}):")
        print(f"  {f.title or f.url}")
        print(f"    {f.url}")
        details = f"{f.error}, checked {f.fetched_at[:10]}"
        if f.folder:
            details += f", folder: {f.folder}"
        print(f"    {details}")
    return 0


def _add_fetch_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("--workers", type=int, default=8, help="parallel downloads (default: 8)")
    p.add_argument("--timeout", type=float, default=10.0, help="seconds per page (default: 10)")
    p.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="minimum seconds between requests to the same site (default: 1, 0 disables)",
    )
    p.add_argument("-q", "--quiet", action="store_true", help="only print the summary")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="markfind", description="Full-text search over your browser bookmarks."
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="database file (default: $MARKFIND_DB or ~/.markfind.sqlite)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser(
        "import", help="import a bookmarks HTML export, Chrome Bookmarks or Firefox places.sqlite"
    )
    p.add_argument("files", nargs="+", type=Path)
    p.add_argument(
        "--prune",
        action="store_true",
        help="remove bookmarks imported from these files earlier that are no longer in them",
    )
    p.set_defaults(func=cmd_import)

    p = sub.add_parser(
        "sync",
        help="import from installed browsers and fetch new pages, in one step",
        description=(
            "Find the bookmarks of every installed browser profile (Chrome, Edge, Brave,"
            " Vivaldi, Chromium, Firefox), import them, and fetch pages not fetched yet."
        ),
    )
    p.add_argument("--list", action="store_true", help="only show the bookmark files found")
    p.add_argument(
        "--browser",
        action="append",
        type=str.lower,
        choices=[b.lower() for b in BROWSERS],
        help="only this browser; repeat for several",
    )
    p.add_argument(
        "--prune", action="store_true", help="remove bookmarks that were deleted in the browser"
    )
    p.add_argument("--no-fetch", action="store_true", help="import only, don't fetch pages")
    _add_fetch_options(p)
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("fetch", help="download bookmarked pages and index their text")
    p.add_argument("--all", action="store_true", help="re-fetch pages that were already fetched")
    p.add_argument(
        "--older-than",
        type=parse_duration,
        metavar="AGE",
        help="also re-fetch pages fetched more than AGE ago, e.g. 90d, 2w, 12h",
    )
    p.add_argument("--failed", action="store_true", help="also retry pages whose last fetch failed")
    _add_fetch_options(p)
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("search", help="search titles, URLs, folders and page text")
    p.add_argument("query", nargs="+")
    p.add_argument("-n", "--limit", type=int, default=20)
    p.add_argument(
        "--raw",
        action="store_true",
        help="pass the query to SQLite FTS5 unchanged (OR, NEAR, title:...)",
    )
    p.set_defaults(func=cmd_search)

    p = sub.add_parser(
        "similar",
        help="find bookmarks like a given one",
        description=(
            "List the bookmarks most like the one with this URL, based on the words"
            " that make its page distinctive."
        ),
    )
    p.add_argument("url", help="the bookmark's URL, exactly as 'markfind list' shows it")
    p.add_argument("-n", "--limit", type=int, default=10)
    p.set_defaults(func=cmd_similar)

    p = sub.add_parser("list", help="list bookmarks")
    p.add_argument("--folder", help="only this folder and its subfolders, e.g. 'Bookmarks bar/Dev'")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser(
        "duplicates",
        help="find bookmarks saved more than once",
        description=(
            "Find bookmarks that are the same page: the same URL apart from cosmetic"
            " differences (http/https, www., tracking parameters, ...), or the same"
            " page text."
        ),
    )
    p.add_argument(
        "--by", choices=("url", "text"), help="only this kind of duplicate (default: both)"
    )
    p.add_argument(
        "--urls",
        action="store_true",
        help="print only the extra copies' URLs (all but the starred one), one per line",
    )
    p.set_defaults(func=cmd_duplicates)

    p = sub.add_parser("stats", help="show how many bookmarks are indexed")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser(
        "export",
        help="write bookmarks to a file browsers can import, or Markdown, JSON, CSV",
        description=(
            "Write bookmarks out. The html format is a standard bookmark file that"
            " every browser can import, e.g. after leaving out dead links with --skip-dead."
        ),
    )
    p.add_argument("-f", "--format", choices=tuple(FORMATS), default="html", help="default: html")
    p.add_argument("-o", "--output", type=Path, help="file to write (default: standard output)")
    p.add_argument("--folder", help="only this folder and its subfolders")
    p.add_argument(
        "--query", metavar="WORDS", help="only bookmarks matching this search, best first"
    )
    p.add_argument("--raw", action="store_true", help="--query uses FTS5 syntax, as in search")
    p.add_argument(
        "--skip-dead",
        action="store_true",
        help="leave out bookmarks that are likely dead (what 'markfind broken' lists)",
    )
    p.set_defaults(func=cmd_export)

    p = sub.add_parser(
        "broken",
        help="list bookmarks whose pages could not be fetched",
        description=(
            "By default, lists bookmarks that are most likely dead for good: "
            f"{' and '.join(DEAD_KINDS)} errors (HTTP 404/410, unknown host)."
        ),
    )
    p.add_argument("--all", action="store_true", help="list every failed bookmark")
    p.add_argument(
        "--kind",
        action="append",
        choices=ERROR_KINDS,
        help="only this kind of failure; repeat for several",
    )
    p.add_argument("--urls", action="store_true", help="print only URLs, one per line")
    p.set_defaults(func=cmd_broken)

    p = sub.add_parser(
        "serve",
        help="search your bookmarks in a web browser",
        description="Start a local, read-only web interface for searching your bookmarks.",
    )
    p.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"address to listen on (default: {DEFAULT_HOST}, this computer only)",
    )
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port (default: {DEFAULT_PORT})")
    p.add_argument("--open", action="store_true", help="open the page in your web browser")
    p.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.db_path = args.db or default_db_path()
    with Store(args.db_path) as store:
        return args.func(store, args)


if __name__ == "__main__":
    sys.exit(main())
