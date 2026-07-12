"""Markdown -> PDF renderer for the file-write node.

Markdown -> HTML (markdown-it-py) -> PDF (xhtml2pdf) — pure-Python, no
system binaries/packages, keeping the existing multi-stage Docker image
lean. See docs/archive/phase-history/specs/2026-07-11-file-storage-provider-framework-design.md §C
for why this was chosen over a pandoc-based approach.
"""

import io

from markdown_it import MarkdownIt
from xhtml2pdf import pisa

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


def _deny_all_resources(uri: object, basepath: object) -> str:
    """xhtml2pdf `link_callback` wired into every resource resolution
    (`<img src>`, CSS backgrounds, `@font-face`, ...) reachable from the
    HTML this module hands to `pisa.CreatePDF`.

    Without this, xhtml2pdf resolves any `http(s)://` reference through its
    own file-manager machinery (xhtml2pdf/files.py: FileNetworkManager /
    NetworkFileUri), which performs a REAL outbound HTTP GET via `httplib`
    with no timeout override, no response-size cap, and no destination
    allowlist. Since file-write's PDF content can originate from upstream
    node output (LLM/agent/HTTP/MCP results), a workflow rendering
    attacker-influenced markdown containing e.g.
    `![x](http://169.254.169.254/latest/meta-data/)` would trigger an
    unguarded outbound request purely by being rendered to PDF -- the same
    class of SSRF already closed for the HTTP node executor (see
    src/executors/http.py + src/security/ssrf.py, commit 999c5ac).

    There is no legitimate use case today for embedding remote (or local)
    images in file-write-generated documents (this feature is
    text/table/BRD-focused), so a flat deny-all is the correct scope here.

    IMPORTANT xhtml2pdf-specific gotcha: this installed version's
    `pisaFileObject.__init__` (xhtml2pdf/files.py) only overrides the
    requested uri/basepath when the callback's return value is *truthy*:

        if callback and (new := callback(uri, basepath)):
            self.uri = new
            self.basepath = None

    Returning `None` is a no-op here -- resolution would proceed against
    the ORIGINAL uri/basepath, which for an `http(s)://` source still
    reaches `NetworkFileUri` and performs the real GET. So instead of
    returning `None`, this callback unconditionally remaps every request
    to a fixed, scheme-less placeholder name. `FileNetworkManager` then
    routes it to `LocalFileURI`, which does a local `Path.exists()` check,
    finds nothing, and returns no data -- the resource renders as
    blank/omitted with no network I/O at all.
    """
    del uri, basepath  # unconditional deny-all; inputs are irrelevant
    return "composer-blocked-external-resource"


def markdown_to_pdf(content: str) -> bytes:
    html_body = _md.render(content)
    full_html = f"<html><head>{_PDF_STYLE}</head><body>{html_body}</body></html>"

    buf = io.BytesIO()
    result = pisa.CreatePDF(io.StringIO(full_html), dest=buf, link_callback=_deny_all_resources)
    # pisaDocument() (aliased as pisa.CreatePDF) only returns raw `bytes` when
    # called with dest_bytes=True, which we never pass; with `dest` supplied
    # it returns a pisaContext exposing `.err`. xhtml2pdf ships no type stubs,
    # so pyright can't infer that dest_bytes-conditional return on its own —
    # narrow explicitly rather than suppressing the check.
    if isinstance(result, bytes):
        raise MarkdownToPdfError(
            "xhtml2pdf unexpectedly returned raw bytes instead of a status object"
        )
    if result.err:
        raise MarkdownToPdfError(f"xhtml2pdf failed with err={result.err}")
    return buf.getvalue()


__all__ = ["MarkdownToPdfError", "markdown_to_pdf"]
