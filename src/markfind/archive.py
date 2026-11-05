"""Show a bookmark's saved copy as a standalone page.

The copy itself is made while fetching (``extract.Page.html``) from an
allowlist of tags, so it holds no scripts, styles or attributes other than
link targets. This module wraps it in a page with a banner saying what it is.
The only style is ``STYLE`` below; ``STYLE_HASH`` lets a Content-Security-Policy
allow exactly that and nothing else.
"""

from __future__ import annotations

import base64
import hashlib
from html import escape

STYLE = """
:root { color-scheme: light dark; --muted: #646a73; --line: #e3e4e6; --accent: #2459d3; }
@media (prefers-color-scheme: dark) {
  :root { --muted: #9aa0a8; --line: #2d3036; --accent: #7aa2ff; }
}
body { max-width: 46rem; margin: 0 auto; padding: 0 1rem 4rem;
  font: 17px/1.6 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
.banner { margin: 0 -1rem 2rem; padding: .75rem 1rem; border-bottom: 1px solid var(--line);
  color: var(--muted); font-size: 14px; line-height: 1.5; }
.banner a, a { color: var(--accent); }
h1.title { line-height: 1.25; }
pre { overflow-x: auto; padding: .75rem; border: 1px solid var(--line); border-radius: 6px; }
code, pre, kbd, samp { font-family: ui-monospace, Consolas, monospace; font-size: .9em; }
blockquote { margin-left: 0; padding-left: 1rem; border-left: 3px solid var(--line); }
table { border-collapse: collapse; display: block; overflow-x: auto; }
th, td { border: 1px solid var(--line); padding: .25rem .5rem; vertical-align: top; }
img { display: none; }
""".strip()

STYLE_HASH = "sha256-" + base64.b64encode(hashlib.sha256(STYLE.encode()).digest()).decode()


def render(url: str, title: str, saved_at: str | None, body_html: str, text_only: bool) -> str:
    """A complete HTML page for a saved copy.

    ``body_html`` must already be safe: the readable copy made by
    ``extract_text``, or escaped text. ``text_only`` says it's just the
    indexed text, without headings or links.
    """
    when = f"on {saved_at[:10]}" if saved_at else "at its last successful fetch"
    what = (
        "only its text was kept, from before markfind saved readable copies"
        if text_only
        else "text and links only; images, scripts and styles aren't kept"
    )
    link = f'<a href="{escape(url)}">{escape(url, quote=False)}</a>'
    shown_title = escape(title or url, quote=False)
    # Most pages have their own heading; add one only if this copy doesn't.
    heading = "" if "<h1>" in body_html else f'<h1 class="title">{shown_title}</h1>\n'
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>{shown_title} (saved copy)</title>
<style>{STYLE}</style>
</head>
<body>
<p class="banner">Saved copy of {link}, made by markfind {when}: {what}.</p>
{heading}{body_html}
</body>
</html>
"""


def text_as_html(text: str) -> str:
    """Indexed page text (one line, no structure) as a safe HTML paragraph."""
    return f"<p>{escape(text, quote=False)}</p>" if text else ""
