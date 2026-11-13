"""Ignoring case has to mean ignoring case, not ignoring ASCII case.

``--folder`` and ``--tag`` are documented as matching whatever the capitals
look like, but the comparison happens in SQLite, whose ``lower()`` and ``LIKE``
fold ``A``-``Z`` and leave every other letter alone. These tests state the rule
as an invariant over a whole collection: shouting a folder or tag name, in any
alphabet, must not change which bookmarks come back.
"""

import pytest

from markfind import cli
from markfind.importers import Bookmark, clean_tags
from markfind.store import Store, _casefold

# Folders and tags that tell Unicode folding apart from ASCII folding: an
# accent, Greek (whose capital sigma lowercases two ways), a German sharp s,
# and a dotted capital I.
BOOKMARKS = [
    ("https://a.example/", "Reading", ["Beans"]),
    ("https://b.example/", "Reading/Cafés", ["Café"]),
    ("https://c.example/", "Reading/Cafés/Wien", ["Café", "Kaffee"]),
    ("https://d.example/", "Σίγμα", ["Σίγμα"]),
    ("https://e.example/", "Σίγμα/Οδός", ["Οδός"]),
    ("https://f.example/", "Straße", ["Straße"]),
    ("https://g.example/", "İstanbul", ["İstanbul"]),
    ("https://h.example/", "Deals/100%_off", ["Café"]),  # LIKE wildcards in the name
    ("https://i.example/", "Deals/100xxoff", []),
    ("https://j.example/", "", []),
]
FOLDERS = sorted({folder for _, folder, _ in BOOKMARKS if folder})
TAGS = sorted({tag for _, _, tags in BOOKMARKS for tag in tags})

NARROWINGS = [
    pytest.param(lambda store, **kw: store.list_bookmarks(**kw), id="list_bookmarks"),
    pytest.param(lambda store, **kw: store.records(**kw), id="records"),
    pytest.param(lambda store, **kw: store.search("indexing", **kw), id="search"),
]


def variants(text):
    """The same name, shouted and whispered."""
    return sorted({text, text.upper(), text.lower(), text.casefold(), text.swapcase()})


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    path = tmp_path_factory.mktemp("case") / "db.sqlite"
    with Store(path) as store:
        store.add_bookmarks(Bookmark(url, "Guide", folder) for url, folder, _ in BOOKMARKS)
        for id_, _ in store.pending():
            store.set_page(id_, "a guide to indexing and to searching what was indexed")
        for url, _, tags in BOOKMARKS:
            if tags:
                store.add_tags(url, tags)
    return path


@pytest.fixture
def store(db):
    with Store(db, readonly=True) as store:
        yield store


# --- the invariant, over every folder and tag in the collection -------------------


@pytest.mark.parametrize("folder", FOLDERS)
def test_a_folder_matches_the_same_bookmarks_however_it_is_capitalized(store, folder):
    expected = sorted(r.url for r in store.records(folder=folder))
    assert expected, f"{folder} should match something to begin with"
    for spelling in variants(folder):
        got = sorted(r.url for r in store.records(folder=spelling))
        assert got == expected, f"--folder {spelling!r} saw {got}, not {expected}"


@pytest.mark.parametrize("tag", TAGS)
def test_a_tag_matches_the_same_bookmarks_however_it_is_capitalized(store, tag):
    expected = sorted(r.url for r in store.records(tag=tag))
    assert expected, f"{tag} should match something to begin with"
    for spelling in variants(tag):
        got = sorted(r.url for r in store.records(tag=spelling))
        assert got == expected, f"--tag {spelling!r} saw {got}, not {expected}"


@pytest.mark.parametrize("narrow", NARROWINGS)
@pytest.mark.parametrize("folder", FOLDERS)
def test_every_way_of_narrowing_by_folder_agrees(store, narrow, folder):
    expected = len(narrow(store, folder=folder))
    assert expected
    assert len(narrow(store, folder=folder.upper())) == expected
    assert len(narrow(store, folder=folder.lower())) == expected


@pytest.mark.parametrize("narrow", NARROWINGS)
@pytest.mark.parametrize("tag", TAGS)
def test_every_way_of_narrowing_by_tag_agrees(store, narrow, tag):
    expected = len(narrow(store, tag=tag))
    assert expected
    assert len(narrow(store, tag=tag.upper())) == expected
    assert len(narrow(store, tag=tag.lower())) == expected


def test_topics_narrows_by_folder_the_same_way(store):
    whole = store.topics(folder="Reading", min_size=2)
    shouted = store.topics(folder="READING", min_size=2)
    assert [t.urls for t in whole.topics] == [t.urls for t in shouted.topics]
    assert [e.url for e in whole.other] == [e.url for e in shouted.other]
    assert whole.unindexed == shouted.unindexed


# --- the particular failures this comes from --------------------------------------


def test_a_shouted_accent_finds_the_tag(store):
    """``--tag CAFÉ`` used to find nothing, though ``--tag café`` found three."""
    assert len(store.records(tag="CAFÉ")) == 3
    assert len(store.records(tag="café")) == 3


def test_a_folder_is_matched_whole_or_not_at_all(store):
    """The halves of the old condition disagreed, so a mistyped case lost a subset."""
    assert len(store.records(folder="Reading")) == 3
    # Not 2: the bookmark sitting in Reading itself used to drop out, because
    # ``folder = ?`` was case-sensitive while ``folder LIKE ?`` was not.
    assert len(store.records(folder="reading")) == 3


def test_greek_capitals_fold(store):
    assert len(store.records(tag="ΣΊΓΜΑ")) == 1
    assert len(store.records(folder="ΣΊΓΜΑ")) == 2
    assert len(store.records(tag="ΟΔΌΣ")) == 1


def test_sharp_s_and_ss_are_the_same_name(store):
    """What casefold() buys over lower(): ß folds to ss, so STRASSE finds Straße."""
    assert [r.url for r in store.records(tag="STRASSE")] == ["https://f.example/"]
    assert [r.url for r in store.records(folder="strasse")] == ["https://f.example/"]


def test_wildcards_in_a_folder_name_are_still_literal(store):
    """``%`` and ``_`` are LIKE wildcards; folding the name must not unescape them."""
    assert [r.url for r in store.records(folder="deals/100%_off")] == ["https://h.example/"]
    assert [r.url for r in store.records(folder="DEALS/100%_OFF")] == ["https://h.example/"]
    assert store.records(folder="Deals/100xx_off") == []


def test_a_folder_is_not_matched_by_part_of_its_name(store):
    """One prefix test now does the work of two conditions; it must not overreach."""
    assert len(store.records(folder="Reading/Cafés")) == 2  # Cafés and Cafés/Wien
    assert store.records(folder="Reading/Caf") == []
    assert store.records(folder="eading") == []
    assert store.records(folder="Reading/") == []


def test_no_folder_and_no_tag_still_mean_everything(store):
    assert len(store.records()) == len(BOOKMARKS)
    for empty in (None, "", "   "):
        assert len(store.records(tag=empty)) == len(BOOKMARKS)
    for empty in (None, ""):
        assert len(store.records(folder=empty)) == len(BOOKMARKS)


# --- the Python half of the same rule ---------------------------------------------


def test_the_sql_function_is_there_on_a_read_only_connection(store):
    assert store.db.execute("SELECT casefold(?)", ("STRASSE",)).fetchone()[0] == "strasse"
    assert store.db.execute("SELECT casefold(?)", ("CAFÉ",)).fetchone()[0] == "café"
    assert store.db.execute("SELECT casefold(NULL)").fetchone()[0] is None


def test_casefold_leaves_what_is_not_text_alone():
    assert _casefold(None) is None
    assert _casefold(7) == 7


def test_the_same_tag_shouted_is_not_a_second_tag(tmp_path):
    with Store(tmp_path / "db.sqlite") as store:
        store.add_bookmark("https://a.example/")
        assert store.add_tags("https://a.example/", ["Café"]) == ("Café",)
        assert store.add_tags("https://a.example/", ["CAFÉ"]) == ()
        assert store.bookmark_tags("https://a.example/").mine == ("Café",)


def test_a_tag_can_be_removed_by_any_spelling(tmp_path):
    with Store(tmp_path / "db.sqlite") as store:
        store.add_bookmark("https://a.example/")
        store.add_tags("https://a.example/", ["Straße"])
        assert store.remove_tags("https://a.example/", ["STRASSE"]) == ("Straße",)
        assert store.bookmark_tags("https://a.example/").mine == ()


def test_tags_counts_spellings_as_one(store):
    counts = dict(store.tags())
    assert counts["Café"] == 3
    assert "CAFÉ" not in counts and "café" not in counts


def test_clean_tags_drops_a_repeat_in_other_capitals():
    assert clean_tags(["Café", "CAFÉ", "café"]) == ("Café",)
    assert clean_tags(["Straße", "STRASSE"]) == ("Straße",)


# --- through the command line ------------------------------------------------------


@pytest.mark.parametrize("folder", ["Reading", "reading", "READING"])
def test_cli_list_folder(db, folder, capsys):
    assert cli.main(["--db", str(db), "list", "--folder", folder]) == 0
    assert capsys.readouterr().out.count("https://") == 3


@pytest.mark.parametrize("tag", ["Café", "café", "CAFÉ"])
def test_cli_search_tag(db, tag, capsys):
    assert cli.main(["--db", str(db), "search", "indexing", "--tag", tag]) == 0
    assert capsys.readouterr().out.count("https://") == 3


def test_cli_remove_by_folder_in_any_case(db, tmp_path, capsys):
    """The dangerous shape of the bug: a filter that quietly matched a subset."""
    copy = tmp_path / "copy.sqlite"
    copy.write_bytes(db.read_bytes())
    assert cli.main(["--db", str(copy), "remove", "--folder", "reading", "--yes"]) == 0
    assert "removed 3 bookmarks" in capsys.readouterr().out
    with Store(copy, readonly=True) as store:
        assert store.records(folder="Reading") == []
