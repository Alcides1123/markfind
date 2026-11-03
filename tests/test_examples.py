"""Run the scripts in examples/ so they keep working as the code changes."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from markfind.importers import load_file
from markfind.store import Store

EXAMPLES = Path(__file__).parent.parent / "examples"
SRC = Path(__file__).parent.parent / "src"


def run(script, *args, **env):
    environ = {**os.environ, "PYTHONPATH": str(SRC), "PYTHONIOENCODING": "utf-8", **env}
    return subprocess.run(
        [sys.executable, str(EXAMPLES / script), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environ,
        check=False,
    )


def test_sample_bookmarks_parse():
    bookmarks = load_file(EXAMPLES / "sample_bookmarks.html")
    assert len(bookmarks) == 7
    assert {b.folder for b in bookmarks} == {
        "Bookmarks bar/Dev",
        "Bookmarks bar/Dev/Packaging",
        "Bookmarks bar/Reading",
        "",
    }


def test_offline_demo():
    result = run("offline_demo.py")
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "imported 7 bookmarks" in out
    assert "[illustrative] [examples]" in out
    assert "folder: Bookmarks bar/Dev/Packaging" in out


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "db.sqlite"
    with Store(path) as store:
        store.add_bookmarks(load_file(EXAMPLES / "sample_bookmarks.html"))
        pending = dict((url, id_) for id_, url in store.pending())
        store.set_page(pending["https://sqlite.org/fts5.html"], "bm25 ranks full-text matches")
        store.set_error(
            pending["https://en.wikipedia.org/wiki/Link_rot"], "HTTP 404 Not Found", "gone"
        )
        store.set_error(pending["https://www.example.com/"], "timed out", "timeout")
    return path


def test_export_markdown_search(db):
    result = run("export_markdown.py", "bm25", "--db", str(db))
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == "# Bookmarks matching “bm25”"
    # Title match (weighted higher) and page-text match.
    assert "- [Okapi BM25 - Wikipedia](https://en.wikipedia.org/wiki/Okapi_BM25)" in lines[2]
    assert "- [SQLite FTS5 Extension](https://sqlite.org/fts5.html) — _Bookmarks bar/Dev_" in lines
    assert "  > **bm25** ranks full-text matches" in lines


def test_export_markdown_broken(db):
    result = run("export_markdown.py", "--broken", "--db", str(db))
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    # Only dead links, not the timeout.
    assert len([line for line in lines if line.startswith("- [ ]")]) == 1
    assert "[Link rot - Wikipedia](https://en.wikipedia.org/wiki/Link_rot) — HTTP 404" in lines[2]


def test_export_markdown_needs_query(db):
    result = run("export_markdown.py", "--db", str(db))
    assert result.returncode == 2
    assert "give some search words" in result.stderr
