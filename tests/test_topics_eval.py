"""scripts/topics_eval.py: the scoring behind the settings in topics.py."""

import importlib.util
import sys
from pathlib import Path

import pytest

from markfind.importers import load_file

SCRIPT = Path(__file__).parent.parent / "scripts" / "topics_eval.py"
spec = importlib.util.spec_from_file_location("topics_eval", SCRIPT)
topics_eval = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = topics_eval  # dataclasses and friends look the module up here
spec.loader.exec_module(topics_eval)


def test_a_perfect_grouping_scores_one():
    index, purity = topics_eval.score([["a", "a"], ["b", "b", "b"]], [])
    assert index == pytest.approx(1) and purity == 1


def test_mixed_subjects_score_lower():
    index, purity = topics_eval.score([["a", "a", "b", "b"], ["c", "c"]], [])
    assert 0 < index < 1 and purity == pytest.approx(4 / 6)


def test_everything_in_one_group_scores_zero():
    index, purity = topics_eval.score([["a", "a", "b", "b"]], [])
    assert index == pytest.approx(0) and purity == 0.5


def test_pages_with_no_topic_count_against_the_index_but_not_purity():
    whole = topics_eval.score([["a", "a", "a"], ["b", "b", "b"]], [])
    partial = topics_eval.score([["a", "a"], ["b", "b", "b"]], ["a"])
    assert partial[0] < whole[0] and partial[1] == whole[1] == 1


def test_nothing_grouped():
    assert topics_eval.score([], ["a", "b"])[1] == 0


@pytest.mark.parametrize("name", topics_eval.SETS)
def test_sets_import_as_bookmarks(tmp_path, name):
    corpus = topics_eval.SETS[name]
    path = tmp_path / "set.html"
    path.write_text(topics_eval.bookmarks_html(corpus), encoding="utf-8")
    imported = load_file(path)
    assert {b.folder for b in imported} == set(corpus)
    assert {b.url for b in imported} == {url for urls in corpus.values() for url in urls}


def test_sets_are_separate():
    urls = [{u for urls in corpus.values() for u in urls} for corpus in topics_eval.SETS.values()]
    assert sum(map(len, urls)) == len(set().union(*urls))


def test_score_needs_a_database(tmp_path, capsys):
    assert topics_eval.main(["score", str(tmp_path / "none.sqlite")]) == 2
    assert "no database" in capsys.readouterr().err
