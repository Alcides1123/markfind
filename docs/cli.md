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
