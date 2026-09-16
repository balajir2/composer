# Design: Feature 2 Foundation — Raw HTML file-write format + `download-pdf` node

**Date:** 2026-07-20
**Status:** Approved (design phase) — implementation plan not yet written
**Origin:** a customer's "Jira-Driven Weekly Tracking & Executive Project Reporting" BRD, Feature 2
(Executive Weekly Report in HTML/PDF). This is new platform capability built on top of the
already-complete Composer engine and the Jira/Confluence foundation shipped 2026-07-19 — not
OAB-parity work, does not touch `D:/GitHub/open-agent-builder`.

## Why this spec exists

The BRD's Feature 2 wants a leadership report that's both directly browsable (responsive HTML,
charts, print CSS) and downloadable as a static PDF (FR-014: "Chrome/Edge PDF export passes
visual QA"). Two gaps stood between Composer's current node set and that:

1. `file-write`'s only formats (`md`/`docx`/`pdf`) all route through a Markdown parser that
   deliberately blocks raw HTML passthrough and external resource resolution — it cannot produce
   a genuine HTML file with embedded charts/print CSS.
2. There was no way to render a real PDF from that same HTML content inside a workflow at all —
   only from Markdown, via the existing `file-write` `pdf` format.

This spec covers both gaps. The actual Feature 2 workflow graph (Jira extract → metrics → the
executive-narrative agent → these two output nodes) is a separate, later design — this spec is
foundation only, matching how the Jira extract-operation and Confluence node specs were scoped
the day before.

## Decisions locked by user Q&A during brainstorming

- `file-write`'s new `html` format is a **raw passthrough** — content bytes written verbatim, no
  conversion, no sanitization beyond what already exists elsewhere in the pipeline. The workflow's
  own agent node is responsible for emitting complete, valid HTML (including any `<style>` block
  for print CSS).
- PDF generation is **not** folded into `file-write` as another format value. It's a **new,
  dedicated node type**, `download-pdf` — more discoverable in the palette than a buried format
  option, consistent with how `email`/`jira`/`confluence` are each their own node type rather than
  sub-modes of something generic.
- `download-pdf` accepts **two input formats, explicitly selected** (`html` or `markdown`) via an
  `inputFormat` field — no format auto-detection/guessing.
- `download-pdf` reuses the **existing** `xhtml2pdf`-based rendering machinery
  (`src/conversion/markdown_to_pdf.py`'s `pisa.CreatePDF` call and its SSRF-blocking
  `_deny_all_resources` `link_callback`) rather than introducing a new PDF rendering engine.
- **Known constraint to carry into the eventual Feature 2 workflow design:** `xhtml2pdf` supports
  only a CSS 2.1-ish subset (tables, basic properties, `page-break-*`) — no flexbox/grid/modern
  CSS. The browser-viewable `.html` file can use full modern CSS; the auto-rendered PDF cannot.
  The executive-narrative agent's prompt (future work) needs to be told to keep its HTML's styling
  within that older subset if the PDF rendering is expected to look right, not just the HTML.

## Component 1 — `file-write` node: `format: "html"`

**File-write** (`src/executors/file_write.py`, `FileWriteNodeData` in `src/engine/workflow.py`):

- Add `"html"` to `FileWriteNodeData.format`'s `Literal["md", "docx", "pdf"]` type.
- Add `"html": "html"` to the `_EXTENSIONS` map.
- In `_convert()`, add a branch identical in shape to the existing `md` branch:
  ```python
  if fmt == "html":
      return content.encode("utf-8")
  ```
- Designer panel (`frontend/components/composer/canvas/node-panels/file-write.tsx`): add
  `{ value: "html", label: "HTML (.html)" }` to the format dropdown's options array.

No other change to `file-write` — `destinationPath`/`filename` validation, the storage-provider
abstraction, and every other format's behavior are untouched.

## Component 2 — new `download-pdf` node

**Schema** (`src/engine/workflow.py`), new `DownloadPdfNodeData`/`DownloadPdfNode` classes
mirroring `FileWriteNodeData`'s shape:

| Field | Purpose |
|---|---|
| `inputFormat` | `Literal["html", "markdown"]`, required, no default (explicit choice, no guessing) |
| `content` | Source text, Mustache-substituted like every other node's content field |
| `provider` | Storage provider name, plain string (matches `file-write`'s "unrecognized provider raises at execution time" convention) — `"local"` for now |
| `destinationPath` | Folder to write into, substituted; same `InvalidDestinationError` behavior as `file-write` (blank/empty rejected, never falls back to the server process's cwd) |
| `filename` | Bare filename, no extension, `.pdf` always appended; same `InvalidFilenameError` behavior as `file-write` (no path separators, no `..`) |

**Executor** (`src/executors/download_pdf.py`, new file):

```
arun():
  destination = substitute(destinationPath); validate (reuse file_write's validators or
    equivalent — see "shared validation" note below)
  filename = substitute(filename); validate
  content = substitute(content)
  if inputFormat == "markdown":
      pdf_bytes = markdown_to_pdf(content)          # existing, unchanged
  else:
      pdf_bytes = html_to_pdf(content)               # new
  provider.write_file(destination, f"{filename}.pdf", pdf_bytes)
  return {lastOutput: written_path, ...}
```

**Shared validation note:** `file_write.py`'s `_validate_filename`/`InvalidDestinationError`/
`InvalidFilenameError` are currently private to that module. Since `download-pdf` needs
byte-for-byte identical destination/filename safety guarantees (same untrusted-upstream-content
threat model — content can originate from an LLM/HTTP/MCP node), these move to a small shared
module (e.g. `src/executors/_file_output_validation.py`) that both `file_write.py` and the new
`download_pdf.py` import, rather than duplicating the validation logic or its tests.

**New conversion module** (`src/conversion/html_to_pdf.py`):

```python
def html_to_pdf(content: str) -> bytes:
    buf = io.BytesIO()
    result = pisa.CreatePDF(io.StringIO(content), dest=buf, link_callback=_deny_all_resources)
    # same isinstance/err handling as markdown_to_pdf
    return buf.getvalue()
```

`_deny_all_resources` moves out of `markdown_to_pdf.py` into a shared location (e.g.
`src/conversion/_pdf_security.py`) that both `markdown_to_pdf.py` and `html_to_pdf.py` import —
same reasoning as the shared file-output validation above: one security-critical function, tested
once, used by both converters, not copy-pasted.

**Designer UI:** new `download-pdf` node type registered across the same 6 integration points the
`confluence` node used — `node-panels/download-pdf.tsx` (credentials-free; just
inputFormat/content/provider/destinationPath/filename), `property-panel.tsx` (`PANEL_MAP` +
`TYPE_LABELS`), `tools-palette.tsx` (`COMPOSER_NODE_PALETTE`), `node-visuals.ts` (icon + accent
color), `workflow-canvas.tsx` (`COMPOSER_NODE_TYPES`), `graph_builder.py` (side-effect import
registration).

## Testing

- **`file-write` `html` format**: one new unit test mirroring the existing `md`-format test
  exactly — raw content bytes written verbatim to a `.html`-suffixed path, no conversion applied.
  One small frontend test confirming the new dropdown option is selectable and calls
  `onChange({ format: "html" })`.
- **`download-pdf` executor**: unit tests per `inputFormat` — `"markdown"` produces the same
  bytes `markdown_to_pdf()` would (delegation, not reimplementation); `"html"` renders via the new
  `html_to_pdf()` and produces valid PDF bytes (`%PDF` magic-number check, matching however the
  existing `markdown_to_pdf` tests already assert PDF-ness); destination/filename validation
  errors match `file_write`'s existing error-class tests, applied to the new node.
- **`html_to_pdf()` SSRF protection**: a test asserting that HTML containing an external resource
  reference (`<img src="http://...">` or similar) does not trigger any outbound network call
  during rendering — mirroring whatever test already exists for `markdown_to_pdf`'s
  `_deny_all_resources` callback, now exercised through the shared location both converters import
  from.
- **Frontend `download-pdf` panel**: input-format toggle reveals/hides nothing conditionally
  (both formats share the same `content` field) but the test suite should still confirm the
  dropdown, content field, destinationPath, and filename fields all wire their `onChange` calls
  correctly — same shape as the `confluence` panel's tests.

## Explicitly deferred to later sub-projects

- The actual Feature 2 workflow graph (Jira extract → metrics/diligence transform → executive
  narrative agent → `file-write` html + `download-pdf` for the PDF).
- Resolving whatever CSS constraints the executive-narrative agent's prompt needs to respect for
  `xhtml2pdf` to render the PDF acceptably — a workflow-prompt-engineering concern, not a platform
  concern, addressed when that agent's prompt is written.
- Any docx/other-format additions to `download-pdf` — out of scope, not requested.
