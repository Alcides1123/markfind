# Command reference

```text
markfind [--db PATH] COMMAND [options]
```

| Global option | Meaning |
|---|---|
| `--db PATH` | Database file. Default: `$MARKFIND_DB`, or `~/.markfind.sqlite`. Created if missing. |
| `--version` | Print the version and exit. |

Commands: [`import`](#import) · [`fetch`](#fetch) · [`search`](#search) ·
[`list`](#list) · [`broken`](#broken) · [`stats`](#stats) · [`serve`](#serve)

---

## import

```text
markfind import [--prune] FILE [FILE ...]
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
- Each bookmark remembers the file it was last imported from.
- `--prune` removes bookmarks that were imported from these files before but
  aren't in them any more, and lists them. Bookmarks imported from other files
  are never touched. A file with no bookmarks at all is not pruned, since
  that's more likely a broken file than a deliberate clear-out. Without
  `--prune`, nothing is ever removed.

Exit code 1 if a file can't be read.

```sh
markfind import bookmarks.html
markfind import "$LOCALAPPDATA/Google/Chrome/User Data/Default/Bookmarks"
markfind import ~/.mozilla/firefox/abcd1234.default-release/places.sqlite
```

---

## sync

```text
markfind sync [--list] [--browser NAME] [--prune] [--no-fetch]
              [--workers N] [--timeout S] [--delay S] [-q]
```

Finds the bookmarks of every browser profile on this computer, imports them,
and fetches pages that haven't been fetched yet. It's `import` plus `fetch`
without having to know where your browsers keep their files, so it's the
easiest way to keep markfind up to date.

```text
$ markfind sync --prune
Chrome (Personal): 3 added, 409 updated
Chrome (Work): 0 added, 87 updated
Firefox (default-release): 1 added, 155 updated
Chrome (Personal): 2 removed
  - Old tutorial <https://old.example/tutorial>
  - Conference 2024 <https://conf.example/2024>
Chrome (Work): 0 removed
Firefox (default-release): 0 removed
[1/4] ok: https://new.example/
...
fetched 4, failed 0
```

| Option | Meaning |
|---|---|
| `--list` | Only show the bookmark files found, one per line: name, then path. |
| `--browser NAME` | Only this browser: `chrome`, `edge`, `brave`, `vivaldi`, `chromium` or `firefox`. Repeat for several. |
| `--prune` | Remove bookmarks that were deleted in the browser (see below). |
| `--no-fetch` | Import only. |
| `--workers`, `--timeout`, `--delay`, `-q` | As for [`fetch`](#fetch). With `-q`, pruned bookmarks are counted but not listed. |

Where it looks:

| Browser | Windows | macOS | Linux |
|---|---|---|---|
| Chrome | `%LOCALAPPDATA%\Google\Chrome\User Data` | `~/Library/Application Support/Google/Chrome` | `~/.config/google-chrome` |
| Edge | `%LOCALAPPDATA%\Microsoft\Edge\User Data` | `~/Library/Application Support/Microsoft Edge` | `~/.config/microsoft-edge` |
| Brave | `%LOCALAPPDATA%\BraveSoftware\Brave-Browser\User Data` | `~/Library/Application Support/BraveSoftware/Brave-Browser` | `~/.config/BraveSoftware/Brave-Browser` |
| Vivaldi | `%LOCALAPPDATA%\Vivaldi\User Data` | `~/Library/Application Support/Vivaldi` | `~/.config/vivaldi` |
| Chromium | `%LOCALAPPDATA%\Chromium\User Data` | `~/Library/Application Support/Chromium` | `~/.config/chromium` |
| Firefox | `%APPDATA%\Mozilla\Firefox\Profiles` | `~/Library/Application Support/Firefox/Profiles` | `~/.mozilla/firefox`, `~/.config/mozilla/firefox`, Snap and Flatpak |

Every profile is included, and shown by the name the browser gives it (from
Chromium's `Local State` file and Firefox's `profiles.ini`). On Linux,
`$XDG_CONFIG_HOME` replaces `~/.config`. Safari isn't supported yet; export
its bookmarks to HTML and use [`import`](#import).

**Pruning.** With `--prune`, a bookmark is removed only if the file it was
last imported from was read in this run and no longer contains it. So:

- a bookmark you moved from one browser to another stays;
- bookmarks you imported from an HTML export, or from a browser you left out
  with `--browser`, are never removed;
- a browser file that can't be read, or that has no bookmarks at all, is not
  pruned.

Removing a bookmark deletes its indexed page text. If you add it back, it's
fetched again on the next sync.

If one browser's file can't be read, sync reports it, carries on with the
others, and exits with code 1. It also exits with code 1 if no browser
bookmarks are found at all.

To run it on a schedule, see [`examples/refresh.sh`](../examples/refresh.sh)
and [`examples/refresh.ps1`](../examples/refresh.ps1); `markfind sync --prune
-q` can replace their import and fetch lines.

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

## similar

```text
markfind similar [-n N] URL
```

"More like this": lists the bookmarks most like the one with this URL, and
the words the match was based on. Use it to rediscover related pages you saved
long ago, in other folders or under unhelpful titles.

```text
$ markfind similar https://git-scm.com/book/en/v2/Git-Branching-Rebasing -n 2
Similar to: Git rebasing
based on: git, rebase, branch, merge, commits, history, figure, master, server, project, replay, topic, patch, pushed, pull

1. Git branching
   https://git-scm.com/book/en/v2/Git-Branching-Basic-Branching-and-Merging
   ...couple of [commits] already on the [master] [branch]. [Figure] 18. A simple [commit] [history] You’ve...
2. Git remotes
   https://git-scm.com/book/en/v2/Git-Basics-Working-with-Remotes
   ...[branch] configured for '[git] [pull]': [master] [merges] with remote [master] Local ref configured for '[git] [push]...
```

| Option | Meaning |
|---|---|
| `-n N`, `--limit N` | Show at most `N` results (default 10). |

`URL` must match a bookmark exactly; copy it from `search` or `list` output.

How it chooses:

1. It takes the words of the bookmark's page text and title. Title words
   count three times, and common English words like "the" are dropped. A
   bookmark that hasn't been fetched yet uses its title alone.
2. It groups words by stem, as search does, so `index`, `indexes` and
   `indexing` count as one.
3. It scores each word by **TF-IDF**: how often the page uses it, times how
   rare it is across all your bookmarks. Words that no other bookmark uses
   can't match anything, and words every bookmark uses can't tell them apart,
   so both are skipped.
4. It searches for the 15 best words with `OR` and ranks results with BM25,
   like [`search`](#output-and-ranking). Bookmarks that share more of the
   distinctive words, and rarer ones, rank higher.

Results get better as your collection grows, because "rare" means more with
more bookmarks to compare.

Exit codes:

- 0: results found
- 1: no similar bookmarks
- 2: the URL isn't in markfind

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

## export

```text
markfind export [-f html|markdown|json|csv] [-o FILE]
                [--folder PATH] [--query WORDS [--raw]] [--skip-dead]
```

Writes bookmarks out, to standard output or to `FILE`.

| Format | What you get |
|---|---|
| `html` (default) | A standard bookmark file with your folders and dates. Every browser can import it. |
| `markdown` | A reading list: a heading per folder and `- [Title](url)` links. With `--query`, a numbered list in ranked order. |
| `json` | A list of objects with `url`, `title`, `folder`, `added_at`, `fetched_at` and `status`. |
| `csv` | The same fields as columns. Written with a byte-order mark when saved to a file, so Excel reads it as UTF-8. |

`status` is `indexed`, `pending` (not fetched yet), or the
[failure kind](#fetch) of the last fetch.

| Option | Meaning |
|---|---|
| `-o FILE`, `--output FILE` | Write to `FILE` instead of standard output. |
| `--folder PATH` | Only this folder and its subfolders. |
| `--query WORDS` | Only bookmarks matching this search, best match first. Quote it if it has several words. |
| `--raw` | `--query` uses [FTS5 syntax](#raw-queries---raw). |
| `--skip-dead` | Leave out bookmarks that are most likely dead for good: what [`broken`](#broken) lists by default (HTTP 404/410 or unknown host). Timeouts and other failures that may be temporary are kept. |

The options combine.

**Cleaning up your browser's bookmarks.** markfind never changes your
browser's bookmarks, but it can give you a cleaned-up file to import:

```sh
markfind broken                                    # check what will be left out
markfind export --skip-dead -o clean-bookmarks.html
```

Then import `clean-bookmarks.html` in the browser: in Chrome and Edge,
*Bookmarks manager → ⋮ → Import bookmarks*, and in Firefox, *Manage bookmarks →
Import and Backup → Import Bookmarks from HTML*. Browsers add imported
bookmarks alongside the existing ones, usually in an *Imported* folder, so to
replace your bookmarks, delete the old ones first.

**Sharing a reading list.**

```sh
markfind export --query "sqlite" -f markdown -o sqlite-reading.md
markfind export --folder "Bookmarks bar/Recipes" -f markdown
```

Exit codes: 1 if the file can't be written, 2 for an invalid `--raw` query.

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

---

## serve

```text
markfind serve [--host HOST] [--port PORT] [--open]
```

Starts a local web interface for your bookmarks, then keeps running until you
press `Ctrl+C`.

```text
$ markfind serve --open
markfind is serving /home/you/.markfind.sqlite at http://127.0.0.1:8737/ (Ctrl+C to stop)
```

| Option | Meaning |
|---|---|
| `--host HOST` | Address to listen on. Default `127.0.0.1`, which means only this computer. |
| `--port PORT` | Port. Default `8737`. |
| `--open` | Open the page in your default browser. |

The page has two views:

- **Search:** results update as you type. Matching words are highlighted, and
  each result shows its folder and URL and opens in a new tab. Tick *Advanced
  syntax* to use the [`--raw` query syntax](#raw-queries---raw). The query is
  kept in the page address, so you can bookmark or share a search.
- **Dead links:** the [`broken`](#broken) report grouped by kind, with a
  switch to show every failure.

Keyboard: `/` jumps to the search box and `Esc` clears it.

The interface is **read-only**. It never changes your bookmarks, so it's safe
to keep it open while `markfind import` or `markfind fetch` runs; new results
appear on the next search.

**Security.** The server only listens on this computer unless you pass
`--host`. Binding to another address, such as `--host 0.0.0.0`, prints a
warning, because anyone who can reach that address can search your bookmarks.
On a local address, requests that name any other host are refused (`403`),
which blocks a known trick ("DNS rebinding") that would let a website read
the page. Page text from bookmarked sites is always displayed as plain text,
and a strict Content-Security-Policy blocks any script but markfind's own.

Exit code 1 if the server can't start, for example because the port is in
use. Choose another with `--port`.
