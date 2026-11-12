"""Turn an HTML page into a title and plain searchable text.

Besides stripping tags, this drops page "chrome" that would otherwise pollute
search results: navigation, footers, sidebars, cookie banners and the like.
When the page marks up its main content (``<main>`` or ``<article>``) and that
content is substantial, only it is kept. Otherwise the block holding most of the
page's prose is found by its text, so unlabelled menus and link lists are left out.

The same pass builds a readable copy of that content (``Page.html``): its
headings, paragraphs, lists, tables and links, rebuilt from an allowlist of tags
so that nothing from the page (scripts, styles, event handlers) gets through.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import escape
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

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

# Tags kept in the readable copy, and what each becomes. Everything else is
# dropped, though its text is kept.
_READER_TAGS = {
    **{
        tag: tag
        for tag in (
            "p h1 h2 h3 h4 h5 h6 ul ol li dl dt dd blockquote pre code em strong b i u s"
            " sub sup mark small kbd samp var del ins q cite abbr figure figcaption"
            " table caption thead tbody tfoot tr th td a br hr"
        ).split()
    },
    **dict.fromkeys(("div", "section", "article", "main", "header"), "div"),
}
_READER_VOID = frozenset({"br", "hr"})

# Main content shorter than this is likely a teaser; use the whole page instead.
MIN_MAIN_WORDS = 30

# Blocks scored by their text on pages without <main> or <article>.
_BLOCKS = frozenset({"div", "section", "td"})

# The block chosen by text density must hold at least this share of the page's
# prose (words outside links); otherwise its parent is tried. Dropping real text
# costs a search index more than keeping a menu, so this is set high.
MIN_PROSE_SHARE = 0.8


@dataclass(frozen=True)
class Page:
    title: str
    text: str
    # A readable copy of the same content as safe HTML (a fragment, no
    # <html> or <body>); empty if none was made.
    html: str = field(default="", compare=False, repr=False)
    # Validators from the HTTP response, carried through so the next fetch can
    # ask the server whether anything changed. Empty unless fetch.py set them.
    etag: str = field(default="", compare=False, repr=False)
    last_modified: str = field(default="", compare=False, repr=False)


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


def _reader_attrs(tag: str, attrs: dict[str, str | None], base_url: str) -> str:
    """The attributes the readable copy keeps: link targets and table spans."""
    if tag == "a":
        href = (attrs.get("href") or "").strip()
        if href:
            href = urljoin(base_url, href)
            if urlsplit(href).scheme in ("http", "https"):
                return f' href="{escape(href)}"'
    elif tag in ("td", "th"):
        # str.isdigit() is also true of "2" written as a superscript or in
        # another script, which no browser reads as a span.
        return "".join(
            f' {name}="{attrs[name]}"'
            for name in ("colspan", "rowspan")
            if (attrs.get(name) or "").isascii() and (attrs.get(name) or "").isdigit()
        )
    return ""


def _reader_html(items: list[tuple[str, str, str]]) -> str:
    """Join ("start" | "end" | "text", tag or text, attributes) items into balanced HTML.

    End tags without a matching start are dropped, and anything left open is
    closed, since the items are a selection from a possibly broken page.
    """
    out: list[str] = []
    stack: list[str] = []
    in_pre = 0
    for kind, value, attrs in items:
        if kind == "text":
            out.append(escape(value if in_pre else re.sub(r"\s+", " ", value), quote=False))
        elif kind == "start":
            out.append(f"<{value}{attrs}>")
            if value not in _READER_VOID:
                stack.append(value)
                in_pre += value == "pre"
        elif value in stack:
            while stack:
                tag = stack.pop()
                out.append(f"</{tag}>")
                in_pre -= tag == "pre"
                if tag == value:
                    break
    out.extend(f"</{tag}>" for tag in reversed(stack))
    html = "".join(out)
    # Wrappers left empty by dropped elements.
    while True:
        shorter = re.sub(r"<(div|p|li|span)>\s*</\1>", "", html)
        if shorter == html:
            return html.strip()
        html = shorter


class _TextParser(HTMLParser):
    def __init__(self, base_url: str = "") -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        # The readable copy: (kind, tag or text, attributes) items, with the
        # block each is in and whether it's inside <main>/<article>.
        self.items: list[tuple[str, str, str]] = []
        self.item_block: list[int] = []
        self.item_in_main: list[bool] = []
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
        # Blocks (_BLOCKS) for text density. Block 0 is the page itself; the
        # others are numbered in document order, so a parent's number is
        # always lower than its children's. For each block: its parent, and
        # the words of prose and of link text directly inside it.
        self.block_parent: list[int] = [-1]
        self.block_prose: list[int] = [0]
        self.block_links: list[int] = [0]
        self.part_block: list[int] = []  # the block of each item in parts
        self._open_blocks: list[tuple[str, int]] = []
        self._link_depth = 0

    @property
    def _block(self) -> int:
        return self._open_blocks[-1][1] if self._open_blocks else 0

    def _space(self) -> None:
        # Keep words from adjacent elements apart, e.g. <td>a</td><td>b</td>.
        self.parts.append(" ")
        self.part_block.append(self._block)
        if self._main_depth:
            self.main_parts.append(" ")

    def _open(self, tag: str) -> None:
        if tag == "a":
            self._link_depth += 1
        elif tag in _BLOCKS:
            if tag == "td" and self._open_blocks and self._open_blocks[-1][0] == "td":
                self._open_blocks.pop()  # <td>a<td>b: the end tag is optional
            self.block_parent.append(self._block)
            self.block_prose.append(0)
            self.block_links.append(0)
            self._open_blocks.append((tag, len(self.block_parent) - 1))

    def _close(self, tag: str) -> None:
        if tag == "a":
            self._link_depth = max(0, self._link_depth - 1)
        elif tag in _BLOCKS:
            # Close the innermost open block of this kind and any left open inside it.
            for i in range(len(self._open_blocks) - 1, -1, -1):
                if self._open_blocks[i][0] == tag:
                    del self._open_blocks[i:]
                    break
        elif tag in ("tr", "table"):
            while self._open_blocks and self._open_blocks[-1][0] == "td":
                self._open_blocks.pop()

    def _item(self, kind: str, value: str, attrs: str = "") -> None:
        self.items.append((kind, value, attrs))
        self.item_block.append(self._block)
        self.item_in_main.append(self._main_depth > 0)

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
        if self._skip_tag is None:
            self._open(tag)
            if tag in _READER_TAGS:
                name = _READER_TAGS[tag]
                self._item("start", name, _reader_attrs(name, dict(attrs), self.base_url))
        self._space()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # Self-closing tags like <div class="ad"/> have no content to skip.
        if self._skip_tag is None:
            if tag in _READER_VOID:
                self._item("start", tag)
            self._space()

    def handle_endtag(self, tag: str) -> None:
        self._raw_end(tag)
        if self._skip_tag is not None:
            if tag == self._skip_tag:
                self._skip_depth -= 1
                if not self._skip_depth:
                    self._skip_tag = None
            return
        if tag in _READER_TAGS and tag not in _READER_VOID:
            self._item("end", _READER_TAGS[tag])
        if tag == "title":
            self._in_title = False
        elif tag in _MAIN_TAGS and self._main_depth:
            self._main_depth -= 1
        self._close(tag)
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
        block = self._block
        self.part_block.append(block)
        if self._link_depth:
            self.block_links[block] += len(data.split())
        else:
            self.block_prose[block] += len(data.split())
        if self._main_depth:
            self.main_parts.append(data)
        self._item("text", data)


def _densest_block(parser: _TextParser) -> list[bool] | None:
    """Which blocks are inside the one holding most of the page's prose.

    None if that's the whole page or under MIN_MAIN_WORDS words.

    Each block scores the prose directly inside it plus half the score of each
    block directly inside it, scaled down by the share of its text that is
    links. From the best-scoring block, parents are tried until the block
    holds MIN_PROSE_SHARE of the page's prose.
    """
    parent = parser.block_parent
    count = len(parent)
    prose = parser.block_prose[:]
    links = parser.block_links[:]
    score = [0.0] * count
    from_children = [0.0] * count
    for i in range(count - 1, 0, -1):  # children before parents
        words = prose[i] + links[i]  # by now, totals for the whole block
        if words:
            score[i] = (parser.block_prose[i] + from_children[i]) * prose[i] / words
        from_children[parent[i]] += score[i] / 2
        prose[parent[i]] += prose[i]
        links[parent[i]] += links[i]
    if count < 2 or not prose[0]:
        return None
    best = max(range(1, count), key=score.__getitem__)
    while best and prose[best] < MIN_PROSE_SHARE * prose[0]:
        best = parent[best]
    if not best:
        return None
    inside = [False] * count
    inside[best] = True
    for i in range(best + 1, count):
        inside[i] = inside[parent[i]]
    prose = sum(parser.block_prose[i] for i in range(count) if inside[i])
    return inside if prose >= MIN_MAIN_WORDS else None


def extract_text(html: str, base_url: str = "") -> Page:
    """The page's title, its main text, and a readable copy of that text.

    ``base_url`` is the page's address, for making links in the copy absolute;
    without it, relative links lose their target.
    """
    parser = _TextParser(base_url)
    parser.feed(html)
    parser.close()
    title = normalize("".join(parser.title))

    def page(text_keep: list[bool] | None, item_keep: list[bool] | None) -> Page:
        parts, items = parser.parts, parser.items
        if text_keep is not None:
            parts = [p for p, keep in zip(parts, text_keep, strict=True) if keep]
            items = [i for i, keep in zip(items, item_keep or (), strict=True) if keep]
        return Page(title, normalize("".join(parts)), _reader_html(items))

    main = normalize("".join(parser.main_parts))
    if len(main.split()) >= MIN_MAIN_WORDS:
        return Page(
            title,
            main,
            _reader_html(
                [i for i, in_main in zip(parser.items, parser.item_in_main, strict=True) if in_main]
            ),
        )
    # If the boilerplate rules removed nearly everything (under 10% of the
    # visible text), a wrapper element was probably misjudged; index all the
    # visible text rather than an empty page. Small pages that are mostly
    # navigation keep their filtered text.
    text = normalize("".join(parser.parts))
    words = len(text.split())
    raw = normalize("".join(parser.raw_parts))
    if words < MIN_MAIN_WORDS and words * 10 < len(raw.split()):
        return Page(title, raw, f"<p>{escape(raw, quote=False)}</p>" if raw else "")
    inside = _densest_block(parser)
    if inside is None:
        return page(None, None)
    return page([inside[b] for b in parser.part_block], [inside[b] for b in parser.item_block])
