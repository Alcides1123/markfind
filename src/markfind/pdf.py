"""Text from bookmarked PDFs, with the optional ``pypdf`` package.

markfind itself needs only the standard library; PDFs are indexed when
``pypdf`` is installed (``pip install "markfind[pdf]"``). Without it, PDFs are
reported as unsupported, with a hint saying how to add support.
"""

from __future__ import annotations

import io
import logging
import re
from html import escape

from .extract import Page, normalize

# PDFs are often far bigger than web pages; these bound the time and memory
# one bookmark can take.
MAX_PDF_BYTES = 30_000_000
MAX_PAGES = 300

INSTALL_HINT = 'PDF: install pypdf to index PDFs (pip install "markfind[pdf]")'
NO_TEXT_MESSAGE = "no text found in the PDF; it may be scanned images"


class PdfUnsupportedError(Exception):
    """The PDF can't be read: pypdf is missing, or the file is protected or damaged."""


class PdfEmptyError(Exception):
    """The PDF was read but has no text, e.g. scanned pages."""


def available() -> bool:
    try:
        import pypdf  # noqa: F401
    except ImportError:
        return False
    return True


def extract_pdf(data: bytes) -> Page:
    """Title, text and a readable copy of a PDF's first MAX_PAGES pages."""
    try:
        import pypdf
    except ImportError:
        raise PdfUnsupportedError(INSTALL_HINT) from None

    # pypdf logs a warning for every font quirk it works around, and without
    # logging set up those land in the middle of `markfind fetch`'s output.
    # Quiet them, unless the application has chosen a level itself.
    logger = logging.getLogger("pypdf")
    if logger.level == logging.NOTSET:
        logger.setLevel(logging.ERROR)

    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        # Many PDFs are "encrypted" with an empty password just to set
        # permissions; those open fine.
        if reader.is_encrypted and not reader.decrypt(""):
            raise PdfUnsupportedError("PDF is password-protected")
        pages = [page.extract_text() or "" for page in reader.pages[:MAX_PAGES]]
        total = len(reader.pages)
        metadata = reader.metadata
        title = normalize(str(metadata.title or "")) if metadata else ""
    except PdfUnsupportedError:
        raise
    except Exception as exc:  # noqa: BLE001 - pypdf raises many types on damaged files
        raise PdfUnsupportedError(f"PDF could not be read: {exc}") from exc

    text = normalize(" ".join(pages))
    if not text:
        raise PdfEmptyError(NO_TEXT_MESSAGE)
    return Page(title=title, text=text, html=_copy(pages, total))


def _copy(pages: list[str], total: int) -> str:
    """The saved copy: each page's paragraphs, pages separated by a rule."""
    parts = []
    for text in pages:
        paragraphs = (normalize(p) for p in re.split(r"\n\s*\n", text))
        parts.append("".join(f"<p>{escape(p, quote=False)}</p>" for p in paragraphs if p))
    html = "<hr>".join(part for part in parts if part)
    if total > len(pages):
        html += f"<hr><p><em>Only the first {len(pages)} of {total} pages were saved.</em></p>"
    return html
