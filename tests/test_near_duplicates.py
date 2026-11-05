"""Near-duplicate pages: mostly the same text, not identical."""

import random

import pytest

from markfind import cli
from markfind.duplicates import find_duplicates, near_pairs, shingles, similarity
from markfind.extract import Page


def text(seed, words=300):
    rnd = random.Random(seed)
    return " ".join(f"w{rnd.randrange(5000)}" for _ in range(words))


def edit(text, changes, seed=0):
    """The text with ``changes`` words replaced, like a page after a small update."""
    rnd = random.Random(seed)
    words = text.split()
    for _ in range(changes):
        words[rnd.randrange(len(words))] = f"new{rnd.randrange(1000)}"
    return " ".join(words)


ARTICLE = text(1)


def page(url, title="", folder="", body=""):
    return (url, title, folder, body)


def test_shingles_and_similarity():
    a = shingles("One two three four five six")
    assert len(a) == 2
    assert a == shingles("  ONE two\nthree four   five SIX ")
    assert similarity(a, a) == 1.0
    assert similarity(a, shingles("seven eight nine ten eleven")) == 0.0
    assert similarity(a, frozenset()) == 0.0
    assert shingles("too few words") == frozenset()
    # 300 words make 296 phrases. A changed word is in 5 of them: 291 shared,
    # 5 old and 5 new ones, so 291 / 301.
    assert similarity(shingles(ARTICLE), shingles(edit(ARTICLE, 1))) == pytest.approx(291 / 301)


def test_near_pairs():
    sets = [shingles(t) for t in (ARTICLE, text(2), edit(ARTICLE, 3), text(3))]
    pairs = near_pairs(sets)
    assert [(i, j) for i, j, _ in pairs] == [(0, 2)]
    assert 0.8 < pairs[0][2] < 1
    assert near_pairs(sets, min_similarity=0.999) == []


def test_long_pages_are_sampled_and_still_found():
    long_article = text(4, words=3000)
    sets = [shingles(long_article), shingles(edit(long_article, 20)), shingles(text(5, 3000))]
    assert [(i, j) for i, j, _ in near_pairs(sets)] == [(0, 1)]


def test_groups():
    groups = find_duplicates(
        [
            page("https://blog.example/post", "My post", "Blog", ARTICLE),
            page("https://mirror.example/post", "My post (mirror)", "Old", edit(ARTICLE, 4)),
            page("https://other.example/", "Other", "", text(6)),
        ]
    )
    assert [(g.reason, [e.url for e in g.entries]) for g in groups] == [
        ("similar", ["https://blog.example/post", "https://mirror.example/post"])
    ]
    assert 0.8 <= groups[0].similarity < 1
    assert (
        find_duplicates(
            [page("https://a.example/", body=ARTICLE), page("https://b.example/", body=ARTICLE)]
        )[0].similarity
        is None
    )


def test_identical_and_near_copies_together():
    rows = [
        page("https://a.example/", body=ARTICLE),
        page("https://b.example/", body=ARTICLE),
        page("https://c.example/", body=edit(ARTICLE, 4)),
    ]
    groups = find_duplicates(rows)
    assert [g.reason for g in groups] == ["text", "similar"]
    # The identical pair is one page for the near match, so all three show once.
    assert len(groups[1].entries) == 3


def test_same_url_is_not_repeated_as_similar():
    rows = [
        page("http://www.example.com/post", body=ARTICLE),
        page("https://example.com/post", body=edit(ARTICLE, 4)),
    ]
    assert [g.reason for g in find_duplicates(rows)] == ["url"]
    # Without URL matching, the pages still match by text.
    assert [g.reason for g in find_duplicates(rows, by=("similar",))] == ["similar"]


def test_threshold_and_short_pages():
    changed = edit(ARTICLE, 25)
    alike = similarity(shingles(ARTICLE), shingles(changed))
    assert 0.3 < alike < 0.8
    rows = [page("https://a.example/", body=ARTICLE), page("https://b.example/", body=changed)]
    assert find_duplicates(rows) == []
    groups = find_duplicates(rows, min_similarity=alike)
    assert [(g.reason, g.similarity) for g in groups] == [("similar", alike)]

    short = " ".join(f"w{i}" for i in range(30))
    rows = [page("https://a.example/", body=short), page("https://b.example/", body=short + " x")]
    assert find_duplicates(rows) == []  # under MIN_WORDS: likely error or login pages
    assert find_duplicates(rows, by=("url", "text", "similar"), min_words=10) != []


def test_by_text_leaves_out_near_duplicates():
    rows = [
        page("https://a.example/", body=ARTICLE),
        page("https://b.example/", body=edit(ARTICLE, 4)),
    ]
    assert find_duplicates(rows, by=("url", "text")) == []


@pytest.fixture
def db(tmp_path, monkeypatch, capsys):
    db = str(tmp_path / "db.sqlite")
    export = tmp_path / "bookmarks.html"
    export.write_text(
        """<DL><p>
        <DT><A HREF="https://blog.example/post">My post</A>
        <DT><A HREF="https://mirror.example/post">My post (mirror)</A>
        <DT><A HREF="https://other.example/">Other</A>
        </DL><p>""",
        encoding="utf-8",
    )
    bodies = {
        "https://blog.example/post": ARTICLE,
        "https://mirror.example/post": edit(ARTICLE, 4),
        "https://other.example/": text(6),
    }
    monkeypatch.setattr(cli, "fetch_page", lambda url, timeout=10.0: Page("", bodies[url]))
    cli.main(["--db", db, "import", str(export)])
    cli.main(["--db", db, "fetch", "--delay", "0", "-q"])
    capsys.readouterr()
    return db


def test_cli(db, capsys):
    assert cli.main(["--db", db, "duplicates"]) == 0
    out = capsys.readouterr().out
    assert "Nearly the same page text (1):" in out
    assert "% the same)" in out and "My post" in out
    assert "check them before deleting" in out
    assert "extra copies" not in out  # nothing exact to delete

    # Near duplicates may be versions worth keeping: --urls lists them only by name.
    assert cli.main(["--db", db, "duplicates", "--urls"]) == 0
    assert capsys.readouterr().out == ""
    assert cli.main(["--db", db, "duplicates", "--urls", "--by", "similar"]) == 0
    assert capsys.readouterr().out.split() == ["https://mirror.example/post"]

    assert cli.main(["--db", db, "duplicates", "--similarity", "99"]) == 0
    assert "no duplicates found" in capsys.readouterr().out


@pytest.mark.parametrize("value", ["0", "101", "abc"])
def test_cli_rejects_bad_similarity(db, capsys, value):
    with pytest.raises(SystemExit):
        cli.main(["--db", db, "duplicates", "--similarity", value])
    assert "invalid percentage" in capsys.readouterr().err
