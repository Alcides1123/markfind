# Examples

Install markfind first (`python -m pip install -e .` from the repository root).

| File | What it shows |
|---|---|
| [`offline_demo.py`](offline_demo.py) | Import the sample bookmarks, index made-up page text, and search — no network needed. The fastest way to see markfind work. |
| [`sample_bookmarks.html`](sample_bookmarks.html) | A small browser export (Netscape format) with nested folders, to try the CLI on. |
| [`export_markdown.py`](export_markdown.py) | Use markfind from Python: export search results, or a dead-link checklist, as Markdown. |
| [`refresh.sh`](refresh.sh) / [`refresh.ps1`](refresh.ps1) | Scheduled refresh: re-import, fetch new and stale pages, report dead links. With cron and Task Scheduler setup notes. |

## Try it

```sh
python examples/offline_demo.py
```

```text
imported 7 bookmarks

$ markfind search ranking
1. SQLite FTS5 Extension
   https://sqlite.org/fts5.html
   folder: Bookmarks bar/Dev
   ...The bm25 function [ranks] matches; snippet and highlight show context.
2. Okapi BM25 - Wikipedia
   https://en.wikipedia.org/wiki/Okapi_BM25
   folder: Bookmarks bar/Reading
   BM25 is a [ranking] function used by search engines to estimate the relevance of documents to...

$ markfind search illustrative examples
1. Untitled
   https://www.example.com/
   This domain is for use in [illustrative] [examples] in documents.
...
```

Then the real thing, with the sample file and your own database location:

```sh
export MARKFIND_DB=/tmp/markfind-sample.sqlite      # PowerShell: $env:MARKFIND_DB = "$env:TEMP\markfind-sample.sqlite"
markfind import examples/sample_bookmarks.html
markfind fetch
markfind search full text search
markfind broken --all
python examples/export_markdown.py bm25 > bm25.md
```

These scripts are run by the test suite (`tests/test_examples.py`), so they
stay in sync with the code.
