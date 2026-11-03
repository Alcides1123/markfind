"""Build a markfind index without touching the network, then search it.

This is the quickest way to see what markfind does. It imports
``sample_bookmarks.html``, stores some made-up page text for each bookmark
(standing in for ``markfind fetch``), and runs a few searches.

    python examples/offline_demo.py

The database goes in a temporary directory and is deleted afterwards.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from markfind.importers import load_file
from markfind.store import Store, to_fts_query

HERE = Path(__file__).parent

# Short stand-ins for the text markfind would download for each page.
PAGE_TEXT = {
    "https://sqlite.org/fts5.html": (
        "FTS5 is an SQLite virtual table module that provides full-text search. "
        "The bm25 function ranks matches; snippet and highlight show context."
    ),
    "https://docs.python.org/3/library/sqlite3.html": (
        "The sqlite3 module provides an SQL interface. Connections, cursors, "
        "transactions and placeholders for query parameters."
    ),
    "https://docs.python.org/3/library/html.parser.html": (
        "HTMLParser is the basis for parsing text files formatted in HTML. "
        "Override handle_starttag, handle_endtag and handle_data."
    ),
    "https://packaging.python.org/en/latest/tutorials/packaging-projects/": (
        "How to package a simple Python project: pyproject.toml, build a wheel "
        "and source distribution, and upload to the Package Index."
    ),
    "https://en.wikipedia.org/wiki/Okapi_BM25": (
        "BM25 is a ranking function used by search engines to estimate the "
        "relevance of documents to a given search query."
    ),
    "https://en.wikipedia.org/wiki/Link_rot": (
        "Link rot is the phenomenon of hyperlinks tending to cease to point to "
        "their originally targeted file, web page, or server."
    ),
    # www.example.com is bookmarked as "Untitled"; only its text can find it.
    "https://www.example.com/": "This domain is for use in illustrative examples in documents.",
}

QUERIES = [
    "ranking",  # matches page text on two pages
    "sqlite",  # titles, URLs and text
    "illustrative examples",  # finds the "Untitled" bookmark by its text
    "packaging",  # also matches the folder name
]


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp, Store(Path(tmp) / "demo.sqlite") as store:
        added, _ = store.add_bookmarks(load_file(HERE / "sample_bookmarks.html"))
        print(f"imported {added} bookmarks")

        for id_, url in store.pending():
            store.set_page(id_, PAGE_TEXT[url])

        for query in QUERIES:
            print(f"\n$ markfind search {query}")
            for n, r in enumerate(store.search(to_fts_query(query), limit=3), 1):
                print(f"{n}. {r.title}")
                print(f"   {r.url}")
                if r.folder:
                    print(f"   folder: {r.folder}")
                if r.snippet:
                    print(f"   {r.snippet}")


if __name__ == "__main__":
    main()
