"""Topics: grouping bookmarks by the words their pages use."""

import json
import random
from collections import Counter

import pytest

from markfind import cli, topics
from markfind.importers import Bookmark
from markfind.store import Store
from markfind.topics import Clusters, cluster, default_count, vectors

SUBJECTS = {
    "cooking": "recipes oven flour butter sugar baking dough simmer garlic onions sauce pasta",
    "astronomy": "telescope galaxy planets orbit nebula stars comets asteroid eclipse lunar",
    "databases": "queries index tables sqlite postgres transactions schema joins columns",
}


def pages(per_subject=8, seed=1, subjects=SUBJECTS, signal=0.8):
    """(url, title, folder, text) for pages using their subject's words signal of the time.

    These only check the mechanics. Quality has to be judged on real pages, where
    settings that were perfect here did badly; see scripts/topics_eval.py.
    """
    rnd = random.Random(seed)
    filler = [f"filler{i}" for i in range(400)]
    result = []
    for name, vocabulary in subjects.items():
        words = vocabulary.split()
        for i in range(per_subject):
            text = [
                rnd.choice(words) if rnd.random() < signal else rnd.choice(filler)
                for _ in range(150)
            ]
            result.append((f"https://{name}.example/{i}", f"Page {i}", name, " ".join(text)))
    return result


def term_counts(rows):
    return [Counter(text.split()) for *_, text in rows]


# --- vectors and clustering ---------------------------------------------------------


def test_vectors():
    docs = [
        Counter({"a": 3, "b": 1, "only": 5, "x": 1}),
        Counter({"a": 1, "b": 2}),
        Counter({"c": 1, "x": 1}),
    ]
    vecs = vectors(docs)
    # "only" is on one page and can't group anything; with three pages, every
    # term on 2+ pages is still allowed.
    assert "only" not in vecs[0]
    assert set(vecs[0]) == {"a", "b", "x"}
    for v in vecs:
        assert sum(w * w for w in v.values()) == pytest.approx(1.0) or v == {}
    assert vecs[0]["a"] > vecs[0]["b"]  # used more often on that page


def test_terms_on_most_pages_describe_nothing(monkeypatch):
    docs = [Counter({"everywhere": 1, f"t{i // 2}": 1}) for i in range(10)]
    assert all("everywhere" not in v for v in vectors(docs))


def test_terms_per_page_are_capped(monkeypatch):
    monkeypatch.setattr(topics, "TERMS_PER_PAGE", 2)
    docs = [Counter({f"t{i}": i + 1 for i in range(5)})] * 3 + [Counter({"z": 1})]
    assert all(len(v) <= 2 for v in vectors(docs))


def test_cluster_finds_the_subjects():
    rows = pages()
    for count in (3, None):  # given, and chosen automatically
        found = cluster(vectors(term_counts(rows)), count=count)
        groups = [sorted({rows[i][2] for i in group}) for group in found.groups]
        assert sorted(groups) == [["astronomy"], ["cooking"], ["databases"]], count
        assert found.other == []
        assert all(len(label) == 3 for label in found.labels)


def test_automatic_count_merges_split_subjects():
    # Too many topics asked for: a subject gets split...
    rows = pages(per_subject=12)
    vecs = vectors(term_counts(rows))
    assert len(cluster(vecs, count=6).groups) > 3
    # ...but when markfind picks the count, halves of a subject are merged.
    assert default_count(len(vecs)) == 12
    assert len(cluster(vecs).groups) == 3


def test_cluster_is_deterministic():
    vecs = vectors(term_counts(pages(seed=4)))
    assert cluster(vecs) == cluster(vecs)


def test_small_topics_and_loners_go_to_other():
    rows = pages(per_subject=8)
    vecs = vectors(term_counts(rows))
    found = cluster(vecs, count=3, min_size=9)
    assert found.groups == [] and sorted(found.other) == list(range(len(rows)))
    # A page sharing no words with the rest has no topic.
    found = cluster([*vecs, {}], count=3)
    assert found.other == [len(vecs)]


def test_cluster_edge_cases():
    assert cluster([]) == Clusters([], [], [])
    assert cluster([{}, {}]) == Clusters([], [], [0, 1])
    assert (
        default_count(1) == 1
        and default_count(30) == 10
        and default_count(10**6) == topics.MAX_TOPICS
    )


# --- from the store ---------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "db.sqlite"
    rows = pages()
    with Store(path) as store:
        store.add_bookmarks([Bookmark(url, title, folder) for url, title, folder, _ in rows])
        store.add_bookmarks([Bookmark("https://unfetched.example/", "Not fetched")])
        ids = {url: id_ for id_, url in store.pending()}
        for url, _, _, text in rows:
            store.set_page(ids[url], text)
    return str(path)


def test_store_topics(db):
    with Store(db) as store:
        found = store.topics()
    assert len(found.topics) == 3 and found.other == [] and found.unindexed == 1
    by_folder = {t.entries[0].folder: t for t in found.topics}
    assert all({e.folder for e in t.entries} == {folder} for folder, t in by_folder.items())
    # Labels are real words, as the pages spell them, not stems.
    vocabulary = set(SUBJECTS["astronomy"].split())
    assert set(by_folder["astronomy"].label) <= vocabulary
    assert "planets" in vocabulary and "planet" not in by_folder["astronomy"].label


def test_store_topics_in_a_folder(db):
    with Store(db) as store:
        found = store.topics(folder="cooking")
    assert [{e.folder for e in t.entries} for t in found.topics] == [{"cooking"}]
    assert found.unindexed == 0


def test_cli_topics(db, capsys):
    assert cli.main(["--db", db, "topics"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("3 topics in 24 indexed bookmarks\n")
    assert out.count(" (8):\n") == 3
    assert "    https://cooking.example/0\n" in out
    assert "1 bookmark without page text isn't included" in out

    assert cli.main(["--db", db, "topics", "--json", "--count", "3"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data["topics"]) == 3 and data["unindexed"] == 1
    assert set(data["topics"][0]["bookmarks"][0]) == {"url", "title", "folder"}

    assert cli.main(["--db", db, "topics", "--min-size", "9"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("0 topics in 24") and "No clear topic (24):" in out


@pytest.mark.parametrize("value", ["0", "-2", "many"])
def test_cli_rejects_bad_numbers(db, capsys, value):
    with pytest.raises(SystemExit):
        cli.main(["--db", db, "topics", "--count", value])
    assert "invalid number" in capsys.readouterr().err


def test_cli_without_indexed_pages(tmp_path, capsys):
    assert cli.main(["--db", str(tmp_path / "empty.sqlite"), "topics"]) == 1
    assert "run 'markfind fetch' first" in capsys.readouterr().out
