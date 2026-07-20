# Download PDF Node Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A new `download-pdf` node type that renders HTML or Markdown content to a PDF file and writes it via a storage provider — the PDF-producing counterpart to `file-write`, as its own dedicated node type rather than a buried format option.

**Architecture:** A new executor (`src/executors/download_pdf.py`) mirroring `file_write.py`'s destination/filename validation pattern (duplicated locally, not imported cross-module — small enough that independent node modules are cleaner than coupling them together for ~10 lines) and reusing the existing `FileStorageProvider`/`LocalFilesystemProvider` abstraction unchanged. A new `html_to_pdf()` converter reuses the *existing*, already-hardened SSRF-blocking `link_callback` from `markdown_to_pdf.py` — extracted into a small shared module (`src/conversion/_pdf_security.py`) so both converters get the identical guarantee from one tested implementation, not two copies that could drift. `markdown_to_pdf()` itself is unchanged in behavior, just refactored to import the shared callback.

**Tech Stack:** Python 3.11, Pydantic v2, `xhtml2pdf` (already a dependency), pytest (backend). Next.js/React, vitest (frontend).

**Spec:** `docs/superpowers/specs/2026-07-20-feature2-pdf-html-foundation-design.md` (Component 2).

---

## Scope note (platform vs. customer flow)

This is generic platform capability — no Macy's-specific paths, filenames, or content appears anywhere in this plan's code. A future Feature 2 workflow will *use* this node with its own configuration, which is out of scope here.

---

### Task 1: Extract the shared SSRF-blocking callback

**Files:**
- Create: `src/conversion/_pdf_security.py`
- Modify: `src/conversion/markdown_to_pdf.py`
- Test: `tests/unit/conversion/test_markdown_to_pdf.py` (existing tests must still pass unmodified — this is a pure refactor, no behavior change)

- [ ] **Step 1: Run the existing tests first to confirm a clean baseline**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/conversion/test_markdown_to_pdf.py -v` (from the repo root; use `/d/GitHub/composer/.venv/Scripts/python.exe` if there's no local `.venv`; try `uv run pytest ...` first if `uv` is on your PATH)

Expected: PASS (all existing tests, before you touch anything)

- [ ] **Step 2: Create the shared module**

Create `src/conversion/_pdf_security.py`:

```python
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
```

- [ ] **Step 3: Update `markdown_to_pdf.py` to use the shared function**

In `src/conversion/markdown_to_pdf.py`:

Remove the entire `_deny_all_resources` function definition (currently lines 37-76 — the whole function, including its docstring).

Add this import near the top of the file (alongside the existing `from xhtml2pdf import pisa`):

```python
from src.conversion._pdf_security import deny_all_resources
```

Update the call site in `markdown_to_pdf()` (currently line 84) from:

```python
    result = pisa.CreatePDF(io.StringIO(full_html), dest=buf, link_callback=_deny_all_resources)
```

to:

```python
    result = pisa.CreatePDF(io.StringIO(full_html), dest=buf, link_callback=deny_all_resources)
```

The rest of `markdown_to_pdf.py` (the `_md` MarkdownIt instance, `_PDF_STYLE`, `MarkdownToPdfError`, `markdown_to_pdf()`'s body) is unchanged.

- [ ] **Step 4: Run the existing tests again to confirm no regression**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/conversion/test_markdown_to_pdf.py -v`

Expected: PASS — every test, including `test_ssrf_image_reference_does_not_trigger_network_call` (it patches `xhtml2pdf.files.NetworkFileUri.get_httplib` directly, not the callback by name, so it's unaffected by the callback moving modules)

- [ ] **Step 5: Run the full backend test suite and lint/format checks**

Run: `.venv/Scripts/python.exe -m pytest -m "not integration" -q && .venv/Scripts/python.exe -m ruff check src tests && .venv/Scripts/python.exe -m ruff format --check src tests`

Expected: all clean

- [ ] **Step 6: Commit**

```bash
git add src/conversion/_pdf_security.py src/conversion/markdown_to_pdf.py
git commit -m "$(cat <<'EOF'
refactor(conversion): extract SSRF-blocking callback to a shared module

html_to_pdf.py (next task) needs the identical xhtml2pdf resource-
resolution guard markdown_to_pdf.py already has. Extracting it now so
both converters share one tested implementation instead of a second
copy that could drift. Pure refactor - markdown_to_pdf's own tests
pass unmodified, confirming no behavior change.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `html_to_pdf()` converter

**Files:**
- Create: `src/conversion/html_to_pdf.py`
- Test: `tests/unit/conversion/test_html_to_pdf.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/conversion/test_html_to_pdf.py`:

```python
"""Tests for the HTML -> PDF renderer.

Mirrors tests/unit/conversion/test_markdown_to_pdf.py's pattern -- xhtml2pdf
output isn't practical to assert on structurally, so these check for a
well-formed PDF (correct magic bytes) rather than parsing rendered content.
"""


def test_produces_valid_pdf_bytes() -> None:
    from src.conversion.html_to_pdf import html_to_pdf

    result = html_to_pdf("<html><body><h1>Title</h1><p>Some body text.</p></body></html>")
    assert result.startswith(b"%PDF-")
    assert len(result) > 100


def test_empty_content_still_produces_valid_pdf() -> None:
    from src.conversion.html_to_pdf import html_to_pdf

    result = html_to_pdf("")
    assert result.startswith(b"%PDF-")


def test_table_content_does_not_raise() -> None:
    from src.conversion.html_to_pdf import html_to_pdf

    html = "<html><body><table><tr><td>A</td><td>B</td></tr></table></body></html>"
    result = html_to_pdf(html)
    assert result.startswith(b"%PDF-")


def test_conversion_failure_raises_clear_error(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import src.conversion.html_to_pdf as mod
    from src.conversion.html_to_pdf import HtmlToPdfError, html_to_pdf

    class _FakeStatus:
        err = 1

    monkeypatch.setattr(
        mod, "pisa", type("P", (), {"CreatePDF": staticmethod(lambda *a, **kw: _FakeStatus())})
    )
    import pytest

    with pytest.raises(HtmlToPdfError):
        html_to_pdf("<html><body>Title</body></html>")


def test_ssrf_image_reference_does_not_trigger_network_call(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """An <img> tag pointing at a cloud-metadata-style address must not
    cause any outbound network I/O when rendered through html_to_pdf().
    Same threat model and same verification seam as
    test_markdown_to_pdf.py's identically-named test."""
    from xhtml2pdf.files import NetworkFileUri

    from src.conversion.html_to_pdf import html_to_pdf

    calls: list[str] = []

    def _fail_if_called(self: object, uri: str) -> tuple[bytes | None, bool]:  # type: ignore[no-untyped-def]
        calls.append(uri)
        raise AssertionError(f"unexpected outbound network fetch attempted for {uri!r}")

    monkeypatch.setattr(NetworkFileUri, "get_httplib", _fail_if_called)

    content = '<html><body><img src="http://169.254.169.254/latest/meta-data/"></body></html>'
    result = html_to_pdf(content)

    assert calls == []
    assert result.startswith(b"%PDF-")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/conversion/test_html_to_pdf.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'src.conversion.html_to_pdf'`

- [ ] **Step 3: Implement the converter**

Create `src/conversion/html_to_pdf.py`:

```python
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
        raise HtmlToPdfError(
            "xhtml2pdf unexpectedly returned raw bytes instead of a status object"
        )
    if result.err:
        raise HtmlToPdfError(f"xhtml2pdf failed with err={result.err}")
    return buf.getvalue()


__all__ = ["HtmlToPdfError", "html_to_pdf"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/conversion/test_html_to_pdf.py -v`

Expected: PASS

- [ ] **Step 5: Run the full backend test suite and lint/format checks**

Run: `.venv/Scripts/python.exe -m pytest -m "not integration" -q && .venv/Scripts/python.exe -m ruff check src tests && .venv/Scripts/python.exe -m ruff format --check src tests`

Expected: all clean

- [ ] **Step 6: Commit**

```bash
git add src/conversion/html_to_pdf.py tests/unit/conversion/test_html_to_pdf.py
git commit -m "$(cat <<'EOF'
feat(conversion): add html_to_pdf converter

Renders HTML directly to PDF via xhtml2pdf, skipping the Markdown-
parsing stage markdown_to_pdf.py needs -- for content that's already
HTML (e.g. an executive report agent's output), not Markdown. Reuses
the shared SSRF-blocking link_callback, same guarantee as
markdown_to_pdf's existing conversion path.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Schema — `DownloadPdfNodeData` / `DownloadPdfNode`

**Files:**
- Modify: `src/engine/workflow.py` (add new classes after `EmailNode`, before `ArcadeNodeData`; add to the `WorkflowNode` union; add to `__all__`)
- Test: `tests/unit/engine/test_download_pdf_node.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/engine/test_download_pdf_node.py`:

```python
"""Tests for the DownloadPdfNode/DownloadPdfNodeData schema."""

from typing import Any

import pytest
from pydantic import ValidationError

from src.engine.workflow import DownloadPdfNode


def _download_pdf_node_json(**data_overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "label": "Download PDF",
        "inputFormat": "html",
        "content": "<html><body>Report</body></html>",
        "destinationPath": "/out",
        "filename": "report",
    }
    data.update(data_overrides)
    return {"id": "dp1", "type": "download-pdf", "position": {"x": 0, "y": 0}, "data": data}


def test_camelcase_fields_parse_with_aliases() -> None:
    node = DownloadPdfNode.model_validate(_download_pdf_node_json())
    assert node.data.input_format == "html"
    assert node.data.destination_path == "/out"
    assert node.data.filename == "report"


def test_markdown_input_format_accepted() -> None:
    node = DownloadPdfNode.model_validate(_download_pdf_node_json(inputFormat="markdown"))
    assert node.data.input_format == "markdown"


def test_input_format_is_required_no_default() -> None:
    payload = _download_pdf_node_json()
    del payload["data"]["inputFormat"]
    with pytest.raises(ValidationError):
        DownloadPdfNode.model_validate(payload)


def test_provider_defaults_to_local() -> None:
    node = DownloadPdfNode.model_validate(_download_pdf_node_json())
    assert node.data.provider == "local"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/engine/test_download_pdf_node.py -v`

Expected: FAIL — `ImportError: cannot import name 'DownloadPdfNode' from 'src.engine.workflow'`

- [ ] **Step 3: Add the schema classes**

In `src/engine/workflow.py`, add this block immediately after the `EmailNode` class (currently ending around line 577, right before `ArcadeNodeData`):

```python
# ─── download-pdf (Feature 2 foundation) ─────────────────────────────────


class DownloadPdfNodeData(BaseNodeData):
    input_format: Literal["html", "markdown"] = Field(alias="inputFormat")
    content: str | None = None
    # Plain str (not Literal["local"]): same reasoning as FileWriteNodeData.provider
    # above -- the executor must be able to *receive* an unrecognized provider
    # value and raise a runtime UnknownStorageProviderError from its own
    # registry lookup, not reject it one layer too early at parse time.
    provider: str = "local"
    destination_path: str | None = Field(default=None, alias="destinationPath")
    filename: str | None = None


class DownloadPdfNode(BaseModel):
    id: str
    type: Literal["download-pdf"]
    position: Position
    data: DownloadPdfNodeData
```

Add `DownloadPdfNode` to the `WorkflowNode` union — insert `| DownloadPdfNode` right after `| ConfluenceNode`:

```python
WorkflowNode = Annotated[
    StartNode
    | EndNode
    | NoteNode
    | FileTriggerNode
    | FileWriteNode
    | AgentNode
    | McpNode
    | IfElseNode
    | WhileNode
    | UserApprovalNode
    | TransformNode
    | DataTransformNode
    | SetStateNode
    | ExtractNode
    | HttpNode
    | GuardrailsNode
    | VectorDbNode
    | GammaAiNode
    | EmailNode
    | ArcadeNode
    | JoinChunksNode
    | JiraNode
    | ConfluenceNode
    | DownloadPdfNode,
    Field(discriminator="type"),
]
```

Add `"DownloadPdfNode"` and `"DownloadPdfNodeData"` to the `__all__` list, in alphabetical position matching the existing ordering.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/engine/test_download_pdf_node.py -v`

Expected: PASS

- [ ] **Step 5: Run the full backend test suite and lint/format checks**

Run: `.venv/Scripts/python.exe -m pytest -m "not integration" -q && .venv/Scripts/python.exe -m ruff check src tests && .venv/Scripts/python.exe -m ruff format --check src tests`

Expected: all clean

- [ ] **Step 6: Commit**

```bash
git add src/engine/workflow.py tests/unit/engine/test_download_pdf_node.py
git commit -m "$(cat <<'EOF'
feat(download-pdf-node): add DownloadPdfNodeData/DownloadPdfNode schema

inputFormat is required with no default (explicit html/markdown choice,
no auto-detection guessing). No executor yet - next task.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Executor

**Files:**
- Create: `src/executors/download_pdf.py`
- Test: `tests/unit/executors/test_download_pdf.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/executors/test_download_pdf.py`:

```python
"""Tests for DownloadPdfExecutor."""

from pathlib import Path
from typing import Any

import pytest


def _node(tmp_path: Path, **data_overrides: Any):
    from src.engine.workflow import DownloadPdfNode

    data: dict[str, Any] = {
        "label": "Download PDF",
        "inputFormat": "html",
        "content": "<html><body><h1>Hello {{name}}</h1></body></html>",
        "destinationPath": str(tmp_path),
        "filename": "report",
    }
    data.update(data_overrides)
    return DownloadPdfNode.model_validate(
        {"id": "dp1", "type": "download-pdf", "position": {"x": 0, "y": 0}, "data": data}
    )


async def test_writes_pdf_from_html(tmp_path: Path) -> None:
    from src.engine.state import initial_state
    from src.executors.download_pdf import DownloadPdfExecutor

    state = initial_state()
    state["variables"]["name"] = "Ada"
    node = _node(tmp_path)
    delta = await DownloadPdfExecutor(node).arun(state)

    written = tmp_path / "report.pdf"
    assert written.exists()
    assert written.read_bytes().startswith(b"%PDF-")
    assert delta["variables"]["lastOutput"] == str(written)
    assert delta["node_results"]["dp1"]["status"] == "completed"


async def test_writes_pdf_from_markdown(tmp_path: Path) -> None:
    from src.engine.state import initial_state
    from src.executors.download_pdf import DownloadPdfExecutor

    node = _node(tmp_path, inputFormat="markdown", content="# Title\n\nBody text.")
    await DownloadPdfExecutor(node).arun(initial_state())

    written = tmp_path / "report.pdf"
    assert written.exists()
    assert written.read_bytes().startswith(b"%PDF-")


async def test_filename_and_destination_support_substitution(tmp_path: Path) -> None:
    from src.engine.state import initial_state
    from src.executors.download_pdf import DownloadPdfExecutor

    state = initial_state()
    state["variables"]["project"] = "acme"
    node = _node(tmp_path, filename="{{project}}-report")
    await DownloadPdfExecutor(node).arun(state)
    assert (tmp_path / "acme-report.pdf").exists()


async def test_unknown_provider_raises(tmp_path: Path) -> None:
    from src.engine.state import initial_state
    from src.executors.download_pdf import DownloadPdfExecutor, UnknownStorageProviderError

    node = _node(tmp_path, provider="s3")
    with pytest.raises(UnknownStorageProviderError):
        await DownloadPdfExecutor(node).arun(initial_state())


async def test_rejects_path_traversal_in_filename(tmp_path: Path) -> None:
    from src.engine.state import initial_state
    from src.executors.download_pdf import DownloadPdfExecutor, InvalidFilenameError

    node = _node(tmp_path, filename="../../evil")
    with pytest.raises(InvalidFilenameError):
        await DownloadPdfExecutor(node).arun(initial_state())
    assert list(tmp_path.iterdir()) == []


async def test_rejects_empty_destination_path(tmp_path: Path) -> None:
    from src.engine.state import initial_state
    from src.executors.download_pdf import DownloadPdfExecutor, InvalidDestinationError

    node = _node(tmp_path, destinationPath="")
    with pytest.raises(InvalidDestinationError):
        await DownloadPdfExecutor(node).arun(initial_state())
    assert not (Path.cwd() / "report.pdf").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/executors/test_download_pdf.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'src.executors.download_pdf'`

- [ ] **Step 3: Implement the executor**

Create `src/executors/download_pdf.py`:

```python
"""DownloadPdfExecutor — the `download-pdf` node type.

Renders HTML or Markdown content to a PDF file and writes it via a
FileStorageProvider — the PDF-producing counterpart to `file-write`, but
as its own dedicated node type (more discoverable in the palette than a
buried file-write format option) since PDF rendering needs an explicit
inputFormat choice rather than sharing file-write's simple format
dropdown.

Destination/filename validation intentionally mirrors file_write.py's
identical guards as independent, local code rather than a cross-module
import — the logic is small (~10 lines) and this keeps the two node
types' modules decoupled. The SSRF-blocking PDF-rendering guarantee
(src/conversion/_pdf_security.py), which IS shared, is the one piece
where sharing matters: that's substantial, security-critical code where
a second copy could drift.
"""

from pathlib import Path
from typing import Any

from src.conversion.html_to_pdf import html_to_pdf
from src.conversion.markdown_to_pdf import markdown_to_pdf
from src.engine.state import WorkflowStateDict
from src.engine.workflow import DownloadPdfNode
from src.executors.base import register_executor
from src.storage_providers.base import FileStorageProvider
from src.storage_providers.local import LocalFilesystemProvider
from src.variable_substitution import substitute

_PROVIDERS: dict[str, type[FileStorageProvider]] = {
    "local": LocalFilesystemProvider,
}


class UnknownStorageProviderError(ValueError):
    """Raised when a download-pdf node names an unregistered provider."""


class InvalidDestinationError(ValueError):
    """Raised when a download-pdf node's substituted destinationPath is empty
    or blank. Path("") resolves to the process's current working directory,
    so silently accepting it would let the node write into whatever
    directory the server happened to be launched from — fail loudly instead.
    """


class InvalidFilenameError(ValueError):
    """Raised when a download-pdf node's substituted filename is not a bare
    filename — e.g. it contains a path separator, is a `..` traversal
    segment, or is empty.

    `filename` and `destinationPath` are both substituted from workflow
    state, which can carry prior node outputs (http/agent/extract/mcp
    results) originating from untrusted external data.
    """


def _validate_filename(node_id: str, filename: str) -> None:
    if not filename or not filename.strip():
        raise InvalidFilenameError(f"download-pdf node {node_id!r} resolved to an empty filename")
    if "/" in filename or "\\" in filename or ".." in filename:
        raise InvalidFilenameError(
            f"download-pdf node {node_id!r} filename {filename!r} must be a bare filename "
            "with no path separators or '..' segments — use destinationPath to control "
            "the output directory"
        )


@register_executor("download-pdf")
class DownloadPdfExecutor:
    def __init__(self, node: DownloadPdfNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        provider_cls = _PROVIDERS.get(self.node.data.provider)
        if provider_cls is None:
            raise UnknownStorageProviderError(
                f"download-pdf node {self.node.id!r} has unknown provider "
                f"{self.node.data.provider!r}; registered: {sorted(_PROVIDERS)}"
            )

        destination = substitute(self.node.data.destination_path or "", state)
        if not destination or not destination.strip():
            raise InvalidDestinationError(
                f"download-pdf node {self.node.id!r} resolved to an empty destinationPath; "
                "refusing to write into the process's current working directory"
            )

        filename = substitute(self.node.data.filename or "output", state)
        _validate_filename(self.node.id, filename)

        content = substitute(self.node.data.content or "", state)

        if self.node.data.input_format == "markdown":
            pdf_bytes = markdown_to_pdf(content)
        else:
            pdf_bytes = html_to_pdf(content)

        full_filename = f"{filename}.pdf"
        provider = provider_cls()
        await provider.write_file(destination, full_filename, pdf_bytes)

        written_path = str(Path(destination) / full_filename)
        return {
            "variables": {"lastOutput": written_path},
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "input": {
                        "filename": full_filename,
                        "inputFormat": self.node.data.input_format,
                    },
                    "output": written_path,
                }
            },
        }


__all__ = [
    "DownloadPdfExecutor",
    "InvalidDestinationError",
    "InvalidFilenameError",
    "UnknownStorageProviderError",
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/executors/test_download_pdf.py -v`

Expected: PASS

- [ ] **Step 5: Run the full backend test suite and lint/format checks**

Run: `.venv/Scripts/python.exe -m pytest -m "not integration" -q && .venv/Scripts/python.exe -m ruff check src tests && .venv/Scripts/python.exe -m ruff format --check src tests`

Expected: all clean

- [ ] **Step 6: Commit**

```bash
git add src/executors/download_pdf.py tests/unit/executors/test_download_pdf.py
git commit -m "$(cat <<'EOF'
feat(download-pdf-node): implement the executor

Dispatches on inputFormat: markdown delegates to the existing
markdown_to_pdf() unchanged, html uses the new html_to_pdf(). Same
destination/filename safety guarantees as file-write, same storage-
provider abstraction reused unchanged.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Register the executor in the graph builder

**Files:**
- Modify: `src/engine/graph_builder.py` (insert alphabetically before `email`, after `data_transform`)

- [ ] **Step 1: Add the side-effect import**

In `src/engine/graph_builder.py`, insert this block right after the `data_transform` import and before the `email` import — keeping the existing alphabetical ordering:

```python
from src.executors import (
    download_pdf as _download_pdf_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
```

(If `ruff format` reorders this differently when you run it, e.g. placing it between `confluence` and `data_transform` to match strict alphabetical order, that's fine — let the formatter's ordering win, same as happened when `confluence`'s import was added.)

- [ ] **Step 2: Verify**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/executors/test_download_pdf.py tests/unit/engine/ -v`

Expected: PASS (no behavior change from this task alone — this wires registration into the graph compiler's import chain)

- [ ] **Step 3: Run the full backend test suite and lint/format checks**

Run: `.venv/Scripts/python.exe -m pytest -m "not integration" -q && .venv/Scripts/python.exe -m ruff check src tests && .venv/Scripts/python.exe -m ruff format --check src tests`

Expected: all clean

- [ ] **Step 4: Commit**

```bash
git add src/engine/graph_builder.py
git commit -m "$(cat <<'EOF'
feat(download-pdf-node): register executor in the graph builder import chain

Without this, build_executor() would raise NotImplementedError for any
workflow containing a download-pdf node.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Designer panel

**Files:**
- Create: `frontend/components/composer/canvas/node-panels/download-pdf.tsx`
- Test: `frontend/components/composer/canvas/node-panels/download-pdf.test.tsx` (new)

- [ ] **Step 1: Write the failing test**

Create `frontend/components/composer/canvas/node-panels/download-pdf.test.tsx`:

```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import DownloadPdfPanel from "./download-pdf";

const CONTENT_PLACEHOLDER = "Reference an upstream node's output, e.g. {{narrative_agent}}";

describe("DownloadPdfPanel", () => {
  it("shows the input format, destination, and filename fields", () => {
    render(<DownloadPdfPanel data={{}} onChange={vi.fn()} currentNodeId="dp-1" />);
    expect(screen.getByLabelText("Input format")).toBeInTheDocument();
    expect(screen.getByLabelText("Destination path")).toBeInTheDocument();
    expect(screen.getByLabelText("Filename (no extension)")).toBeInTheDocument();
    // PromptField's <Label> has no htmlFor association with its textarea
    // (see prompt-field.tsx) -- getByLabelText won't find it, but its
    // placeholder text proves it's rendered.
    expect(screen.getByPlaceholderText(CONTENT_PLACEHOLDER)).toBeInTheDocument();
  });

  it("selecting input format calls onChange with inputFormat", () => {
    const onChange = vi.fn();
    render(<DownloadPdfPanel data={{}} onChange={onChange} currentNodeId="dp-1" />);
    fireEvent.change(screen.getByLabelText("Input format"), {
      target: { value: "markdown" },
    });
    expect(onChange).toHaveBeenCalledWith({ inputFormat: "markdown" });
  });

  it("editing destination path calls onChange with destinationPath", () => {
    const onChange = vi.fn();
    render(<DownloadPdfPanel data={{}} onChange={onChange} currentNodeId="dp-1" />);
    fireEvent.change(screen.getByLabelText("Destination path"), {
      target: { value: "/out" },
    });
    expect(onChange).toHaveBeenCalledWith({ destinationPath: "/out" });
  });

  it("editing filename calls onChange with filename", () => {
    const onChange = vi.fn();
    render(<DownloadPdfPanel data={{}} onChange={onChange} currentNodeId="dp-1" />);
    fireEvent.change(screen.getByLabelText("Filename (no extension)"), {
      target: { value: "weekly-report" },
    });
    expect(onChange).toHaveBeenCalledWith({ filename: "weekly-report" });
  });

  it("editing content calls onChange with content", () => {
    const onChange = vi.fn();
    render(<DownloadPdfPanel data={{}} onChange={onChange} currentNodeId="dp-1" />);
    fireEvent.change(screen.getByPlaceholderText(CONTENT_PLACEHOLDER), {
      target: { value: "<html>report</html>" },
    });
    expect(onChange).toHaveBeenCalledWith({ content: "<html>report</html>" });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `frontend/`): `npx vitest run components/composer/canvas/node-panels/download-pdf.test.tsx`

Expected: FAIL — `Cannot find module './download-pdf'`

- [ ] **Step 3: Create the panel**

Create `frontend/components/composer/canvas/node-panels/download-pdf.tsx`:

```tsx
"use client";

import type { Node as RFNode } from "reactflow";

import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { PromptField } from "../prompt-field";

const INPUT_FORMAT_OPTIONS = [
  { value: "html", label: "HTML" },
  { value: "markdown", label: "Markdown" },
];

export default function DownloadPdfPanel({
  data,
  onChange,
  allNodes,
  currentNodeId,
}: {
  data: Record<string, unknown>;
  onChange: (patch: Record<string, unknown>) => void;
  allNodes?: RFNode[];
  currentNodeId?: string;
}) {
  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="dlpdf-input-format">Input format</Label>
        <NativeSelect
          id="dlpdf-input-format"
          value={(data.inputFormat as string) ?? ""}
          onValueChange={(v) => onChange({ inputFormat: v })}
          options={INPUT_FORMAT_OPTIONS}
          placeholder="Select input format"
        />
      </div>

      <PromptField
        label="Content"
        value={(data.content as string) ?? ""}
        onChange={(next) => onChange({ content: next })}
        nodes={allNodes ?? []}
        currentNodeId={currentNodeId ?? ""}
        rows={8}
        placeholder="Reference an upstream node's output, e.g. {{narrative_agent}}"
      />

      <div className="rounded-md border bg-muted/20 p-3">
        <p className="mb-2 text-xs font-semibold text-muted-foreground">Destination</p>
        <div className="space-y-2">
          <div className="space-y-1">
            <Label htmlFor="dlpdf-provider" className="text-xs">
              Provider
            </Label>
            <NativeSelect
              id="dlpdf-provider"
              value={(data.provider as string) ?? "local"}
              onValueChange={(v) => onChange({ provider: v })}
              options={[{ value: "local", label: "Local filesystem" }]}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="dlpdf-dest" className="text-xs">
              Destination path
            </Label>
            <Input
              id="dlpdf-dest"
              value={(data.destinationPath as string) ?? ""}
              onChange={(e) => onChange({ destinationPath: e.target.value })}
              placeholder="/out or {{output_dir}}"
              className="font-mono text-xs"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="dlpdf-filename" className="text-xs">
              Filename (no extension)
            </Label>
            <Input
              id="dlpdf-filename"
              value={(data.filename as string) ?? ""}
              onChange={(e) => onChange({ filename: e.target.value })}
              placeholder="{{project_name}}-weekly-report"
              className="font-mono text-xs"
            />
            <p className="text-[10px] text-muted-foreground">
              The .pdf extension is added automatically. Must be a bare filename — no
              path separators or &quot;..&quot;.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run (from `frontend/`): `npx vitest run components/composer/canvas/node-panels/download-pdf.test.tsx`

Expected: PASS

- [ ] **Step 5: Run the full frontend test suite and type check**

Run (from `frontend/`): `npx vitest run && npx tsc --noEmit`

Expected: PASS, no regressions, no new type errors

- [ ] **Step 6: Commit**

```bash
git add frontend/components/composer/canvas/node-panels/download-pdf.tsx frontend/components/composer/canvas/node-panels/download-pdf.test.tsx
git commit -m "$(cat <<'EOF'
feat(download-pdf-panel): add the Designer property panel

inputFormat selector, Content (shared for both html and markdown input),
and the same provider/destinationPath/filename fields file-write uses.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Register the node type on the canvas

**Files:**
- Modify: `frontend/components/composer/canvas/property-panel.tsx`
- Modify: `frontend/components/composer/canvas/tools-palette.tsx`
- Modify: `frontend/components/composer/canvas/node-visuals.ts`
- Modify: `frontend/components/composer/canvas/workflow-canvas.tsx`

- [ ] **Step 1: Register the panel in `property-panel.tsx`**

Add the import, alphabetically near the other node-panel imports:

```tsx
import DownloadPdfPanel from "./node-panels/download-pdf";
```

Add to `PANEL_MAP`:

```tsx
  "download-pdf": DownloadPdfPanel,
```

Add to `TYPE_LABELS`:

```tsx
  "download-pdf": "Download PDF",
```

- [ ] **Step 2: Register in the palette — `tools-palette.tsx`**

Add to `COMPOSER_NODE_PALETTE`:

```tsx
  { nodeType: "download-pdf", label: "Download PDF" },
```

- [ ] **Step 3: Add a visual identity — `node-visuals.ts`**

First read the top of this file to check whether an icon fitting "download"/"PDF" is already imported (e.g. `FileDown`, `Download`, `FileText`) — if not, add `FileDown` to the `lucide-react` import list.

Add an entry to the `NODE_VISUALS` object:

```ts
  "download-pdf": {
    icon: FileDown,
    iconWrapClass: "bg-rose-100 text-rose-700",
    accent: "text-rose-700",
    label: "Download PDF",
  },
```

- [ ] **Step 4: Register in `workflow-canvas.tsx`**

Add to `COMPOSER_NODE_TYPES`:

```tsx
  "download-pdf": InnerNode,
```

- [ ] **Step 5: Run the full frontend test suite and type check**

Run (from `frontend/`): `npx vitest run && npx tsc --noEmit`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add frontend/components/composer/canvas/property-panel.tsx frontend/components/composer/canvas/tools-palette.tsx frontend/components/composer/canvas/node-visuals.ts frontend/components/composer/canvas/workflow-canvas.tsx
git commit -m "$(cat <<'EOF'
feat(download-pdf-node): register the node type on the canvas

Palette entry, property-panel dispatch, visual identity (FileDown icon,
rose accent), and ReactFlow node-type registration - the download-pdf
node is now fully usable from the Designer, not just via raw workflow
JSON.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-review notes

- **Spec coverage:** Component 2 of the spec (new `download-pdf` node, HTML or Markdown input, shared SSRF-hardened PDF rendering) is fully covered by Tasks 1-7.
- **Placeholder scan:** none found — every step has complete code.
- **Type consistency:** `input_format`/`destination_path` (Python, `DownloadPdfNodeData`) match `inputFormat`/`destinationPath` (the camelCase aliases the frontend panel actually sends) consistently across Tasks 3, 4, and 6. `DownloadPdfExecutor`/`InvalidDestinationError`/`InvalidFilenameError`/`UnknownStorageProviderError` names match between Task 4's implementation and its tests. The `PromptField` label-association gotcha (no `htmlFor`) was caught and corrected in Task 6's test before being written into the plan, rather than left as a plan bug a future implementer would hit.
