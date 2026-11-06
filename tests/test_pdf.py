"""PDF bookmarks: indexed with the optional pypdf package, reported clearly without it."""

import io
import logging
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from markfind import cli, fetch, pdf
from markfind.fetch import EmptyPageError, UnsupportedContentError, fetch_page
from markfind.pdf import INSTALL_HINT, PdfEmptyError, PdfUnsupportedError, extract_pdf


def make_pdf(pages, title=None):
    """A small, valid PDF with one line of Helvetica text per string in ``pages``."""
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        None,
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    kids = []
    for text in pages:
        lines = text.split("\n")
        ops = ["BT", "/F1 12 Tf", "14 TL", "72 720 Td"]
        for line in lines:
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            ops.append(f"({escaped}) Tj T*")
        ops.append("ET")
        stream = "\n".join(ops)
        objects.append(
            f"<< /Length {len(stream.encode('latin-1'))} >>\nstream\n{stream}\nendstream"
        )
        content = len(objects)
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]"
            f" /Resources << /Font << /F1 3 0 R >> >> /Contents {content} 0 R >>"
        )
        kids.append(f"{len(objects)} 0 R")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    info = None
    if title is not None:
        objects.append(f"<< /Title ({title}) >>")
        info = len(objects)

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n{body}\nendobj\n".encode("latin-1"))
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    trailer = (
        f"<< /Size {len(objects) + 1} /Root 1 0 R" + (f" /Info {info} 0 R" if info else "") + " >>"
    )
    out.write(f"trailer\n{trailer}\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


PAPER = make_pdf(
    [
        "A study of bookmark rot\n\nLinks decay over time <b>fast</b>.",
        "Second page about archives.",
    ],
    title="Bookmark Rot",
)


# --- without pypdf: the core stays standard-library only ---------------------


def test_without_pypdf_pdfs_are_unsupported_with_a_hint(monkeypatch):
    monkeypatch.setitem(sys.modules, "pypdf", None)  # makes `import pypdf` fail
    assert not pdf.available()
    with pytest.raises(PdfUnsupportedError) as error:
        extract_pdf(PAPER)
    assert str(error.value) == INSTALL_HINT
    assert 'pip install "markfind[pdf]"' in INSTALL_HINT


# --- with pypdf ---------------------------------------------------------------

needs_pypdf = pytest.mark.skipif(not pdf.available(), reason='needs pypdf: pip install -e ".[dev]"')


@needs_pypdf
def test_text_title_and_saved_copy():
    page = extract_pdf(PAPER)
    assert page.title == "Bookmark Rot"
    assert "Links decay over time <b>fast</b>." in page.text
    assert "Second page about archives." in page.text
    # One paragraph per block of text, pages separated by a rule, all escaped.
    assert "&lt;b&gt;fast&lt;/b&gt;" in page.html and "<b>" not in page.html
    assert page.html.count("<hr>") == 1
    assert page.html.startswith("<p>") and page.html.endswith("</p>")


@needs_pypdf
def test_only_the_first_pages_are_read(monkeypatch):
    monkeypatch.setattr(pdf, "MAX_PAGES", 1)
    page = extract_pdf(PAPER)
    assert "Second page" not in page.text
    assert "Only the first 1 of 2 pages were saved." in page.html


@needs_pypdf
def test_pdf_without_text_is_empty():
    with pytest.raises(PdfEmptyError, match="scanned images"):
        extract_pdf(make_pdf([""]))


def encrypted(user_password):
    import pypdf

    writer = pypdf.PdfWriter(clone_from=io.BytesIO(PAPER))
    writer.encrypt(user_password=user_password, owner_password="owner", algorithm="RC4-128")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


@needs_pypdf
def test_protected_pdfs():
    # Encrypted only to set permissions: it opens without a password.
    assert "Second page about archives." in extract_pdf(encrypted("")).text
    with pytest.raises(PdfUnsupportedError, match="password-protected"):
        extract_pdf(encrypted("secret"))


@needs_pypdf
def test_damaged_pdf():
    with pytest.raises(PdfUnsupportedError, match="PDF could not be read"):
        extract_pdf(b"%PDF-1.7\nthis is not really a PDF")


# --- fetching -------------------------------------------------------------------

PAGES = {
    "/paper.pdf": ("application/pdf", PAPER),
    "/x-pdf": ("application/x-pdf", PAPER),
    "/download": (None, PAPER),  # no Content-Type: recognised by its signature
    "/scan.pdf": ("application/pdf", make_pdf([""])),
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


@needs_pypdf
@pytest.mark.parametrize("path", ["/paper.pdf", "/x-pdf", "/download"])
def test_fetch_pdf(base_url, path):
    page = fetch_page(base_url + path)
    assert page.title == "Bookmark Rot"
    assert "Links decay over time" in page.text
    assert "<hr>" in page.html


@needs_pypdf
def test_fetch_reads_pdfs_past_the_page_size_limit(base_url, monkeypatch):
    # Web pages are cut at max_bytes, but a cut PDF is unreadable.
    assert "Second page" in fetch_page(base_url + "/paper.pdf", max_bytes=100).text
    monkeypatch.setattr(fetch, "MAX_PDF_BYTES", 200)
    with pytest.raises(UnsupportedContentError, match="PDF larger than"):
        fetch_page(base_url + "/paper.pdf", max_bytes=100)


@needs_pypdf
def test_fetch_scanned_pdf_is_empty(base_url):
    with pytest.raises(EmptyPageError, match="scanned images"):
        fetch_page(base_url + "/scan.pdf")
    assert fetch.classify_error(EmptyPageError("x"))[0] == "empty"


@needs_pypdf
def test_pdf_bookmark_end_to_end(base_url, tmp_path, capsys):
    db = str(tmp_path / "db.sqlite")
    export = tmp_path / "bookmarks.html"
    export.write_text(
        f'<DL><p><DT><A HREF="{base_url}/paper.pdf">paper.pdf</A></DL><p>', encoding="utf-8"
    )
    assert cli.main(["--db", db, "import", str(export)]) == 0
    assert cli.main(["--db", db, "fetch", "--delay", "0", "-q"]) == 0
    capsys.readouterr()

    assert cli.main(["--db", db, "search", "archives"]) == 0
    assert f"{base_url}/paper.pdf" in capsys.readouterr().out
    assert cli.main(["--db", db, "archive", f"{base_url}/paper.pdf", "--text"]) == 0
    assert "Links decay over time" in capsys.readouterr().out


@needs_pypdf
def test_pypdf_warnings_are_quiet_unless_configured():
    logger = logging.getLogger("pypdf")
    saved = logger.level
    try:
        logger.setLevel(logging.NOTSET)
        extract_pdf(PAPER)
        assert logger.level == logging.ERROR  # not printed in the middle of `fetch`
        logger.setLevel(logging.DEBUG)  # an application's own choice is kept
        extract_pdf(PAPER)
        assert logger.level == logging.DEBUG
    finally:
        logger.setLevel(saved)
