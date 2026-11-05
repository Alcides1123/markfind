import json

from markfind import cli
from markfind.extract import Page
from markfind.fetch import EMPTY_MESSAGE, EmptyPageError
from markfind.importers import Bookmark
from markfind.store import MIGRATIONS, SCHEMA_VERSION, Store, _mark_empty_pages


def test_migration_marks_only_pages_fetched_without_text(tmp_path):
    path = tmp_path / "db.sqlite"
    with Store(path) as store:
        store.add_bookmarks(
            [
                Bookmark("https://text.example/", "Has text"),
                Bookmark("https://app.example/", "Chat app"),
                Bookmark("https://new.example/", "Not fetched yet"),
                Bookmark("https://down.example/", "Failed"),
            ]
        )
        store.set_page(1, "some real words")
        store.set_page(2, "")  # how markfind 0.1 and 0.2 stored a page with no text
        store.set_error(4, "timed out", "timeout")
        # Pretend this database was last opened by a version before "empty".
        version = MIGRATIONS.index(_mark_empty_pages)
        store.db.execute(f"PRAGMA user_version = {version}")
        store.db.commit()

    with Store(path) as store:
        assert store.db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        failures = {f.url: (f.kind, f.error) for f in store.failures()}
        assert failures == {
            "https://app.example/": ("empty", EMPTY_MESSAGE),
            "https://down.example/": ("timeout", "timed out"),
        }
        assert store.pending() == [(3, "https://new.example/")]
        stats = store.stats()
        assert (stats.fetched, stats.failed) == (1, 2)
        # Its title is still searchable.
        assert [r.url for r in store.search('"chat"')] == ["https://app.example/"]


def fake_fetch(url, timeout=10.0):
    if "app" in url:
        raise EmptyPageError(EMPTY_MESSAGE)
    return Page(title="", text="an essay about migrating birds")


def test_fetch_records_empty_pages(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_page", fake_fetch)
    db = str(tmp_path / "db.sqlite")
    with Store(db) as store:
        store.add_bookmarks(
            [Bookmark("https://app.example/", "Chat app"), Bookmark("https://blog.example/")]
        )

    assert cli.main(["--db", db, "fetch", "--delay", "0"]) == 0
    out = capsys.readouterr().out
    assert f"failed, empty: {EMPTY_MESSAGE}: https://app.example/" in out
    assert "fetched 1, failed 1" in out

    assert cli.main(["--db", db, "stats"]) == 0
    assert "failed:    1 (empty 1)" in capsys.readouterr().out

    # Not dead: hidden from the default report, shown with --all or --kind.
    assert cli.main(["--db", db, "broken"]) == 0
    assert "app.example" not in capsys.readouterr().out
    assert cli.main(["--db", db, "broken", "--kind", "empty", "--urls"]) == 0
    assert capsys.readouterr().out == "https://app.example/\n"

    assert cli.main(["--db", db, "export", "--skip-dead", "-f", "json"]) == 0
    statuses = {r["url"]: r["status"] for r in json.loads(capsys.readouterr().out)}
    assert statuses == {"https://app.example/": "empty", "https://blog.example/": "indexed"}

    # --failed retries it.
    assert cli.main(["--db", db, "fetch", "--failed", "-q"]) == 0
    assert "fetched 0, failed 1" in capsys.readouterr().out
