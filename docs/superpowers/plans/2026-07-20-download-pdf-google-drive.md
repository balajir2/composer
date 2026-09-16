# Download PDF Google Drive Write Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Google Drive as a write-capable destination for the `download-pdf` node, so its PDF output can land somewhere retrievable from the deployed Cloud Run instance (unlike the `local` provider's ephemeral container filesystem).

**Architecture:** Implement the currently-stubbed `GoogleDriveProvider.write_file()` using Drive's two-call `uploadType=media` upload contract (metadata-create-or-find, then binary content PATCH) rather than hand-rolled `multipart/related`. Wire `download-pdf`'s executor to resolve a Drive OAuth access token at execution time via the existing `get_current_db()` contextvar + `get_valid_drive_access_token()` — both already built and used by `file-trigger`'s polling path, reused here unchanged. Add `connectionId`/`driveFolderId` fields to `DownloadPdfNodeData`, mirroring `FileTriggerNodeData`'s exact naming. Reuse the existing `GoogleDriveConnect` Designer component (adding one new optional prop to hide its trigger-only Processed/Error folder pickers, since those don't apply to a write destination).

**Tech Stack:** Python 3.11, Pydantic v2, httpx, `pytest-httpx` (backend). Next.js/React, vitest (frontend).

**Spec:** `docs/superpowers/specs/2026-07-20-download-pdf-google-drive-design.md`.

---

## Scope note (platform vs. customer flow)

Generic platform capability — no customer-specific literals appear anywhere in this plan's code.

---

### Task 1: `GoogleDriveProvider.write_file()`

**Files:**
- Modify: `src/storage_providers/google_drive.py`
- Test: `tests/unit/storage_providers/test_google_drive.py` (replace the existing `test_write_file_raises_not_implemented` test at lines 210-215 with real coverage)

- [ ] **Step 1: Remove the obsolete "not implemented" test**

In `tests/unit/storage_providers/test_google_drive.py`, delete this test (lines 210-215):

```python
async def test_write_file_raises_not_implemented() -> None:
    from src.storage_providers.google_drive import GoogleDriveProvider

    provider = GoogleDriveProvider("at-1")
    with pytest.raises(NotImplementedError):
        await provider.write_file("dest", "name.md", b"content")
```

- [ ] **Step 2: Write the failing tests**

Add these tests to `tests/unit/storage_providers/test_google_drive.py` (in the same place the removed test was):

```python
async def test_write_file_creates_new_file_when_not_found(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    """No existing file matches the search query -> a metadata-only create
    (regular API) followed by a content upload (upload API) against the
    newly-created file id. No update/PATCH against a pre-existing id."""
    from src.storage_providers.google_drive import GoogleDriveProvider

    httpx_mock.add_response(
        url=re.compile(r"^https://www\.googleapis\.com/drive/v3/files\?"),
        method="GET",
        json={"files": []},
    )
    httpx_mock.add_response(
        url="https://www.googleapis.com/drive/v3/files",
        method="POST",
        json={"id": "new456"},
    )
    httpx_mock.add_response(
        url="https://www.googleapis.com/upload/drive/v3/files/new456?uploadType=media",
        method="PATCH",
        json={"id": "new456"},
    )

    provider = GoogleDriveProvider("at-1")
    await provider.write_file("folder123", "report.pdf", b"%PDF-fake-bytes")

    requests = httpx_mock.get_requests()
    assert len(requests) == 3

    import json as _json

    create_req = requests[1]
    assert create_req.method == "POST"
    assert _json.loads(create_req.content) == {"name": "report.pdf", "parents": ["folder123"]}

    upload_req = requests[2]
    assert upload_req.method == "PATCH"
    assert str(upload_req.url) == "https://www.googleapis.com/upload/drive/v3/files/new456?uploadType=media"
    assert upload_req.content == b"%PDF-fake-bytes"
    assert upload_req.headers.get("content-type") == "application/octet-stream"
    assert upload_req.headers.get("authorization") == "Bearer at-1"


async def test_write_file_updates_existing_file_in_place(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    """A file with the same name already exists in the destination folder ->
    its content is replaced (same file id), no new file is created, and no
    POST /files create call happens at all."""
    from src.storage_providers.google_drive import GoogleDriveProvider

    httpx_mock.add_response(
        url=re.compile(r"^https://www\.googleapis\.com/drive/v3/files\?"),
        method="GET",
        json={"files": [{"id": "existing123"}]},
    )
    httpx_mock.add_response(
        url="https://www.googleapis.com/upload/drive/v3/files/existing123?uploadType=media",
        method="PATCH",
        json={"id": "existing123"},
    )

    provider = GoogleDriveProvider("at-1")
    await provider.write_file("folder123", "report.pdf", b"%PDF-new-bytes")

    requests = httpx_mock.get_requests()
    assert len(requests) == 2
    assert requests[0].method == "GET"
    assert requests[1].method == "PATCH"
    assert str(requests[1].url) == "https://www.googleapis.com/upload/drive/v3/files/existing123?uploadType=media"
    assert requests[1].content == b"%PDF-new-bytes"


async def test_write_file_search_query_matches_name_and_parent_folder(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    from src.storage_providers.google_drive import GoogleDriveProvider

    httpx_mock.add_response(
        url=re.compile(r"^https://www\.googleapis\.com/drive/v3/files\?"),
        method="GET",
        json={"files": []},
    )
    httpx_mock.add_response(
        url="https://www.googleapis.com/drive/v3/files", method="POST", json={"id": "n1"}
    )
    httpx_mock.add_response(
        url="https://www.googleapis.com/upload/drive/v3/files/n1?uploadType=media",
        method="PATCH",
        json={"id": "n1"},
    )

    provider = GoogleDriveProvider("at-1")
    await provider.write_file("folder123", "weekly-report.pdf", b"content")

    search_req = httpx_mock.get_requests()[0]
    q = search_req.url.params["q"]
    assert q == "name = 'weekly-report.pdf' and 'folder123' in parents and trashed = false"


async def test_write_file_escapes_quotes_and_backslashes(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    """Mirrors list_new_files's existing escaping test -- filename and
    destination folder id are both attacker-reachable (substituted from
    workflow state), so both must be escaped the same way before being
    interpolated into the Drive query-language string."""
    from src.storage_providers.google_drive import GoogleDriveProvider

    httpx_mock.add_response(
        url=re.compile(r"^https://www\.googleapis\.com/drive/v3/files\?"),
        method="GET",
        json={"files": []},
    )
    httpx_mock.add_response(
        url="https://www.googleapis.com/drive/v3/files", method="POST", json={"id": "n1"}
    )
    httpx_mock.add_response(
        url="https://www.googleapis.com/upload/drive/v3/files/n1?uploadType=media",
        method="PATCH",
        json={"id": "n1"},
    )

    provider = GoogleDriveProvider("at-1")
    await provider.write_file("folder'with\\special", "report'name.pdf", b"content")

    search_req = httpx_mock.get_requests()[0]
    q = search_req.url.params["q"]
    assert "name = 'report\\'name.pdf'" in q
    assert "'folder\\'with\\\\special' in parents" in q


async def test_write_file_raises_on_search_http_error(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    from src.storage_providers.google_drive import GoogleDriveProvider, GoogleDriveProviderError

    httpx_mock.add_response(
        url=re.compile(r"^https://www\.googleapis\.com/drive/v3/files\?"),
        method="GET",
        status_code=403,
        text="permission denied",
    )

    provider = GoogleDriveProvider("at-1")
    with pytest.raises(GoogleDriveProviderError):
        await provider.write_file("folder123", "report.pdf", b"content")


async def test_write_file_raises_on_create_http_error(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    from src.storage_providers.google_drive import GoogleDriveProvider, GoogleDriveProviderError

    httpx_mock.add_response(
        url=re.compile(r"^https://www\.googleapis\.com/drive/v3/files\?"),
        method="GET",
        json={"files": []},
    )
    httpx_mock.add_response(
        url="https://www.googleapis.com/drive/v3/files",
        method="POST",
        status_code=500,
        text="server error",
    )

    provider = GoogleDriveProvider("at-1")
    with pytest.raises(GoogleDriveProviderError):
        await provider.write_file("folder123", "report.pdf", b"content")


async def test_write_file_raises_on_upload_http_error(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    from src.storage_providers.google_drive import GoogleDriveProvider, GoogleDriveProviderError

    httpx_mock.add_response(
        url=re.compile(r"^https://www\.googleapis\.com/drive/v3/files\?"),
        method="GET",
        json={"files": [{"id": "existing123"}]},
    )
    httpx_mock.add_response(
        url="https://www.googleapis.com/upload/drive/v3/files/existing123?uploadType=media",
        method="PATCH",
        status_code=403,
        text="permission denied",
    )

    provider = GoogleDriveProvider("at-1")
    with pytest.raises(GoogleDriveProviderError):
        await provider.write_file("folder123", "report.pdf", b"content")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/storage_providers/test_google_drive.py -v`

Expected: FAIL — `write_file()` still raises `NotImplementedError`, not the behavior these tests expect.

- [ ] **Step 4: Implement `write_file()`**

In `src/storage_providers/google_drive.py`, add a new module-level constant right after `DRIVE_API_BASE` (currently line 20):

```python
DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"
UPLOAD_API_BASE = "https://www.googleapis.com/upload/drive/v3"
```

Add a new private method, placed right after the existing `_request` method (currently ending at line 81, right before `list_new_files`):

```python
    async def _upload_request(
        self,
        method: str,
        path: str,
        *,
        error_prefix: str,
        timeout: httpx.Timeout,
        **kwargs: Any,
    ) -> httpx.Response:
        """Same choke point as _request(), but against Drive's separate
        upload-specific base URL -- uploadType=media/multipart endpoints
        live under /upload/drive/v3/, not /drive/v3/ like every other call
        in this provider. Kept as its own method rather than parameterizing
        _request()'s base_url, since every existing call site relies on
        _request() always meaning the metadata API."""
        try:
            async with httpx.AsyncClient(
                base_url=UPLOAD_API_BASE, headers=self._headers(), timeout=timeout
            ) as client:
                resp = await client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise GoogleDriveProviderError(f"{error_prefix} (request error): {exc}") from exc
        if resp.status_code >= 400:
            raise GoogleDriveProviderError(
                f"{error_prefix} (HTTP {resp.status_code}): {resp.text[:300]}"
            )
        return resp
```

Replace the existing `write_file` stub:

```python
    async def write_file(self, dest: str, filename: str, content: bytes) -> None:
        raise NotImplementedError(
            "GoogleDriveProvider is trigger-only; unrelated to the file-write node"
        )
```

with:

```python
    async def write_file(self, dest: str, filename: str, content: bytes) -> None:
        """`dest` is a Drive folder id (unlike LocalFilesystemProvider,
        where `dest` is a directory path). Reruns with the same
        folder+filename update the existing file in place rather than
        creating a duplicate -- Drive has no filesystem-style uniqueness
        constraint on names within a folder, so without this search step
        every rerun would silently pile up a new file. Escaping mirrors
        list_new_files' existing escaping for the same injection-safety
        reason: both filename and dest can be attacker-reachable via
        workflow-state substitution."""
        escaped_name = filename.replace("\\", "\\\\").replace("'", "\\'")
        escaped_dest = dest.replace("\\", "\\\\").replace("'", "\\'")
        query = f"name = '{escaped_name}' and '{escaped_dest}' in parents and trashed = false"
        search_resp = await self._request(
            "GET",
            "/files",
            error_prefix="Drive find-existing failed",
            timeout=httpx.Timeout(30.0, connect=5.0),
            params={"q": query, "fields": "files(id)"},
        )
        existing = search_resp.json().get("files", [])

        if existing:
            file_id = existing[0]["id"]
        else:
            create_resp = await self._request(
                "POST",
                "/files",
                error_prefix="Drive create failed",
                timeout=httpx.Timeout(30.0, connect=5.0),
                json={"name": filename, "parents": [dest]},
            )
            file_id = create_resp.json()["id"]

        await self._upload_request(
            "PATCH",
            f"/files/{file_id}",
            error_prefix="Drive content upload failed",
            timeout=httpx.Timeout(60.0, connect=5.0),
            params={"uploadType": "media"},
            headers={"Content-Type": "application/octet-stream"},
            content=content,
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/storage_providers/test_google_drive.py -v`

Expected: PASS (all tests, including the pre-existing `list_new_files`/`read_file`/`move_file` tests, unaffected by this addition)

- [ ] **Step 6: Run the full backend test suite and lint/format checks**

Run: `.venv/Scripts/python.exe -m pytest -m "not integration" -q && .venv/Scripts/python.exe -m ruff check src tests && .venv/Scripts/python.exe -m ruff format --check src tests`

Expected: all clean

- [ ] **Step 7: Commit**

```bash
git add src/storage_providers/google_drive.py tests/unit/storage_providers/test_google_drive.py
git commit -m "$(cat <<'EOF'
feat(google-drive-provider): implement write_file()

Uses Drive's two-call uploadType=media contract (metadata create-or-find,
then a binary content PATCH) rather than hand-rolled multipart/related,
which httpx has no native support for. Reruns with the same folder +
filename update the existing file in place instead of creating a
duplicate, since Drive allows multiple files with an identical name in
one folder.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `DownloadPdfNodeData` schema additions

**Files:**
- Modify: `src/engine/workflow.py` (the `DownloadPdfNodeData` class)
- Test: `tests/unit/engine/test_download_pdf_node.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/engine/test_download_pdf_node.py`:

```python
def test_google_drive_fields_parse_with_aliases() -> None:
    node = DownloadPdfNode.model_validate(
        _download_pdf_node_json(
            provider="google-drive",
            connectionId="conn-1",
            driveFolderId="folder-1",
        )
    )
    assert node.data.provider == "google-drive"
    assert node.data.connection_id == "conn-1"
    assert node.data.drive_folder_id == "folder-1"


def test_google_drive_fields_default_to_none() -> None:
    node = DownloadPdfNode.model_validate(_download_pdf_node_json())
    assert node.data.connection_id is None
    assert node.data.drive_folder_id is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/engine/test_download_pdf_node.py -v`

Expected: FAIL — `DownloadPdfNodeData` has no `connection_id`/`drive_folder_id` attributes yet (extra keys in the input dict are just ignored by default Pydantic behavior, so the JSON parses but the new assertions fail with `AttributeError`).

- [ ] **Step 3: Add the schema fields**

In `src/engine/workflow.py`, in `DownloadPdfNodeData` (currently):

```python
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
```

add two new fields, mirroring `FileTriggerNodeData`'s exact naming for the same concepts:

```python
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
    # google-drive only -- destination_path is ignored when provider is
    # "google-drive" (mirroring FileTriggerNodeData's sourcePath/destPath
    # being ignored when ITS provider is "google-drive").
    connection_id: str | None = Field(default=None, alias="connectionId")
    drive_folder_id: str | None = Field(default=None, alias="driveFolderId")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/engine/test_download_pdf_node.py -v`

Expected: PASS (all tests, including the pre-existing ones)

- [ ] **Step 5: Run the full backend test suite and lint/format checks**

Run: `.venv/Scripts/python.exe -m pytest -m "not integration" -q && .venv/Scripts/python.exe -m ruff check src tests && .venv/Scripts/python.exe -m ruff format --check src tests`

Expected: all clean

- [ ] **Step 6: Commit**

```bash
git add src/engine/workflow.py tests/unit/engine/test_download_pdf_node.py
git commit -m "$(cat <<'EOF'
feat(download-pdf-node): add connectionId/driveFolderId schema fields

Mirrors FileTriggerNodeData's exact field naming for the same Google
Drive connection concept. destinationPath stays present but is ignored
when provider is google-drive -- the executor wiring lands in the next
task.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Executor wiring

**Files:**
- Modify: `src/executors/download_pdf.py`
- Test: `tests/unit/executors/test_download_pdf.py`

- [ ] **Step 1: Write the failing tests**

At the top of `tests/unit/executors/test_download_pdf.py`, update the imports (currently `from pathlib import Path`, `from typing import Any`, `import pytest`) to also bring in what the new tests need:

```python
"""Tests for DownloadPdfExecutor."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
```

Add this autouse fixture right after the imports, before `_node`:

```python
@pytest.fixture(autouse=True)
def _reset_db_ctx() -> Iterator[None]:
    from src.engine.context import _current_db  # pyright: ignore[reportPrivateUsage]

    token = _current_db.set(None)
    yield
    _current_db.reset(token)
```

Append these tests to the end of the file:

```python
async def test_writes_pdf_to_google_drive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import src.engine.context as ctx_mod
    import src.executors.download_pdf as download_pdf_mod
    from src.engine.state import initial_state
    from src.executors.download_pdf import DownloadPdfExecutor

    fake_db = MagicMock()
    ctx_mod.set_current_db(fake_db)

    async def _fake_token(connection_id: str, db: Any) -> str:
        assert connection_id == "conn-1"
        assert db is fake_db
        return "fake-access-token"

    monkeypatch.setattr(download_pdf_mod, "get_valid_drive_access_token", _fake_token)

    write_calls: list[tuple[str, str, bytes]] = []

    class _FakeDriveProvider:
        def __init__(self, access_token: str) -> None:
            assert access_token == "fake-access-token"

        async def write_file(self, dest: str, filename: str, content: bytes) -> None:
            write_calls.append((dest, filename, content))

    monkeypatch.setattr(download_pdf_mod, "GoogleDriveProvider", _FakeDriveProvider)

    node = _node(
        tmp_path,
        provider="google-drive",
        connectionId="conn-1",
        driveFolderId="folder-1",
    )
    delta = await DownloadPdfExecutor(node).arun(initial_state())

    assert len(write_calls) == 1
    dest, filename, content = write_calls[0]
    assert dest == "folder-1"
    assert filename == "report.pdf"
    assert content.startswith(b"%PDF-")
    assert delta["variables"]["lastOutput"] == "drive://folder-1/report.pdf"
    assert delta["node_results"]["dp1"]["status"] == "completed"


async def test_google_drive_missing_connection_id_raises(tmp_path: Path) -> None:
    import src.engine.context as ctx_mod
    from src.engine.state import initial_state
    from src.executors.download_pdf import DownloadPdfExecutor, MissingDriveConfigError

    ctx_mod.set_current_db(MagicMock())
    node = _node(tmp_path, provider="google-drive", driveFolderId="folder-1")
    with pytest.raises(MissingDriveConfigError):
        await DownloadPdfExecutor(node).arun(initial_state())


async def test_google_drive_missing_folder_id_raises(tmp_path: Path) -> None:
    import src.engine.context as ctx_mod
    from src.engine.state import initial_state
    from src.executors.download_pdf import DownloadPdfExecutor, MissingDriveConfigError

    ctx_mod.set_current_db(MagicMock())
    node = _node(tmp_path, provider="google-drive", connectionId="conn-1")
    with pytest.raises(MissingDriveConfigError):
        await DownloadPdfExecutor(node).arun(initial_state())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/executors/test_download_pdf.py -v`

Expected: FAIL — `download_pdf.py` has no `get_valid_drive_access_token`/`GoogleDriveProvider`/`MissingDriveConfigError` names to patch/import yet, and passing `provider="google-drive"` currently falls into the `UnknownStorageProviderError` branch.

- [ ] **Step 3: Wire the executor**

Replace the full contents of `src/executors/download_pdf.py` with:

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

Google Drive support resolves an OAuth access token at execution time via
get_current_db() (a per-execution ContextVar set by LangGraphExecutor,
the same mechanism src/executors/mcp.py already uses for its own
server-side OAuth token retrieval) + get_valid_drive_access_token() (the
same helper file-trigger's polling endpoint already uses) — no new
plumbing, no token ever transits through the client.
"""

from pathlib import Path
from typing import Any

from src.conversion.html_to_pdf import html_to_pdf
from src.conversion.markdown_to_pdf import markdown_to_pdf
from src.engine.context import get_current_db
from src.engine.state import WorkflowStateDict
from src.engine.workflow import DownloadPdfNode
from src.executors.base import register_executor
from src.integrations.google_drive.oauth import get_valid_drive_access_token
from src.storage_providers.base import FileStorageProvider
from src.storage_providers.google_drive import GoogleDriveProvider
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


class MissingDriveConfigError(ValueError):
    """Raised when a download-pdf node selects the google-drive provider
    without both connectionId and driveFolderId set — there is no
    reasonable default destination to fall back to."""


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
        provider_name = self.node.data.provider

        if provider_name == "google-drive":
            connection_id = substitute(self.node.data.connection_id or "", state)
            folder_id = substitute(self.node.data.drive_folder_id or "", state)
            if not connection_id or not folder_id:
                raise MissingDriveConfigError(
                    f"download-pdf node {self.node.id!r}: google-drive provider requires "
                    "connectionId and driveFolderId to be set"
                )
            db = get_current_db()
            access_token = await get_valid_drive_access_token(connection_id, db)
            provider: FileStorageProvider = GoogleDriveProvider(access_token)
            destination = folder_id
        else:
            provider_cls = _PROVIDERS.get(provider_name)
            if provider_cls is None:
                raise UnknownStorageProviderError(
                    f"download-pdf node {self.node.id!r} has unknown provider "
                    f"{provider_name!r}; registered: {sorted([*_PROVIDERS, 'google-drive'])}"
                )
            destination = substitute(self.node.data.destination_path or "", state)
            if not destination or not destination.strip():
                raise InvalidDestinationError(
                    f"download-pdf node {self.node.id!r} resolved to an empty destinationPath; "
                    "refusing to write into the process's current working directory"
                )
            provider = provider_cls()

        filename = substitute(self.node.data.filename or "output", state)
        _validate_filename(self.node.id, filename)

        content = substitute(self.node.data.content or "", state)

        if self.node.data.input_format == "markdown":
            pdf_bytes = markdown_to_pdf(content)
        else:
            pdf_bytes = html_to_pdf(content)

        full_filename = f"{filename}.pdf"
        await provider.write_file(destination, full_filename, pdf_bytes)

        written_path = (
            f"drive://{destination}/{full_filename}"
            if provider_name == "google-drive"
            else str(Path(destination) / full_filename)
        )
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
    "MissingDriveConfigError",
    "UnknownStorageProviderError",
]
```

Note: this preserves the exact original validation order for every existing "local"/unknown-provider
code path (provider lookup → destination → filename → content/render → write) — only a new,
independent `google-drive` branch is added ahead of it, so none of the six pre-existing tests in
this file change behavior.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/executors/test_download_pdf.py -v`

Expected: PASS (all tests, including all six pre-existing ones)

- [ ] **Step 5: Run the full backend test suite and lint/format checks**

Run: `.venv/Scripts/python.exe -m pytest -m "not integration" -q && .venv/Scripts/python.exe -m ruff check src tests && .venv/Scripts/python.exe -m ruff format --check src tests`

Expected: all clean

- [ ] **Step 6: Commit**

```bash
git add src/executors/download_pdf.py tests/unit/executors/test_download_pdf.py
git commit -m "$(cat <<'EOF'
feat(download-pdf-node): wire google-drive provider into the executor

Resolves an OAuth access token at execution time via get_current_db() +
get_valid_drive_access_token() -- the same mechanism mcp.py and
file-trigger's polling endpoint already use, no new plumbing. A missing
connectionId/driveFolderId raises MissingDriveConfigError before any
network call. Existing local-provider validation order and behavior are
unchanged.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: `GoogleDriveConnect` — optional `showProcessedErrorFolders` prop

**Files:**
- Modify: `frontend/components/composer/canvas/node-panels/google-drive-connect.tsx`
- Test: `frontend/components/composer/canvas/node-panels/google-drive-connect.test.tsx`

**Why this task exists:** `GoogleDriveConnect` currently always renders "Processed folder (optional)" and "Error folder (optional)" picker sections once connected. Those are `file-trigger`-only concepts (claim-move destinations) that make no sense on a `download-pdf` write destination. This task adds a prop to hide them, defaulting to the current (unchanged) behavior so `file-trigger.tsx`'s existing usage — which doesn't pass this new prop — is unaffected.

- [ ] **Step 1: Write the failing test**

Append to `frontend/components/composer/canvas/node-panels/google-drive-connect.test.tsx`, inside the existing `describe("GoogleDriveConnect", ...)` block (add it as a new `it(...)` alongside the others, before the closing `});` of the describe block):

```tsx
  it("hides Processed/Error folder pickers when showProcessedErrorFolders is false", async () => {
    listCloudStorageConnections.mockResolvedValue([
      { id: "conn-1", provider: "google-drive", accountEmail: "user@gmail.com" },
    ]);
    render(
      <GoogleDriveConnect
        connectionId="conn-1"
        driveFolderId="watch-folder"
        driveProcessedFolderId={undefined}
        driveErrorFolderId={undefined}
        showProcessedErrorFolders={false}
        onChange={vi.fn()}
      />
    );
    expect(await screen.findByText(/Connected as user@gmail.com/)).toBeInTheDocument();
    expect(screen.queryByText(/Processed folder/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Error folder/)).not.toBeInTheDocument();
  });
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `frontend/`): `node_modules/.bin/vitest run components/composer/canvas/node-panels/google-drive-connect.test.tsx`

Expected: FAIL — TypeScript error (no `showProcessedErrorFolders` prop exists yet) and/or the assertion fails since the sections still render unconditionally.

- [ ] **Step 3: Add the prop**

In `frontend/components/composer/canvas/node-panels/google-drive-connect.tsx`, update the function signature (currently):

```tsx
export default function GoogleDriveConnect({
  connectionId,
  driveFolderId,
  driveProcessedFolderId,
  driveErrorFolderId,
  onChange,
}: {
  connectionId: string | undefined;
  driveFolderId: string | undefined;
  driveProcessedFolderId: string | undefined;
  driveErrorFolderId: string | undefined;
  onChange: (patch: Record<string, unknown>) => void;
}) {
```

to:

```tsx
export default function GoogleDriveConnect({
  connectionId,
  driveFolderId,
  driveProcessedFolderId,
  driveErrorFolderId,
  showProcessedErrorFolders = true,
  onChange,
}: {
  connectionId: string | undefined;
  driveFolderId: string | undefined;
  driveProcessedFolderId: string | undefined;
  driveErrorFolderId: string | undefined;
  /** file-trigger's claim-move destinations are meaningless on a write
   *  destination (e.g. download-pdf) -- defaults true so file-trigger's
   *  existing usage (which doesn't pass this prop) is unaffected. */
  showProcessedErrorFolders?: boolean;
  onChange: (patch: Record<string, unknown>) => void;
}) {
```

Then wrap the two folder-picker `<div>` blocks. Currently (inside the `connected` branch, after the "Connected as..." block):

```tsx
          <div className="space-y-1 border-t pt-2">
            <div className="text-xs text-muted-foreground">
              Processed folder (optional) — successfully-handled files are moved here
              {driveProcessedFolderId ? `: ${driveProcessedFolderId}` : ""}
            </div>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={pickerBusy !== null}
              onClick={() => handlePickFolder("processed")}
            >
              {pickerBusy === "processed"
                ? "Opening…"
                : driveProcessedFolderId
                  ? "Change folder"
                  : "Select folder"}
            </Button>
          </div>

          <div className="space-y-1">
            <div className="text-xs text-muted-foreground">
              Error folder (optional) — files that fail to process are moved here
              {driveErrorFolderId ? `: ${driveErrorFolderId}` : ""}
            </div>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={pickerBusy !== null}
              onClick={() => handlePickFolder("error")}
            >
              {pickerBusy === "error" ? "Opening…" : driveErrorFolderId ? "Change folder" : "Select folder"}
            </Button>
          </div>
```

becomes:

```tsx
          {showProcessedErrorFolders && (
            <div className="space-y-1 border-t pt-2">
              <div className="text-xs text-muted-foreground">
                Processed folder (optional) — successfully-handled files are moved here
                {driveProcessedFolderId ? `: ${driveProcessedFolderId}` : ""}
              </div>
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={pickerBusy !== null}
                onClick={() => handlePickFolder("processed")}
              >
                {pickerBusy === "processed"
                  ? "Opening…"
                  : driveProcessedFolderId
                    ? "Change folder"
                    : "Select folder"}
              </Button>
            </div>
          )}

          {showProcessedErrorFolders && (
            <div className="space-y-1">
              <div className="text-xs text-muted-foreground">
                Error folder (optional) — files that fail to process are moved here
                {driveErrorFolderId ? `: ${driveErrorFolderId}` : ""}
              </div>
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={pickerBusy !== null}
                onClick={() => handlePickFolder("error")}
              >
                {pickerBusy === "error" ? "Opening…" : driveErrorFolderId ? "Change folder" : "Select folder"}
              </Button>
            </div>
          )}
```

- [ ] **Step 4: Run test to verify it passes**

Run (from `frontend/`): `node_modules/.bin/vitest run components/composer/canvas/node-panels/google-drive-connect.test.tsx`

Expected: PASS (all tests, including every pre-existing one — none of them pass `showProcessedErrorFolders`, so they all default to `true` and keep their exact current behavior)

- [ ] **Step 5: Run the full frontend test suite and type check**

Run (from `frontend/`): `node_modules/.bin/vitest run && node_modules/.bin/tsc --noEmit`

Expected: PASS, no regressions, no new type errors

- [ ] **Step 6: Commit**

```bash
git add frontend/components/composer/canvas/node-panels/google-drive-connect.tsx frontend/components/composer/canvas/node-panels/google-drive-connect.test.tsx
git commit -m "$(cat <<'EOF'
feat(google-drive-connect): add showProcessedErrorFolders prop

file-trigger's claim-move destinations (Processed/Error folder) don't
apply to a write destination like download-pdf. Defaults to true so
file-trigger's existing usage is unaffected.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: `download-pdf` Designer panel

**Files:**
- Modify: `frontend/components/composer/canvas/node-panels/download-pdf.tsx`
- Modify: `frontend/components/composer/canvas/node-panels/download-pdf.test.tsx`

- [ ] **Step 1: Write the failing tests**

At the top of `frontend/components/composer/canvas/node-panels/download-pdf.test.tsx` (currently starting with `import { describe, it, expect, vi } from "vitest";`), add a mock for the child component right after the imports and before the `CONTENT_PLACEHOLDER` constant:

```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import DownloadPdfPanel from "./download-pdf";

vi.mock("./google-drive-connect", () => ({
  default: (props: Record<string, unknown>) => (
    <div data-testid="google-drive-connect" data-props={JSON.stringify(props)} />
  ),
}));

const CONTENT_PLACEHOLDER = "Reference an upstream node's output, e.g. {{narrative_agent}}";
```

Append these tests inside the existing `describe("DownloadPdfPanel", ...)` block, alongside the current ones:

```tsx
  it("shows the destination path field for the local provider by default", () => {
    render(<DownloadPdfPanel data={{}} onChange={vi.fn()} currentNodeId="dp-1" />);
    expect(screen.getByLabelText("Destination path")).toBeInTheDocument();
    expect(screen.queryByTestId("google-drive-connect")).not.toBeInTheDocument();
  });

  it("swaps to the Google Drive picker when provider is google-drive", () => {
    render(
      <DownloadPdfPanel
        data={{ provider: "google-drive", connectionId: "conn-1", driveFolderId: "folder-1" }}
        onChange={vi.fn()}
        currentNodeId="dp-1"
      />
    );
    expect(screen.queryByLabelText("Destination path")).not.toBeInTheDocument();
    const picker = screen.getByTestId("google-drive-connect");
    const props = JSON.parse(picker.getAttribute("data-props") ?? "{}");
    expect(props.connectionId).toBe("conn-1");
    expect(props.driveFolderId).toBe("folder-1");
    expect(props.showProcessedErrorFolders).toBe(false);
  });

  it("selecting google-drive in the provider dropdown calls onChange with provider", () => {
    const onChange = vi.fn();
    render(<DownloadPdfPanel data={{}} onChange={onChange} currentNodeId="dp-1" />);
    fireEvent.change(screen.getByLabelText("Provider"), {
      target: { value: "google-drive" },
    });
    expect(onChange).toHaveBeenCalledWith({ provider: "google-drive" });
  });
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `node_modules/.bin/vitest run components/composer/canvas/node-panels/download-pdf.test.tsx`

Expected: FAIL — the provider dropdown has no `google-drive` option yet, and `GoogleDriveConnect` is never rendered.

- [ ] **Step 3: Update the panel**

Replace the full contents of `frontend/components/composer/canvas/node-panels/download-pdf.tsx` with:

```tsx
"use client";

import type { Node as RFNode } from "reactflow";

import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { PromptField } from "../prompt-field";
import GoogleDriveConnect from "./google-drive-connect";

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
  const provider = (data.provider as string) ?? "local";

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
              value={provider}
              onValueChange={(v) => onChange({ provider: v })}
              options={[
                { value: "local", label: "Local filesystem" },
                { value: "google-drive", label: "Google Drive" },
              ]}
            />
          </div>
          {provider === "google-drive" ? (
            <GoogleDriveConnect
              connectionId={data.connectionId as string | undefined}
              driveFolderId={data.driveFolderId as string | undefined}
              driveProcessedFolderId={undefined}
              driveErrorFolderId={undefined}
              showProcessedErrorFolders={false}
              onChange={onChange}
            />
          ) : (
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
          )}
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

- [ ] **Step 4: Run tests to verify they pass**

Run (from `frontend/`): `node_modules/.bin/vitest run components/composer/canvas/node-panels/download-pdf.test.tsx`

Expected: PASS (all tests, including every pre-existing one)

- [ ] **Step 5: Run the full frontend test suite and type check**

Run (from `frontend/`): `node_modules/.bin/vitest run && node_modules/.bin/tsc --noEmit`

Expected: PASS, no regressions, no new type errors

- [ ] **Step 6: Commit**

```bash
git add frontend/components/composer/canvas/node-panels/download-pdf.tsx frontend/components/composer/canvas/node-panels/download-pdf.test.tsx
git commit -m "$(cat <<'EOF'
feat(download-pdf-panel): add Google Drive as a destination option

Reuses the existing GoogleDriveConnect picker (with
showProcessedErrorFolders={false}, since those are trigger-only
concepts) in place of the destinationPath field when provider is
google-drive -- same conditional structure file-trigger.tsx already
uses for its own provider swap.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-review notes

- **Spec coverage:** Component 1 (`GoogleDriveProvider.write_file()`) → Task 1. Component 2 (schema) → Task 2. Component 3 (executor wiring) → Task 3. Component 4 (Designer panel) → Tasks 4-5 (Task 4 is a necessary prerequisite the spec didn't call out by name — `GoogleDriveConnect`'s Processed/Error folder sections would otherwise render meaninglessly on a `download-pdf` panel — added here as a small, backward-compatible, in-scope refinement rather than a redesign).
- **Placeholder scan:** none found — every step has complete code; the spec's earlier `multipart/related` ellipsis was resolved to a concrete two-call `uploadType=media` implementation before this plan was written.
- **Type consistency:** `connection_id`/`drive_folder_id` (Python, `DownloadPdfNodeData`) match `connectionId`/`driveFolderId` (the camelCase aliases both the frontend panel and `FileTriggerNodeData` use) consistently across Tasks 2, 3, and 5. `GoogleDriveProvider`, `get_valid_drive_access_token`, `get_current_db`, and `MissingDriveConfigError` names match between Task 3's implementation and its tests. `showProcessedErrorFolders` matches between Task 4's component change and Task 5's panel usage.
