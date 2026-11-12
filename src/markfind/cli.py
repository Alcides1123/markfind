"""Command-line interface: ``markfind import | sync | fetch | search | list | ...``."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import webbrowser
from datetime import timedelta
from pathlib import Path

from . import __version__
from .archive import render
from .browsers import BROWSERS, find_sources
from .duplicates import MIN_SIMILARITY, REASONS, Entry
from .extract import Page
from .fetch import (
    DEAD_KINDS,
    ERROR_KINDS,
    MAX_RETRY_WAIT,
    HostLimiter,
    NotModified,
    classify_error,
    fetch_many,
    fetch_page,
)
from .formats import FORMATS, to_markdown
from .importers import clean_tags, is_web_url, load_file
from .server import DEFAULT_HOST, DEFAULT_PORT, serve
from .store import Failure, QueryError, Store, to_fts_query
from .topics import MIN_SIZE

_DURATION_UNITS = {"h": "hours", "d": "days", "w": "weeks"}

SSL_HINT = (
    "hint: some pages failed SSL certificate checks. Their certificates may be\n"
    "fine: Python often can't use all of your system's, especially on Windows. See\n"
    "https://github.com/Alcides1123/markfind#ssl-certificate-errors"
)


FULL_DISK_ACCESS_HINT = """hint: macOS only lets apps with Full Disk Access read Safari's bookmarks.
Allow your terminal app in System Settings > Privacy & Security > Full Disk
Access and try again, or export them in Safari with File > Export > Bookmarks."""


STILL_IN_BROWSER_NOTE = """Your browser still has them: remove them there too, or the next
'markfind sync' or import brings them back. 'markfind export --skip-dead'
writes a clean bookmark file you can import into the browser."""


def default_db_path() -> Path:
    return Path(os.environ.get("MARKFIND_DB") or Path.home() / ".markfind.sqlite")


def cmd_export(store: Store, args: argparse.Namespace) -> int:
    records = store.records(args.folder, tag=args.tag)
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


def _percent(text: str) -> float:
    try:
        value = float(text.rstrip("%"))
    except ValueError:
        value = -1
    if not 1 <= value <= 100:
        raise argparse.ArgumentTypeError(f"invalid percentage {text!r} (1 to 100)")
    return value


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


def _permission_hint(exc: Exception, path: Path) -> None:
    """Explain macOS's Full Disk Access when Safari's bookmarks can't be read."""
    if isinstance(exc, PermissionError) and "/Library/Safari/" in Path(path).as_posix():
        print(FULL_DISK_ACCESS_HINT, file=sys.stderr)


def cmd_add(store: Store, args: argparse.Namespace) -> int:
    urls = []
    for given in args.urls:
        url = given.strip()
        if not is_web_url(url):
            print(f"error: not a web address: {given}", file=sys.stderr)
            return 2
        urls.append(url)

    new = {url for url in urls if store.add_bookmark(url, folder=args.folder)}
    for url in urls:
        if args.tag:
            store.add_tags(url, args.tag)
        if args.note is not None:
            store.set_note(url, args.note)

    if not args.no_fetch:
        wanted = set(urls)
        to_fetch = [(id_, url) for id_, url in store.pending() if url in wanted]
        if to_fetch:
            _fetch(store, to_fetch, args)

    failed = 0
    for url in urls:
        record = store.bookmark(url)
        state = "added" if url in new else "already in markfind"
        print(f"{state}: {record.title}" if record and record.title else f"{state}: {url}")
        if record and record.title:
            print(f"  {url}")
        if record and record.error_kind:
            failed += 1
    # The page may be worth keeping even when it can't be read, so the
    # bookmark stays; the exit code says something went wrong.
    return 1 if failed else 0


def cmd_import(store: Store, args: argparse.Namespace) -> int:
    loaded = []
    for path in args.files:
        try:
            bookmarks = load_file(path)
        except (OSError, ValueError) as exc:
            print(f"error: could not read {path}: {exc}", file=sys.stderr)
            _permission_hint(exc, path)
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
            _permission_hint(exc, source.path)
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

    ok = unchanged = failed = 0
    kinds: set[str] = set()
    # Only re-fetches can be conditional; `sync` has no such option.
    revalidate = getattr(args, "if_changed", False)
    results = fetch_many(
        pending,
        fetch=fetch_page,
        workers=args.workers,
        timeout=args.timeout,
        limiter=HostLimiter(delay=args.delay),
        max_retry_wait=args.max_retry_wait,
        validators=store.validators([id_ for id_, _ in pending]) if revalidate else None,
    )
    for done, (id_, url, result) in enumerate(results, 1):
        if isinstance(result, Page):
            store.set_page(
                id_,
                result.text,
                result.title,
                html=result.html,
                etag=result.etag,
                last_modified=result.last_modified,
            )
            ok += 1
            status = "ok"
        elif isinstance(result, NotModified):
            store.set_unchanged(id_)
            unchanged += 1
            status = "unchanged"
        else:
            kind, message = classify_error(result)
            store.set_error(id_, message, kind)
            kinds.add(kind)
            failed += 1
            status = f"failed, {kind}: {message}"
        if not args.quiet:
            print(f"[{done}/{len(pending)}] {status}: {url}")
    counts = f"fetched {ok}" + (f", unchanged {unchanged}" if unchanged else "")
    print(f"{counts}, failed {failed}")
    if failed:
        print("run 'markfind broken --all' to see what failed")
    if "ssl" in kinds:
        print(SSL_HINT)


def cmd_search(store: Store, args: argparse.Namespace) -> int:
    text = " ".join(args.query)
    query = text if args.raw else to_fts_query(text)
    try:
        results = store.search(
            query, limit=args.limit, mark=_mark(), folder=args.folder, tag=args.tag
        )
    except QueryError as exc:
        print(f"error: invalid query: {exc}", file=sys.stderr)
        return 2

    if not results:
        print("no matches")
        return 1
    _print_results(results)
    return 0


def _not_in_markfind(url: str) -> int:
    print(
        f"error: {url} is not in markfind; copy the URL from 'markfind search' or 'markfind list'",
        file=sys.stderr,
    )
    return 2


def cmd_similar(store: Store, args: argparse.Namespace) -> int:
    try:
        found = store.similar(args.url, limit=args.limit, mark=_mark())
    except KeyError:
        return _not_in_markfind(args.url)

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


# How much of a note search results show.
NOTE_PREVIEW = 120


def _shorten(text: str, width: int) -> str:
    """The text on one line, cut at a word boundary to at most ``width`` characters."""
    text = " ".join(text.split())
    if len(text) <= width:
        return text
    return text[: width - 3].rsplit(" ", 1)[0] + "..."


def _print_results(results: list) -> None:
    for n, r in enumerate(results, 1):
        print(f"{n}. {r.title or r.url}")
        print(f"   {r.url}")
        if r.folder:
            print(f"   folder: {r.folder}")
        if r.tags:
            print(f"   tags: {', '.join(r.tags)}")
        if r.note:
            print(f"   note: {_shorten(r.note, NOTE_PREVIEW)}")
        if r.snippet:
            print(f"   {r.snippet}")


def cmd_list(store: Store, args: argparse.Namespace) -> int:
    for url, title, folder in store.list_bookmarks(args.folder, tag=args.tag):
        print(f"{folder or '-'}\t{title or '-'}\t{url}")
    return 0


def cmd_tags(store: Store, args: argparse.Namespace) -> int:
    tags = store.tags()
    if not tags:
        print(
            "no tags; add some with 'markfind tag URL TAG', or import them from Firefox"
            " (sync, places.sqlite or an HTML export)"
        )
        return 0
    width = len(str(tags[0][1]))
    for tag, count in tags:
        print(f"{count:>{width}}  {tag}")
    return 0


def cmd_tag(store: Store, args: argparse.Namespace) -> int:
    removed: tuple[str, ...] = ()
    try:
        if args.tags:
            store.add_tags(args.url, args.tags)
        if args.remove:
            removed = store.remove_tags(args.url, args.remove)
        current = store.bookmark_tags(args.url)
    except KeyError:
        return _not_in_markfind(args.url)

    status = 0
    if args.remove:
        gone = {tag.lower() for tag in removed}
        from_browser = {tag.lower() for tag in current.browser}
        for tag in clean_tags(args.remove):
            if tag.lower() in gone:
                continue
            if tag.lower() in from_browser:
                print(f"{tag}: comes from your browser; remove it there", file=sys.stderr)
            else:
                print(f"{tag}: you haven't added this tag", file=sys.stderr)
            status = 1

    if current.mine:
        print(f"your tags: {', '.join(current.mine)}")
    if current.browser:
        print(f"from your browser: {', '.join(current.browser)}")
    if not current.all:
        print("no tags")
    return status


def cmd_note(store: Store, args: argparse.Namespace) -> int:
    text = " ".join(args.text).strip()
    if args.clear and text:
        print("error: --clear takes no text", file=sys.stderr)
        return 2
    try:
        if args.clear or text:
            store.set_note(args.url, text)
            return 0
        note = store.note(args.url)
    except KeyError:
        return _not_in_markfind(args.url)
    if not note:
        print("no note on this bookmark", file=sys.stderr)
        return 1
    print(note)
    return 0


def _positive(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        value = 0
    if value < 1:
        raise argparse.ArgumentTypeError(f"invalid number {text!r} (1 or more)")
    return value


def cmd_topics(store: Store, args: argparse.Namespace) -> int:
    found = store.topics(count=args.count, min_size=args.min_size, folder=args.folder)

    def entry(e: Entry) -> dict[str, str]:
        return {"url": e.url, "title": e.title, "folder": e.folder}

    if args.json:
        data = {
            "topics": [
                {"label": t.label, "bookmarks": [entry(e) for e in t.entries]} for t in found.topics
            ],
            "other": [entry(e) for e in found.other],
            "unindexed": found.unindexed,
        }
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0

    indexed = sum(len(t.entries) for t in found.topics) + len(found.other)
    if not indexed:
        print("no indexed bookmarks to group; run 'markfind fetch' first")
        return 1
    print(
        f"{len(found.topics)} topic{'' if len(found.topics) == 1 else 's'}"
        f" in {indexed} indexed bookmark{'' if indexed == 1 else 's'}"
    )
    for t in found.topics:
        print()
        print(f"{' · '.join(t.label)} ({len(t.entries)}):")
        for e in t.entries:
            print(f"  {e.title or e.url}")
            print(f"    {e.url}")
    if found.other:
        print()
        print(f"No clear topic ({len(found.other)}):")
        for e in found.other:
            print(f"  {e.title or e.url}")
            print(f"    {e.url}")
    if found.unindexed:
        print()
        n = found.unindexed
        print(
            f"{n} bookmark{'' if n == 1 else 's'} without page text"
            f" {'is' if n == 1 else 'are'}n't included; 'markfind fetch' adds them."
        )
    return 0


def cmd_duplicates(store: Store, args: argparse.Namespace) -> int:
    groups = store.duplicates(
        by=(args.by,) if args.by else REASONS, min_similarity=args.similarity / 100
    )

    if args.urls:
        seen = set()
        for group in groups:
            # Nearly-same pages may be different versions worth keeping, so
            # they're listed only when asked for by name.
            if group.reason == "similar" and args.by != "similar":
                continue
            for entry in group.entries[1:]:
                if entry.url not in seen:
                    seen.add(entry.url)
                    print(entry.url)
        return 0

    if not groups:
        print("no duplicates found")
        return 0
    headings = {
        "url": "Same page, different URLs",
        "text": "Same page text",
        "similar": "Nearly the same page text",
    }
    for reason, heading in headings.items():
        section = [g for g in groups if g.reason == reason]
        if not section:
            continue
        if reason != groups[0].reason:
            print()
        print(f"{heading} ({len(section)}):")
        for group in section:
            if group.similarity is not None:
                print(f"  {group.title} ({group.similarity:.0%} the same)")
            else:
                print(f"  {group.title}")
            width = min(max(len(e.url) for e in group.entries), 70)
            for n, entry in enumerate(group.entries):
                line = f"  {'*' if n == 0 else ' '} {entry.url:<{width}}"
                if entry.folder:
                    line += f"  folder: {entry.folder}"
                print(line.rstrip())
    copies = sum(len(g.entries) - 1 for g in groups if g.reason != "similar")
    print()
    if copies:
        print(f"* = cleanest URL. {copies} extra copies; 'markfind duplicates --urls' lists them.")
    if any(g.reason == "similar" for g in groups):
        print(
            "Nearly-same pages may differ in ways that matter, such as a newer version;"
            " check them before deleting."
        )
    return 0


def _size(n: int) -> str:
    """A byte count for people: 512 B, 3.4 KB, 12.0 MB."""
    for unit in ("B", "KB", "MB"):
        if n < 1000 or unit == "MB":
            return f"{n} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000
    raise AssertionError("unreachable")


def cmd_archive(store: Store, args: argparse.Namespace) -> int:
    try:
        copy = store.saved_copy(args.url)
    except KeyError:
        return _not_in_markfind(args.url)
    if copy is None:
        print("no saved copy: this page has never been fetched successfully", file=sys.stderr)
        return 1
    if args.text:
        print(copy.text)
        return 0

    page = render(copy.url, copy.title, copy.saved_at, copy.html, copy.text_only)
    if args.output:
        path = Path(args.output)
    else:
        name = hashlib.sha256(copy.url.encode()).hexdigest()[:12]
        path = Path(tempfile.gettempdir()) / f"markfind-copy-{name}.html"
    try:
        path.write_text(page, encoding="utf-8")
    except OSError as exc:
        print(f"error: could not write {path}: {exc}", file=sys.stderr)
        return 1
    if args.output:
        print(f"saved copy written to {path}", file=sys.stderr)
    elif webbrowser.open(path.resolve().as_uri()):
        print(f"opened the saved copy in your browser: {path}")
    else:
        print(f"saved copy written to {path}; open it in your browser")
    return 0


def cmd_stats(store: Store, args: argparse.Namespace) -> int:
    s = store.stats()
    print(f"bookmarks: {s.total}")
    print(f"indexed:   {s.fetched}")
    breakdown = ", ".join(f"{kind} {n}" for kind, n in s.failed_by_kind.items())
    print(f"failed:    {s.failed}" + (f" ({breakdown})" if breakdown else ""))
    print(f"pending:   {s.total - s.fetched - s.failed}")
    print(f"saved copies: {s.saved_copies} ({_size(s.saved_bytes)})")
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

    if args.delete and args.urls:
        print("error: --delete can't be combined with --urls", file=sys.stderr)
        return 2
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
        if f.saved:
            details += ", saved copy"
        print(f"    {details}")
    saved = sum(f.saved for f in failures)
    if saved:
        print()
        print(
            f"{saved} of these {'has' if saved == 1 else 'have'} a saved copy;"
            " read it with 'markfind archive URL'."
        )
        if args.delete:
            print("Deleting a bookmark deletes its saved copy too.")
    if args.delete:
        return _delete_failures(store, failures, args.yes)
    return 0


def _confirm(question: str) -> bool | None:
    """Ask a yes/no question on the terminal; ``None`` if there is no terminal."""
    if not sys.stdin.isatty():
        return None
    try:
        answer = input(f"{question} [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


def _delete_failures(store: Store, failures: list[Failure], yes: bool) -> int:
    n = len(failures)
    print()
    if not yes:
        what = "this bookmark" if n == 1 else f"these {n} bookmarks"
        answer = _confirm(f"Delete {what} from markfind?")
        if answer is None:
            print(
                "error: not deleting without confirmation; add --yes to delete without being asked",
                file=sys.stderr,
            )
            return 1
        if not answer:
            print("nothing deleted")
            return 0
    deleted = store.delete(f.url for f in failures)
    print(f"deleted {deleted} {'bookmark' if deleted == 1 else 'bookmarks'} from markfind")
    print(STILL_IN_BROWSER_NOTE)
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
    p.add_argument(
        "--max-retry-wait",
        type=float,
        default=MAX_RETRY_WAIT,
        metavar="S",
        help="when a site says 'too many requests' (HTTP 429/503), wait up to S seconds"
        f" and retry (default: {MAX_RETRY_WAIT:.0f}, 0 disables)",
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
        "add",
        help="add a bookmark by its address and index the page",
        description=(
            "Add one or more web addresses and fetch their pages straight away."
            " A URL markfind already has keeps everything it already holds."
        ),
    )
    p.add_argument("urls", nargs="+", metavar="URL")
    p.add_argument("--folder", default="", help="file the new bookmarks under this folder")
    p.add_argument(
        "--tag", action="append", default=[], metavar="TAG", help="add this tag; repeat for several"
    )
    p.add_argument("--note", help="your own note about the bookmark")
    p.add_argument("--no-fetch", action="store_true", help="add without downloading the pages")
    _add_fetch_options(p)
    p.set_defaults(func=cmd_add)

    p = sub.add_parser(
        "import",
        help="import an HTML export or a browser's own bookmarks file (Chrome, Firefox, Safari)",
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
            " Vivaldi, Chromium, Firefox, Safari), import them, and fetch pages not fetched yet."
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
    p.add_argument(
        "--if-changed",
        action="store_true",
        help="ask the server first and skip pages it reports as unchanged",
    )
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
    p.add_argument("--folder", help="only this folder and its subfolders, e.g. 'Bookmarks bar/Dev'")
    p.add_argument("--tag", help="only bookmarks with this tag (ignoring case)")
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
    p.add_argument("--tag", help="only bookmarks with this tag (ignoring case)")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser(
        "tags",
        help="list tags and how many bookmarks have each",
        description=(
            "Lists tags, most used first: those imported from Firefox and those"
            " added with 'markfind tag'."
        ),
    )
    p.set_defaults(func=cmd_tags)

    p = sub.add_parser(
        "tag",
        help="add or remove your own tags on a bookmark",
        description=(
            "Add tags to a bookmark, or remove ones you added. Your tags are kept apart"
            " from the browser's, so importing never changes them. Without tags, shows"
            " the bookmark's tags."
        ),
    )
    p.add_argument("url", help="the bookmark's URL, as 'markfind list' or 'search' shows it")
    p.add_argument("tags", nargs="*", metavar="TAG", help="tags to add")
    p.add_argument(
        "--remove",
        nargs="+",
        metavar="TAG",
        help="remove these tags (only ones you added)",
    )
    p.set_defaults(func=cmd_tag)

    p = sub.add_parser(
        "note",
        help="write a searchable note on a bookmark",
        description=(
            "Write a note on a bookmark, replacing any earlier one. Notes are searchable"
            " and shown in search results. Without text, shows the note."
        ),
    )
    p.add_argument("url", help="the bookmark's URL, as 'markfind list' or 'search' shows it")
    p.add_argument("text", nargs="*", help="the note; quotes are optional")
    p.add_argument("--clear", action="store_true", help="remove the note")
    p.set_defaults(func=cmd_note)

    p = sub.add_parser(
        "topics",
        help="group bookmarks into topics by their words",
        description=(
            "Group indexed bookmarks into topics by the words their pages use, and"
            " name each topic by its most telling words. No folders or tags needed."
        ),
    )
    p.add_argument(
        "--count",
        type=_positive,
        metavar="N",
        help="how many topics to look for (default: chosen from the number of bookmarks)",
    )
    p.add_argument(
        "--min-size",
        type=_positive,
        default=MIN_SIZE,
        metavar="N",
        help=(
            f"smallest topic to show; smaller ones go under 'No clear topic' (default: {MIN_SIZE})"
        ),
    )
    p.add_argument("--folder", help="only this folder and its subfolders")
    p.add_argument("--json", action="store_true", help="print the topics as JSON")
    p.set_defaults(func=cmd_topics)

    p = sub.add_parser(
        "duplicates",
        help="find bookmarks saved more than once",
        description=(
            "Find bookmarks that are the same page: the same URL apart from cosmetic"
            " differences (http/https, www., tracking parameters, ...), the same"
            " page text, or nearly the same page text."
        ),
    )
    p.add_argument("--by", choices=REASONS, help="only this kind of duplicate (default: all three)")
    p.add_argument(
        "--similarity",
        type=_percent,
        default=round(MIN_SIMILARITY * 100),
        metavar="PCT",
        help="how much text pages must share to be nearly the same"
        f" (default: {MIN_SIMILARITY * 100:.0f}%%)",
    )
    p.add_argument(
        "--urls",
        action="store_true",
        help="print only the extra copies' URLs (all but the starred one), one per line;"
        " nearly-same pages only with --by similar",
    )
    p.set_defaults(func=cmd_duplicates)

    p = sub.add_parser(
        "archive",
        help="read the saved copy of a page, even after it's gone",
        description=(
            "Open what markfind saved of a bookmarked page in your web browser: a"
            " readable copy (text, headings, lists, tables and links) from the last"
            " successful fetch. Pages fetched before markfind saved copies show their"
            " indexed text instead."
        ),
    )
    p.add_argument("url", help="the bookmark's URL, as 'markfind list' or 'search' shows it")
    shown = p.add_mutually_exclusive_group()
    shown.add_argument(
        "-o", "--output", metavar="FILE", help="write the page to FILE instead of opening it"
    )
    shown.add_argument("--text", action="store_true", help="print the plain text instead")
    p.set_defaults(func=cmd_archive)

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
    p.add_argument("--tag", help="only bookmarks with this tag (ignoring case)")
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
    p.add_argument(
        "--delete",
        action="store_true",
        help="remove the listed bookmarks from markfind (not from your browser), after asking",
    )
    p.add_argument("-y", "--yes", action="store_true", help="with --delete, don't ask first")
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
