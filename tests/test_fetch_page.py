import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from markfind.fetch import UnsupportedContentError, fetch_page, sniff_content_type

HTML = b"""<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN">
<HTML><HEAD><TITLE>Redirector</TITLE>
<script>document.write("<script src=x.js><\\/script>");</script></HEAD>
<BODY><p>Welcome back</p></BODY></HTML>"""

# path -> (Content-Type header or None to leave it out, body)
PAGES = {
    "/html": ("text/html; charset=utf-8", HTML),
    "/no-type-html": (None, HTML),
    "/no-type-html-bom": (None, b"\xef\xbb\xbf\n  <html><body>Hello there</body></html>"),
    "/no-type-text": (None, b"Just some notes.\nSecond line."),
    "/no-type-pdf": (None, b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj"),
    "/no-type-binary": (None, b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"),
    "/plain-html": ("text/plain", HTML),
    "/plain-text": ("text/plain; charset=utf-8", b"<b> is how you write bold in HTML."),
    "/pdf": ("application/pdf", b"%PDF-1.7"),
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        content_type, body = PAGES[self.path]
        self.send_response(200)
        if content_type:
            self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def base_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02})
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    thread.join()
    server.server_close()


@pytest.mark.parametrize("path", ["/html", "/no-type-html", "/plain-html"])
def test_html_is_extracted_whatever_the_label(base_url, path):
    page = fetch_page(base_url + path)
    assert page.title == "Redirector"
    assert page.text == "Welcome back"


def test_html_after_bom_and_whitespace(base_url):
    assert fetch_page(base_url + "/no-type-html-bom").text == "Hello there"


def test_plain_text_stays_text(base_url):
    assert fetch_page(base_url + "/no-type-text").text == "Just some notes. Second line."
    # Real text that merely mentions a tag isn't mistaken for HTML.
    assert fetch_page(base_url + "/plain-text").text == "<b> is how you write bold in HTML."


@pytest.mark.parametrize("path", ["/no-type-pdf", "/no-type-binary", "/pdf"])
def test_binary_is_unsupported(base_url, path):
    with pytest.raises(UnsupportedContentError):
        fetch_page(base_url + path)


@pytest.mark.parametrize(
    "declared, body, expected",
    [
        ("text/html", b"anything", "text/html"),
        ("application/json", b"<html>", "application/json"),
        (None, b"<!doctype html><title>x</title>", "text/html"),
        (None, b"  <HEAD><title>x</title>", "text/html"),
        ("text/plain", b"<BODY>hi", "text/html"),
        ("text/plain", b"<html-is-not-a-tag>", "text/plain"),
        (None, b"<html", "text/html"),
        (None, b"", "text/plain"),
        (None, b"%PDF-1.4", None),
        ("text/plain", b"%PDF-1.4", "text/plain"),  # declared text is trusted unless it's HTML
    ],
)
def test_sniff_content_type(declared, body, expected):
    assert sniff_content_type(declared, body) == expected
