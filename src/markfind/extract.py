"""Turn an HTML page into a title and plain searchable text.

Besides stripping tags, this drops page "chrome" that would otherwise pollute
search results: navigation, footers, sidebars, cookie banners and the like.
When the page marks up its main content (``<main>`` or ``<article>``) and that
content is substantial, only it is kept.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser

# Elements whose contents are never visible prose.
_NOT_TEXT = frozenset({"script", "style", "noscript", "template", "svg", "iframe", "canvas"})

# Elements that hold site-wide boilerplate rather than page content.
_BOILERPLATE_TAGS = frozenset({"nav", "footer", "aside", "form", "button", "dialog"})

# <header> is a page banner at the top level, but inside <article> it usually
# holds the article's own title, so it is only dropped outside main content.
_TOP_LEVEL_BOILERPLATE_TAGS = frozenset({"header"})

_BOILERPLATE_ROLES = frozenset(
    {"navigation", "banner", "contentinfo", "complementary", "search", "menu", "menubar", "dialog"}
)

# Matched as whole words against class and id, e.g. "site-footer", "cookie_bar".
_BOILERPLATE_NAMES = re.compile(
    r"(?<![a-z0-9])(?:nav|navbar|navigation|menu|sidebar|footer|breadcrumbs?|cookies?"
    r"|consent|banner|ads?|advert|advertisement|promo|share|sharing|social|related"
    r"|newsletter|subscribe|popup|modal|skip-link|comments?)(?![a-z0-9])"
)

# Class names that describe page state rather than what an element is, e.g.
# <body class="has-sidebar"> or <div class="is-menu-open">.
_STATE_FLAG_PREFIXES = ("has-", "is-", "no-", "with-")

_MAIN_TAGS = frozenset({"main", "article"})

# Elements that wrap the whole page or its content. Class, id and role
# heuristics never apply to them: sites put page-wide flags on <html> and
# <body> (Wikipedia's <html> class includes "vector-feature-main-menu-..."),
# and dropping one of these would drop the entire page.
_STRUCTURAL = frozenset({"html", "body"}) | _MAIN_TAGS

# Elements without an end tag; they can't contain text, so never start a skip.
_VOID = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "source",
        "track",
        "wbr",
    }
)

# Main content shorter than this is likely a teaser; use the whole page instead.
MIN_MAIN_WORDS = 30


@dataclass(frozen=True)
class Page:
    title: str
    text: str


def normalize(text: str) -> str:
    return " ".join(text.split())


def _is_boilerplate(tag: str, attrs: dict[str, str | None], in_main: bool) -> bool:
    if tag in _NOT_TEXT or tag in _BOILERPLATE_TAGS:
        return True
    if tag in _TOP_LEVEL_BOILERPLATE_TAGS and not in_main:
        return True
    if tag in _STRUCTURAL:
        return False
    if "hidden" in attrs or attrs.get("aria-hidden") == "true":
        return True
    if (attrs.get("role") or "").lower() in _BOILERPLATE_ROLES:
        return True
    names = f"{attrs.get('class') or ''} {attrs.get('id') or ''}".lower().split()
    return any(
        _BOILERPLATE_NAMES.search(name)
        for name in names
        if not name.startswith(_STATE_FLAG_PREFIXES)
    )


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: list[str] = []
        self.parts: list[str] = []
        self.main_parts: list[str] = []
        # Text with only non-prose elements (_NOT_TEXT) removed, as a fallback
        # if the boilerplate rules remove nearly everything.
        self.raw_parts: list[str] = []
        self._in_title = False
        self._main_depth = 0
        # While skipping, the tag that started the skip and how deeply it is nested.
        self._skip_tag: str | None = None
        self._skip_depth = 0
        self._raw_skip_tag: str | None = None
        self._raw_skip_depth = 0

    def _space(self) -> None:
        # Keep words from adjacent elements apart, e.g. <td>a</td><td>b</td>.
        self.parts.append(" ")
        if self._main_depth:
            self.main_parts.append(" ")

    def _raw_start(self, tag: str) -> None:
        if self._raw_skip_tag is not None:
            if tag == self._raw_skip_tag:
                self._raw_skip_depth += 1
        elif tag in _NOT_TEXT:
            self._raw_skip_tag = tag
            self._raw_skip_depth = 1
        self.raw_parts.append(" ")

    def _raw_end(self, tag: str) -> None:
        if tag == self._raw_skip_tag:
            self._raw_skip_depth -= 1
            if not self._raw_skip_depth:
                self._raw_skip_tag = None
        self.raw_parts.append(" ")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._raw_start(tag)
        if self._skip_tag is not None:
            if tag == self._skip_tag:
                self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
        elif tag not in _VOID and _is_boilerplate(tag, dict(attrs), self._main_depth > 0):
            self._skip_tag = tag
            self._skip_depth = 1
        elif tag in _MAIN_TAGS:
            self._main_depth += 1
        self._space()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # Self-closing tags like <div class="ad"/> have no content to skip.
        if self._skip_tag is None:
            self._space()

    def handle_endtag(self, tag: str) -> None:
        self._raw_end(tag)
        if self._skip_tag is not None:
            if tag == self._skip_tag:
                self._skip_depth -= 1
                if not self._skip_depth:
                    self._skip_tag = None
            return
        if tag == "title":
            self._in_title = False
        elif tag in _MAIN_TAGS and self._main_depth:
            self._main_depth -= 1
        self._space()

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title.append(data)
            return
        if self._raw_skip_tag is None:
            self.raw_parts.append(data)
        if self._skip_tag is not None:
            return
        self.parts.append(data)
        if self._main_depth:
            self.main_parts.append(data)


def extract_text(html: str) -> Page:
    parser = _TextParser()
    parser.feed(html)
    parser.close()

    text = normalize("".join(parser.parts))
    main = normalize("".join(parser.main_parts))
    if len(main.split()) >= MIN_MAIN_WORDS:
        text = main
    else:
        # If the boilerplate rules removed nearly everything (under 10% of
        # the visible text), a wrapper element was probably misjudged; index
        # all the visible text rather than an empty page. Small pages that
        # are mostly navigation keep their filtered text.
        words = len(text.split())
        raw = normalize("".join(parser.raw_parts))
        if words < MIN_MAIN_WORDS and words * 10 < len(raw.split()):
            text = raw
    return Page(title=normalize("".join(parser.title)), text=text)
