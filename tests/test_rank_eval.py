"""scripts/rank_eval.py: scoring searches and deciding whether to change the weights."""

import importlib.util
import json
from pathlib import Path

import pytest

from markfind import store as store_module
from markfind.importers import Bookmark
from markfind.store import RANK_WEIGHTS, Store

SCRIPT = Path(__file__).parent.parent / "scripts" / "rank_eval.py"
spec = importlib.util.spec_from_file_location("rank_eval", SCRIPT)
rank_eval = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rank_eval)

CURRENT = (5.0, 1.0, 2.0, 1.0, 4.0, 3.0)
OTHER = (2.0, 2.0, 1.0, 1.0, 4.0, 3.0)


def test_grid_contains_the_current_weights():
    assert all(w in values for w, values in zip(RANK_WEIGHTS, rank_eval.GRID, strict=True))


# --- metrics and the decision ---------------------------------------------------------


def test_metrics():
    ranks = [1, 2, None, 4]
    assert rank_eval.mrr(ranks) == pytest.approx((1 + 0.5 + 0 + 0.25) / 4)
    assert rank_eval.hits(ranks, 1) == 1 and rank_eval.hits(ranks, 3) == 2
    assert rank_eval.mrr([]) == 0


def test_a_clear_gain_changes_the_weights():
    table = {CURRENT: [2] * 10, OTHER: [1] * 9 + [2]}
    verdict = rank_eval.decide(table, CURRENT)
    assert verdict.change and verdict.weights == OTHER
    assert verdict.wins == 9 and verdict.losses == 0
    assert verdict.best_mrr == pytest.approx(0.95) and verdict.current_mrr == pytest.approx(0.5)


def test_too_few_searches_never_change_anything():
    verdict = rank_eval.decide({CURRENT: [2] * 9, OTHER: [1] * 9}, CURRENT)
    assert not verdict.change and "too few" in verdict.reason


def test_ties_keep_the_current_weights():
    verdict = rank_eval.decide({OTHER: [1, 2] * 5, CURRENT: [1, 2] * 5}, CURRENT)
    assert not verdict.change and verdict.weights == CURRENT


def test_a_small_gain_is_not_enough():
    verdict = rank_eval.decide({CURRENT: [1] * 9 + [2], OTHER: [1] * 10}, CURRENT)
    assert verdict.weights == OTHER and verdict.best_mrr > verdict.current_mrr
    assert not verdict.change and "needed" in verdict.reason


def test_a_gain_that_hurts_more_searches_than_it_helps_is_not_enough():
    # Four big wins lift MRR by enough, even held out, but five searches get worse.
    now = [10] * 4 + [1] * 16
    other = [1] * 4 + [2] * 5 + [1] * 11
    verdict = rank_eval.decide({CURRENT: now, OTHER: other}, CURRENT)
    assert verdict.held_out_mrr - verdict.current_mrr >= rank_eval.MIN_GAIN
    assert verdict.wins == 4 and verdict.losses == 5
    assert not verdict.change and "hurt 5" in verdict.reason


def test_chance_wins_do_not_survive_leave_one_out():
    # Twenty candidates, each lucky on exactly one search and no better elsewhere.
    count = 20
    table = {CURRENT: [2] * count}
    for q in range(count):
        table[(float(q),) * 6] = [1 if i == q else 2 for i in range(count)]
    verdict = rank_eval.decide(table, CURRENT)
    assert verdict.best_mrr > verdict.current_mrr  # the best looks like a gain...
    assert verdict.held_out_mrr == pytest.approx(0.5)  # ...which held-out searches don't see
    assert not verdict.change


# --- against a real index ---------------------------------------------------------------


def make_db(path, pages):
    """pages: (url, title, folder, text)."""
    with Store(path) as store:
        store.add_bookmarks([Bookmark(url, title, folder) for url, title, folder, _ in pages])
        ids = {url: id_ for id_, url in store.pending()}
        for url, _, _, text in pages:
            store.set_page(ids[url], text)


FILLER = "alpha beta gamma delta epsilon zeta eta theta iota kappa " * 5


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "db.sqlite"
    pages = []
    for i in range(12):
        word = f"subject{i}"
        pages.append((f"https://{word}.example/", f"About {word}", "A", FILLER))
        pages.append((f"https://noise{i}.example/", "Notes", "B", f"{word} {word} {word} {FILLER}"))
    make_db(path, pages)
    return path


def write_queries(tmp_path, items):
    path = tmp_path / "queries.json"
    path.write_text(json.dumps(items), encoding="utf-8")
    return path


def title_queries(tmp_path):
    return write_queries(
        tmp_path,
        [{"query": f"subject{i}", "expect": [f"https://subject{i}.example/"]} for i in range(12)],
    )


def test_report_ranks_with_the_current_weights(db, tmp_path, capsys):
    assert rank_eval.main([str(title_queries(tmp_path)), "--db", str(db)]) == 0
    out = capsys.readouterr().out
    assert "Current weights: title 5, url 1, body 2, folder 1, tags 4, note 3" in out
    assert "subject3" in out and "hits@3 12/12" in out
    assert "Tried" not in out


def test_weights_are_put_back(db):
    with Store(db, readonly=True) as store:
        rank_eval.rank(store, rank_eval.Query("subject1", frozenset({"x"})), OTHER)
    assert store_module.RANK_WEIGHTS == RANK_WEIGHTS


def test_weights_change_the_order(db):
    query = rank_eval.Query("subject1", frozenset({"https://subject1.example/"}))
    with Store(db, readonly=True) as store:
        favouring_title = rank_eval.rank(store, query, (100.0, 1.0, 1.0, 1.0, 1.0, 1.0))
        favouring_body = rank_eval.rank(store, query, (1.0, 0.1, 100.0, 1.0, 1.0, 1.0))
    assert favouring_title == 1 and favouring_body == 2


def test_grid_finds_a_real_improvement(db, tmp_path, capsys, monkeypatch):
    # These searches want the body match, which a body-heavy weighting favours.
    queries = write_queries(
        tmp_path,
        [{"query": f"subject{i}", "expect": [f"https://noise{i}.example/"]} for i in range(12)],
    )
    # A small grid, starting from weights that favour titles heavily.
    monkeypatch.setattr(store_module, "RANK_WEIGHTS", (100.0, 0.1, 1.0, 1.0, 1.0, 1.0))
    monkeypatch.setattr(
        rank_eval, "GRID", ((100.0, 1.0), (0.1,), (1.0, 100.0), (1.0,), (1.0,), (1.0,))
    )
    assert rank_eval.main([str(queries), "--db", str(db), "--grid"]) == 0
    out = capsys.readouterr().out
    assert "Tried 4 combinations" in out
    assert "Verdict: change the weights" in out
    best = next(line for line in out.splitlines() if line.startswith("Best:"))
    assert "body 100" in best


def test_grid_keeps_weights_that_are_already_right(db, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(rank_eval, "GRID", ((2.0, 5.0), (1.0,), (1.0, 2.0), (1.0,), (4.0,), (3.0,)))
    assert rank_eval.main([str(title_queries(tmp_path)), "--db", str(db), "--grid"]) == 0
    assert "Verdict: keep the weights" in capsys.readouterr().out


def test_not_found_is_called_out(db, tmp_path, capsys):
    queries = write_queries(
        tmp_path,
        [{"query": "subject1 nosuchword", "expect": ["https://subject1.example/"]}],
    )
    assert rank_eval.main([str(queries), "--db", str(db)]) == 0
    out = capsys.readouterr().out
    assert "MRR 0.000" in out and "1 not found: weights can't fix these" in out


def test_any_expected_bookmark_counts(db):
    query = rank_eval.Query(
        "subject1", frozenset({"https://noise1.example/", "https://subject1.example/"})
    )
    with Store(db, readonly=True) as store:
        assert rank_eval.rank(store, query, RANK_WEIGHTS) == 1


def test_urls_not_in_the_database_are_reported_and_left_out(db, tmp_path, capsys):
    queries = write_queries(
        tmp_path,
        [
            {"query": "subject1", "expect": ["https://subject1.example/"]},
            {"query": "subject2", "expect": ["https://gone.example/"]},
        ],
    )
    assert rank_eval.main([str(queries), "--db", str(db)]) == 0
    out = capsys.readouterr().out
    assert "not in the database: https://gone.example/" in out
    assert "leaving out 1 searches" in out and "hits@3 1/1" in out

    only_missing = write_queries(tmp_path, [{"query": "x", "expect": ["https://gone.example/"]}])
    assert rank_eval.main([str(only_missing), "--db", str(db)]) == 2
    assert "no searches left" in capsys.readouterr().err


def test_nothing_is_written(db, tmp_path):
    before = db.read_bytes()
    assert rank_eval.main([str(title_queries(tmp_path)), "--db", str(db)]) == 0
    assert db.read_bytes() == before
    assert not db.with_name(db.name + "-wal").exists()


def test_an_old_database_is_not_upgraded(db, tmp_path, capsys):
    with Store(db) as store:
        store.db.execute("PRAGMA user_version = 3")
        store.db.commit()
    before = db.read_bytes()
    assert rank_eval.main([str(title_queries(tmp_path)), "--db", str(db)]) == 2
    assert "run 'markfind stats' once" in capsys.readouterr().err
    assert db.read_bytes() == before


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[]",
        '{"query": "x"}',
        '[{"query": "x"}]',
        '[{"query": "", "expect": ["u"]}]',
        '[{"query": "x", "expect": []}]',
        '[{"query": "x", "expect": "u"}]',
        '[{"query": "x", "expect": [1]}]',
        '["x"]',
    ],
)
def test_bad_query_files(db, tmp_path, capsys, content):
    path = tmp_path / "bad.json"
    path.write_text(content, encoding="utf-8")
    assert rank_eval.main([str(path), "--db", str(db)]) == 2
    assert "error:" in capsys.readouterr().err


def test_no_database(tmp_path, capsys):
    assert rank_eval.main([str(title_queries(tmp_path)), "--db", str(tmp_path / "none")]) == 2
    assert "no database" in capsys.readouterr().err
    assert not (tmp_path / "none").exists()


def test_the_example_file_is_valid():
    assert rank_eval.load_queries(SCRIPT.parent / "rank_queries.example.json")
