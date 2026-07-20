"""HTML -> PDF renderer for the download-pdf node.

HTML -> PDF (xhtml2pdf), skipping the Markdown-parsing stage entirely --
the content handed in is already complete HTML (typically produced by an
upstream `agent` node), not Markdown. Shares the same SSRF-blocking
link_callback as markdown_to_pdf.py's converter (src/conversion/_pdf_security.py)
since both feed content through the identical pisa.CreatePDF call against
the identical untrusted-content threat model.

Known constraint: xhtml2pdf supports only a CSS 2.1-ish subset (tables,
basic properties, page-break-*) -- no flexbox/grid/modern CSS. Content
relying on modern layout CSS will render acceptably as a standalone .html
file (viewed in a real browser) but may render incorrectly here.
"""

import io

from xhtml2pdf import pisa

from src.conversion._pdf_security import deny_all_resources


class HtmlToPdfError(RuntimeError):
    """Raised when xhtml2pdf fails to render the given HTML."""


def html_to_pdf(content: str) -> bytes:
    buf = io.BytesIO()
    result = pisa.CreatePDF(io.StringIO(content), dest=buf, link_callback=deny_all_resources)
    # pisaDocument() (aliased as pisa.CreatePDF) only returns raw `bytes` when
    # called with dest_bytes=True, which we never pass; with `dest` supplied
    # it returns a pisaContext exposing `.err`. xhtml2pdf ships no type stubs,
    # so pyright can't infer that dest_bytes-conditional return on its own --
    # narrow explicitly rather than suppressing the check.
    if isinstance(result, bytes):
        raise HtmlToPdfError("xhtml2pdf unexpectedly returned raw bytes instead of a status object")
    if result.err:
        raise HtmlToPdfError(f"xhtml2pdf failed with err={result.err}")
    return buf.getvalue()


__all__ = ["HtmlToPdfError", "html_to_pdf"]
