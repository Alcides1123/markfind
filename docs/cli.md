# Command reference

```text
markfind [--db PATH] COMMAND [options]
```

| Global option | Meaning |
|---|---|
| `--db PATH` | Database file. Default: `$MARKFIND_DB`, or `~/.markfind.sqlite`. Created if missing. |
| `--version` | Print the version and exit. |

Commands: [`import`](#import) · [`fetch`](#fetch) · [`search`](#search) ·
[`list`](#list) · [`broken`](#broken) · [`stats`](#stats)

---

## import

```text
markfind import FILE [FILE ...]
```

Adds bookmarks from one or more files. The format is detected from the file
contents:

- An SQLite database is read as Firefox's `places.sqlite`. markfind reads a
  temporary copy, including changes Firefox hasn't saved to the main file yet,
  and never modifies the original.
- A file starting with `{` is read as Chrome / Edge `Bookmarks` JSON.
- Anything else is read as a Netscape bookmark HTML export.

Rules:

- Only `http://` and `https://` URLs are kept.
- From Firefox, tags are ignored (they're stored as extra copies of tagged
  bookmarks), and a bookmark with no title uses the page title from history.
- A bookmark is identified by its exact URL. Importing a URL that's already in
  the database **updates** its title and folder and keeps its fetched page
  text, so re-importing a fresh export is always safe.
- If the same URL appears twice in one file, the last occurrence wins.
- Bookmarks deleted from the browser are not removed from markfind (yet; see
  the [roadmap](../ROADMAP.md)).

Exit code 1 if a file can't be read.

```sh
markfind import bookmarks.html
markfind import "$LOCALAPPDATA/Google/Chrome/User Data/Default/Bookmarks"
markfind import ~/.mozilla/firefox/abcd1234.default-release/places.sqlite
```

---

## fetch

```text
markfind fetch [--older-than AGE] [--failed] [--all]
               [--workers N] [--timeout S] [--delay S] [-q]
```

Downloads bookmarked pages and indexes their text. By default it fetches only
bookmarks that have **never** been fetched.

| Option | Meaning |
|---|---|
| `--older-than AGE` | Also re-fetch pages last fetched more than `AGE` ago. `AGE` is a number plus `h`, `d` or `w`: `12h`, `90d`, `2w`. |
| `--failed` | Also retry bookmarks whose last fetch failed. |
| `--all` | Re-fetch everything. |
| `--workers N` | Parallel downloads (default 8). |
| `--timeout S` | Seconds to wait for each page (default 10). |
| `--delay S` | Minimum seconds between two requests to the same host (default 1; `0` disables). |
| `-q`, `--quiet` | Print only the summary, not one line per page. |

The options combine. For example, `--older-than 30d --failed` fetches new,
stale and failed bookmarks.

What gets indexed:

- HTML pages (`text/html`, `application/xhtml+xml`) are reduced to their main
  text. See [text extraction](how-it-works.md#text-extraction).
- `text/plain` pages are indexed as they are.
- Anything else, such as PDFs and images, is recorded as an `unsupported`
  failure.
- Pages over 2 MB are truncated.

If a bookmark has no title, the page's `<title>` is used.

Every failure is recorded with a **kind**. [`broken`](#broken) and
[`stats`](#stats) use these kinds.

| Kind | Cause | Likely dead? |
|---|---|---|
| `gone` | HTTP 404 or 410 | yes |
| `dns` | The host name doesn't exist | yes |
| `blocked` | HTTP 401 or 403 (login required, bot protection) | no |
| `server` | HTTP 429 or 5xx | no, try later |
| `http` | Any other HTTP error status | maybe |
| `timeout` | No response within `--timeout` | no, try later |
| `connection` | Connection refused or reset | maybe |
| `ssl` | Certificate or TLS failure | see [SSL certificate errors](../README.md#ssl-certificate-errors) |
| `unsupported` | Not HTML or plain text | no |
| `other` | Anything else | — |

---

## search

```text
markfind search [-n N] [--raw] WORD [WORD ...]
```

Searches bookmark titles, URLs, folder paths and page text, and prints the
best matches first.

| Option | Meaning |
|---|---|
| `-n N`, `--limit N` | Show at most `N` results (default 20). |
| `--raw` | Pass the query to SQLite FTS5 unchanged (see below). |

### Plain queries (default)

- Every word must match, somewhere in the bookmark.
- Matching ignores case and accents (`cafe` finds `café`) and uses stems
  (`index` finds `indexes`, `indexing`).
- Punctuation is safe: `full-text`, `c++` and `node.js` are all searched as
  plain words.

### Raw queries (`--raw`)

These give you the full [FTS5 query
syntax](https://sqlite.org/fts5.html#full_text_query_syntax). Quote the whole
query for your shell.

| Query | Finds |
|---|---|
| `'sqlite OR postgres'` | either word |
| `'python NOT django'` | `python` without `django` |
| `'"full text search"'` | the exact phrase |
| `'NEAR(cache invalidation, 5)'` | both words within 5 words of each other |
| `'pars*'` | words starting with `pars` |
| `'title:rust'` | `rust` in the title only (columns: `title`, `url`, `body`, `folder`) |
| `'folder:recipes AND chicken'` | `chicken` in bookmarks under a "recipes" folder |

### Output and ranking

Each result shows the title (or the URL if there's no title), the URL, the
folder, and a snippet of page text with the matched words highlighted. They're
bold in a terminal and `[bracketed]` when output is piped.

Results are ranked with BM25, with matches weighted by where they occur:
title ×5, page text ×2, URL ×1, folder ×1.

Exit codes:

- 0: results found
- 1: no matches
- 2: invalid `--raw` query

---

## list

```text
markfind list [--folder PATH]
```

Prints every bookmark as tab-separated `folder`, `title` and `url`, sorted by
folder and then title. Empty fields are shown as `-`.

`--folder PATH` limits the list to that folder and its subfolders. For
example, `--folder "Bookmarks bar/Dev"` includes `Bookmarks bar/Dev/Python`
but not `Bookmarks bar/Devices`.

---

## duplicates

```text
markfind duplicates [--by url|text] [--urls]
```

Finds bookmarks you saved more than once, so you can tidy them up in your
browser. markfind never changes your browser's bookmarks.

```text
$ markfind duplicates
Same page, different URLs (1):
  SQLite FTS5
  * https://sqlite.org/fts5.html
    https://sqlite.org/fts5.html#overview_of_fts5                    folder: Old
    http://www.sqlite.org/fts5.html?utm_source=hn&utm_medium=social  folder: Old

Same page text (2):
  asyncio
  * https://docs.python.org/3/library/asyncio-task.html
    https://docs.python.org/3.15/library/asyncio-task.html  folder: Old
  Git rebasing
  * https://git-scm.com/book/en/v2/Git-Branching-Rebasing
    https://git-scm.com/book/en/v2/Git-Branching-Rebasing?ref=blog  folder: Old

* = cleanest URL. 4 extra copies; 'markfind duplicates --urls' lists them.
```

| Option | Meaning |
|---|---|
| `--by url` | Only bookmarks whose URLs match. |
| `--by text` | Only bookmarks whose page text matches. |
| `--urls` | Print only the extra copies' URLs (everything but the starred one), one per line. |

**Same page, different URLs.** URLs are compared after removing differences
that don't change the page:

- `http` and `https`, a leading `www.`, upper-case letters in the host name,
  and default ports (`:80`, `:443`);
- a trailing `/`, and `index.html`, `index.htm`, `index.php` or
  `default.aspx` at the end of the path;
- `#section` links. App routes like `#/inbox` or `#!/inbox` are kept, since
  they show different pages;
- tracking parameters: `utm_*`, `fbclid`, `gclid`, `msclkid`, `mc_cid`,
  `mc_eid`, `igshid` and similar. Other parameters are kept, in any order.

Paths stay case-sensitive, and parameters such as `?ref=` or `?id=` are kept
because some sites use them for real content.

**Same page text.** Fetched pages whose text is identical, ignoring case and
spacing. This catches pages that moved, redirects, version-pinned docs and
other URLs that serve the same content. Pages under 50 words are skipped, so
short error and login pages don't all match each other. Pages behind a login
can still look identical, so check the group before deleting anything. A group
already shown under *Same page, different URLs* isn't repeated.

**Which one is starred.** In each group the cleanest URL comes first: `https`
before `http`, then the shortest.

---

## broken

```text
markfind broken [--all | --kind KIND ...] [--urls]
```

Lists bookmarks whose last fetch failed, grouped by
[failure kind](#fetch), with the error message, the date it was checked and
the folder.

| Option | Meaning |
|---|---|
| *(none)* | Only likely-dead bookmarks: kinds `gone` and `dns`. |
| `--all` | Every failed bookmark. |
| `--kind KIND` | Only this kind. Repeat for several: `--kind ssl --kind timeout`. |
| `--urls` | Print bare URLs, one per line, for use in scripts. |

A later successful fetch removes a bookmark from this list, so temporary
problems clear themselves with `markfind fetch --failed`.

---

## stats

```text
markfind stats
```

```text
bookmarks: 812
indexed:   774
failed:    38 (gone 21, timeout 9, dns 5, blocked 3)
pending:   0
```

`pending` counts bookmarks that have never been fetched.
