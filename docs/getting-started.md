# Getting started

## 1. Install

markfind needs Python 3.10 or newer and has no other dependencies.

```sh
git clone https://github.com/Alcides1123/markfind.git
cd markfind
python -m pip install -e .
markfind --version
```

To try it without your own bookmarks first, run the offline demo:

```sh
python examples/offline_demo.py
```

## 2. Get your bookmarks

**The quick way:** let markfind find them.

```sh
markfind sync --list    # shows the browser profiles it found
markfind sync           # imports them all and fetches the pages
```

This works for Chrome, Edge, Brave, Vivaldi, Chromium and Firefox, with every
profile. If `sync` finds what you want, skip to [step 3](#3-import-fetch-search)
and use `markfind sync` in place of `import` and `fetch`. See
[`sync`](cli.md#sync) for details.

Otherwise, give markfind a file. It reads three formats.

**An HTML export (any browser).** This is the standard "Netscape bookmark
file" that all major browsers produce:

| Browser | How to export |
|---|---|
| Chrome | Bookmarks manager (`Ctrl+Shift+O`) → ⋮ → *Export bookmarks* |
| Edge | Favorites (`Ctrl+Shift+O`) → ⋯ → *Export favorites* |
| Firefox | Bookmarks → *Manage bookmarks* → *Import and Backup* → *Export Bookmarks to HTML* |
| Safari | File → Export → *Bookmarks* |

**Chrome or Edge's live `Bookmarks` file.** There's no export step, and it's
always up to date, which suits a scheduled refresh:

| Browser | Location |
|---|---|
| Chrome, Windows | `%LOCALAPPDATA%\Google\Chrome\User Data\Default\Bookmarks` |
| Chrome, macOS | `~/Library/Application Support/Google/Chrome/Default/Bookmarks` |
| Chrome, Linux | `~/.config/google-chrome/Default/Bookmarks` |
| Edge, Windows | `%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\Bookmarks` |

If you use several browser profiles, replace `Default` with `Profile 1`,
`Profile 2`, and so on.

**Firefox's `places.sqlite` database.** Like Chrome's `Bookmarks` file, this
needs no export step and is always up to date:

| System | Location |
|---|---|
| Windows | `%APPDATA%\Mozilla\Firefox\Profiles\<profile>\places.sqlite` |
| macOS | `~/Library/Application Support/Firefox/Profiles/<profile>/places.sqlite` |
| Linux | `~/.mozilla/firefox/<profile>/places.sqlite` (Snap: `~/snap/firefox/common/.mozilla/firefox/<profile>/`) |

`<profile>` is a folder named something like `abcd1234.default-release`. To
find yours, open `about:profiles` in Firefox and look at *Root Directory*.

markfind reads a temporary copy of the database, so it's safe to import while
Firefox is open, and recent changes are included. On Windows, if the import
fails with a permission error, close Firefox and try again.

## 3. Import, fetch, search

```sh
markfind import ~/Downloads/bookmarks.html
```
```text
/home/you/Downloads/bookmarks.html: 812 added, 0 updated
```

Only `http` and `https` links are imported. Bookmarklets, `file://` and
`chrome://` links are skipped. Each bookmark keeps its folder path, such as
`Bookmarks bar/Dev/Python`.

```sh
markfind fetch
```
```text
[1/812] ok: https://sqlite.org/fts5.html
[2/812] failed, gone: HTTP 404 Not Found: https://blog.example/old-post
...
fetched 774, failed 38
run 'markfind broken --all' to see what failed
```

Fetching downloads each page once and stores its text. It sends at most one
request at a time to each website and waits a second between them, so a large
first fetch takes a while. Later runs only fetch new bookmarks.

```sh
markfind search sqlite full text
```
```text
1. SQLite FTS5 Extension
   https://sqlite.org/fts5.html
   folder: Bookmarks bar/Dev
   ...FTS5 is an [SQLite] virtual table module that provides [full]-[text] search...
```

All the words must appear somewhere in the bookmark's title, URL, folder or
page text. Words are matched by their stem, so `index` also finds `indexes` and
`indexing`. Results are ranked best first, and title matches count most.

## 4. Search in your browser

```sh
markfind serve --open
```

This opens a page at `http://127.0.0.1:8737/` where results appear as you
type. Click one to open it in a new tab. A *Dead links* tab lists bookmarks
whose pages are gone. The page runs only on your computer and never changes
your bookmarks. Press `Ctrl+C` in the terminal to stop it. See
[`serve`](cli.md#serve) for options.

## 5. Keep it fresh

```sh
markfind import ~/Downloads/bookmarks.html   # picks up new bookmarks, renames, moves
markfind fetch --older-than 30d --failed     # new pages, stale pages, earlier failures
markfind broken                              # bookmarks that are gone for good
```

[`examples/refresh.sh`](../examples/refresh.sh) and
[`examples/refresh.ps1`](../examples/refresh.ps1) wrap this up for cron or
Windows Task Scheduler.

## Where's my data?

Everything is in a single SQLite file, `~/.markfind.sqlite` by default. Use
`--db PATH` or the `MARKFIND_DB` environment variable to keep it elsewhere,
for example in a synced folder. Delete the file to start over. markfind never
changes your browser's bookmarks.

## Next

- [Command reference](cli.md) covers every option and advanced search syntax.
- If many pages fail with SSL errors, see
  [SSL certificate errors](../README.md#ssl-certificate-errors).
