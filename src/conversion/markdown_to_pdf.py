"""Markdown -> PDF renderer for the file-write node.

Markdown -> HTML (markdown-it-py) -> PDF (xhtml2pdf) — pure-Python, no
system binaries/packages, keeping the existing multi-stage Docker image
lean. See docs/archive/phase-history/specs/2026-07-11-file-storage-provider-framework-design.md §C
for why this was chosen over a pandoc-based approach.
"""

import io
from typing import Any, cast

from markdown_it import MarkdownIt
from xhtml2pdf import pisa  # pyright: ignore[reportMissingTypeStubs]

from src.conversion._pdf_security import deny_all_resources

# `html: False` disables CommonMark's raw-HTML passthrough rules
# (html_block/html_inline). Without it, bracket placeholders extremely
# common in the BRD-style documents this node targets -- `<Client Name>`,
# `<INSERT DATE>` -- parse as (unrecognized) inline HTML tags and get
# silently dropped by xhtml2pdf's HTML parser instead of rendering as
# literal, escaped text. See docs/archive/phase-history/plans/
# 2026-07-11-file-storage-provider-framework-plan.md Task 6/7 follow-up.
_md = MarkdownIt("commonmark", {"html": False}).enable("table")

_PDF_STYLE = """
<style>
  body { font-family: Helvetica, Arial, sans-serif; font-size: 10pt; }
  h1, h2, h3 { color: #1e293b; }
  table { border-collapse: collapse; width: 100%; margin: 8px 0; }
  th, td { border: 1px solid #94a3b8; padding: 4px 8px; text-align: left; }
</style>
"""


class MarkdownToPdfError(RuntimeError):
    """Raised when xhtml2pdf fails to render the generated HTML."""


def markdown_to_pdf(content: str) -> bytes:
    html_body = _md.render(content)
    full_html = f"<html><head>{_PDF_STYLE}</head><body>{html_body}</body></html>"

    buf = io.BytesIO()
    result = cast(
        "Any",
        pisa.CreatePDF(  # pyright: ignore[reportUnknownMemberType]
            io.StringIO(full_html), dest=buf, link_callback=deny_all_resources
        ),
    )
    # pisaDocument() (aliased as pisa.CreatePDF) only returns raw `bytes` when
    # called with dest_bytes=True, which we never pass; with `dest` supplied
    # it returns a pisaContext exposing `.err`. xhtml2pdf ships no type stubs,
    # so pyright can't infer that dest_bytes-conditional return on its own —
    # narrow explicitly rather than suppressing the check.
    if isinstance(result, bytes):
        raise MarkdownToPdfError(
            "xhtml2pdf unexpectedly returned raw bytes instead of a status object"
        )
    err = cast("int", result.err)
    if err:
        raise MarkdownToPdfError(f"xhtml2pdf failed with err={err}")
    return buf.getvalue()


__all__ = ["MarkdownToPdfError", "markdown_to_pdf"]
