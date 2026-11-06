#!/usr/bin/env python3
"""How good is markfind's ranking on your own searches?

    python scripts/rank_eval.py QUERIES.json [--db PATH] [--grid]

QUERIES.json lists real searches and the bookmarks that should come first (see
rank_queries.example.json). The script reports each search's rank, mean
reciprocal rank (MRR) and hits@3 for the current RANK_WEIGHTS. With --grid, it
also tries every combination in GRID and says whether a change is justified.

Trying hundreds of combinations on a few searches will find one that wins by
chance, so the verdict doesn't rest on the best score. It uses leave-one-out:
each search is scored with the weights that did best on all the others.

The database is opened read-only, and nothing is written.
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from markfind import store as store_module
from markfind.store import SCHEMA_VERSION, Store, to_fts_query

COLUMNS = ("title", "url", "body", "folder", "tags", "note")
# Each column's current weight, and a step either way.
GRID = (
    (2.0, 5.0, 10.0),  # title
    (0.5, 1.0, 2.0),  # url
    (1.0, 2.0, 4.0),  # body
    (0.5, 1.0, 2.0),  # folder
    (2.0, 4.0, 8.0),  # tags
    (1.0, 3.0, 6.0),  # note
)
RESULTS = 100  # an expected bookmark further down than this counts as not found
MIN_QUERIES = 10  # fewer than this can't justify changing anything
MIN_GAIN = 0.05  # how much MRR must rise, out of 1, to be worth a change

Weights = tuple[float, ...]
Ranks = list[int | None]  # for each query, where the first expected bookmark came


@dataclass(frozen=True)
class Query:
    text: str
    expect: frozenset[str]


@dataclass(frozen=True)
class Verdict:
    change: bool
    weights: Weights  # the best weights found; the current ones if nothing beats them
    current_mrr: float
    best_mrr: float
    held_out_mrr: float  # leave-one-out: the honest estimate of best_mrr
    wins: int  # searches that rank better with `weights`
    losses: int
    reason: str


def load_queries(path: Path) -> list[Query]:
    """Read the query file. Raises ValueError saying what's wrong with it."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"can't read {path}: {exc}") from exc
    if not isinstance(data, list) or not data:
        raise ValueError(f'{path}: expected a list of {{"query": ..., "expect": [...]}}')
    queries = []
    for n, item in enumerate(data, 1):
        text = item.get("query") if isinstance(item, dict) else None
        expect = item.get("expect") if isinstance(item, dict) else None
        if (
            not isinstance(text, str)
            or not text.strip()
            or not isinstance(expect, list)
            or not expect
            or not all(isinstance(url, str) for url in expect)
        ):
            raise ValueError(f'entry {n}: needs a "query" and a non-empty "expect" list of URLs')
        queries.append(Query(text, frozenset(expect)))
    return queries


def rank(store: Store, query: Query, weights: Weights) -> int | None:
    """Where the first expected bookmark comes, from 1, searching as `markfind search` does."""
    saved = store_module.RANK_WEIGHTS
    store_module.RANK_WEIGHTS = weights
    try:
        results = store.search(to_fts_query(query.text), limit=RESULTS)
    finally:
        store_module.RANK_WEIGHTS = saved
    for position, result in enumerate(results, 1):
        if result.url in query.expect:
            return position
    return None


def missing_bookmarks(store: Store, queries: Sequence[Query]) -> list[str]:
    """Expected URLs that aren't in the database."""
    known = {row[0] for row in store.db.execute("SELECT url FROM bookmarks")}
    return sorted({url for q in queries for url in q.expect} - known)


def reciprocal(position: int | None) -> float:
    return 1 / position if position else 0.0


def mrr(ranks: Ranks) -> float:
    return sum(map(reciprocal, ranks)) / len(ranks) if ranks else 0.0


def hits(ranks: Ranks, within: int) -> int:
    return sum(1 for position in ranks if position and position <= within)


def leave_one_out(table: dict[Weights, Ranks], current: Weights) -> float:
    """MRR when each query is scored by the weights best on all the others.

    Ties go to the current weights, so only a real difference moves away from them.
    """
    totals = {w: sum(map(reciprocal, ranks)) for w, ranks in table.items()}
    count = len(table[current])
    total = 0.0
    for q in range(count):
        best = max(
            table, key=lambda w: (round(totals[w] - reciprocal(table[w][q]), 9), w == current)
        )
        total += reciprocal(table[best][q])
    return total / count if count else 0.0


def decide(table: dict[Weights, Ranks], current: Weights) -> Verdict:
    """Whether the evaluation justifies leaving the current weights."""
    now = table[current]
    best = max(table, key=lambda w: (round(mrr(table[w]), 9), w == current))
    wins = sum(reciprocal(b) > reciprocal(a) for a, b in zip(now, table[best], strict=True))
    losses = sum(reciprocal(b) < reciprocal(a) for a, b in zip(now, table[best], strict=True))
    held_out = leave_one_out(table, current)
    gain = held_out - mrr(now)
    if len(now) < MIN_QUERIES:
        change, reason = False, f"{len(now)} searches is too few; use at least {MIN_QUERIES}"
    elif best == current:
        change, reason = False, "nothing in the grid beats the current weights"
    elif gain < MIN_GAIN:
        change, reason = (
            False,
            (
                f"on searches they weren't chosen on, the best weights gain {gain:+.3f} MRR,"
                f" under the {MIN_GAIN} needed"
            ),
        )
    elif wins <= losses:
        change, reason = False, f"they help {wins} searches and hurt {losses}"
    else:
        change, reason = (
            True,
            (
                f"{gain:+.3f} MRR on searches they weren't chosen on;"
                f" {wins} searches rank better, {losses} worse"
            ),
        )
    return Verdict(change, best, mrr(now), mrr(table[best]), held_out, wins, losses, reason)


def describe(weights: Weights) -> str:
    return ", ".join(f"{name} {w:g}" for name, w in zip(COLUMNS, weights, strict=True))


def _report(queries: Sequence[Query], ranks: Ranks) -> None:
    for query, position in zip(queries, ranks, strict=True):
        print(f"  {position or '-':>3}  {query.text}")
    count = len(queries)
    print(f"MRR {mrr(ranks):.3f}, hits@1 {hits(ranks, 1)}/{count}, hits@3 {hits(ranks, 3)}/{count}")
    not_found = sum(1 for position in ranks if position is None)
    if not_found:
        print(
            f"{not_found} not found: weights can't fix these. Searches need every word to"
            " match, so check the page was fetched and uses those words."
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate markfind's ranking on your searches.")
    parser.add_argument("queries", type=Path, help="JSON file of searches and expected bookmarks")
    parser.add_argument("--db", type=Path, default=Path.home() / ".markfind.sqlite")
    parser.add_argument("--grid", action="store_true", help="also try other weights")
    args = parser.parse_args(argv)

    try:
        queries = load_queries(args.queries)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not args.db.is_file():
        print(f"error: no database at {args.db}", file=sys.stderr)
        return 2

    store = Store(args.db, readonly=True)
    try:
        version = store.db.execute("PRAGMA user_version").fetchone()[0]
        if version != SCHEMA_VERSION:
            print(
                f"error: database is at schema {version}, not {SCHEMA_VERSION};"
                " run 'markfind stats' once to upgrade it",
                file=sys.stderr,
            )
            return 2

        missing = missing_bookmarks(store, queries)
        for url in missing:
            print(f"not in the database: {url}")
        usable = [q for q in queries if q.expect - set(missing)]
        if len(usable) < len(queries):
            print(f"leaving out {len(queries) - len(usable)} searches with no bookmark to find")
        if not usable:
            print("error: no searches left to evaluate", file=sys.stderr)
            return 2

        current = tuple(store_module.RANK_WEIGHTS)
        print(f"Current weights: {describe(current)}")
        ranks = [rank(store, q, current) for q in usable]
        _report(usable, ranks)
        if not args.grid:
            return 0

        table = {current: ranks}
        for weights in itertools.product(*GRID):
            if weights not in table:
                table[weights] = [rank(store, q, weights) for q in usable]
        verdict = decide(table, current)
        print(f"\nTried {len(table)} combinations.")
        print(f"Best: {describe(verdict.weights)}")
        print(f"  MRR {verdict.best_mrr:.3f} (current {verdict.current_mrr:.3f})")
        print(f"  on searches not used to choose them: {verdict.held_out_mrr:.3f}")
        print(f"Verdict: {'change' if verdict.change else 'keep'} the weights: {verdict.reason}.")
        if verdict.change:
            _report(usable, table[verdict.weights])
        return 0
    finally:
        store.db.close()


if __name__ == "__main__":
    sys.exit(main())
