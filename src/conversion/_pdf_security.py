"""Shared xhtml2pdf resource-resolution guard for markdown_to_pdf and html_to_pdf.

Both converters render user/LLM-influenced content through xhtml2pdf's
pisa.CreatePDF, which by default resolves any <img src>/CSS background/
@font-face reference reachable from the rendered HTML via a REAL outbound
HTTP GET (xhtml2pdf/files.py: FileNetworkManager/NetworkFileUri) -- no
timeout override, no response-size cap, no destination allowlist. Since
content can originate from an upstream LLM/HTTP/MCP node, a workflow
rendering attacker-influenced content containing e.g.
`![x](http://169.254.169.254/latest/meta-data/)` would trigger an
unguarded outbound request purely by being rendered -- the same class of
SSRF already closed for the HTTP node executor (see src/executors/http.py
+ src/security/ssrf.py).

Extracted from markdown_to_pdf.py (originally 2026-07-11) so html_to_pdf.py
gets the identical guarantee from the same, once-tested implementation
rather than a second copy that could drift.
"""


def deny_all_resources(uri: object, basepath: object) -> str:
    """xhtml2pdf `link_callback` wired into every resource resolution
    (`<img src>`, CSS backgrounds, `@font-face`, ...) reachable from the
    HTML handed to `pisa.CreatePDF`.

    Without this, xhtml2pdf resolves any `http(s)://` reference through its
    own file-manager machinery (xhtml2pdf/files.py: FileNetworkManager /
    NetworkFileUri), which performs a REAL outbound HTTP GET via `httplib`
    with no timeout override, no response-size cap, and no destination
    allowlist.

    There is no legitimate use case today for embedding remote (or local)
    images in the documents these converters produce, so a flat deny-all
    is the correct scope here.

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


__all__ = ["deny_all_resources"]
