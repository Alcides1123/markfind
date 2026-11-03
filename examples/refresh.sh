#!/bin/sh
# Keep a markfind index up to date. Meant to run on a schedule (cron, launchd).
#
#   examples/refresh.sh ~/Downloads/bookmarks.html
#
# Example crontab entry, every Sunday at 09:00:
#   0 9 * * 0  /path/to/markfind/examples/refresh.sh "$HOME/Downloads/bookmarks.html" >> "$HOME/.markfind-refresh.log" 2>&1
#
# Chrome / Edge users can pass the live Bookmarks file instead of an export, e.g.
#   ~/.config/google-chrome/Default/Bookmarks                         (Linux)
#   ~/Library/Application Support/Google/Chrome/Default/Bookmarks     (macOS)

set -eu

if [ $# -ne 1 ]; then
    echo "usage: $0 BOOKMARKS_FILE" >&2
    exit 2
fi

echo "== markfind refresh: $(date)"
markfind import "$1"
# New bookmarks, pages older than 30 days, and earlier failures.
markfind fetch --older-than 30d --failed --quiet
markfind stats
markfind broken
