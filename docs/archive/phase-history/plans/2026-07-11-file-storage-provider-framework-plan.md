# File Storage Provider Framework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A pluggable `FileStorageProvider` interface (local filesystem now; S3/Google Drive/OneDrive as documented future providers) backing two new node types — `file-trigger` (visual-only, like `note`; the real watching happens in a new `composer watch` CLI) and `file-write` (a real graph node producing PDF/DOCX/MD output).

**Architecture:** New top-level package `src/storage_providers/` (parallel to `src/tools/` and `src/vectordb/`) defines the provider ABC and the first (`local`) implementation. `file-trigger` reuses the existing `note`-node "visual-only, skipped by graph_builder" precedent and the existing `POST /api/run/{slug}` external-invoke endpoint as its trigger mechanism — no execution-engine changes. `file-write` is a normal executor producing bytes via a small Markdown→DOCX/PDF conversion layer.

**Tech Stack:** Python (new deps: `markdown-it-py`, `xhtml2pdf`; `python-docx`/`pypdf` already present), FastAPI/Pydantic node-schema conventions, Next.js/React frontend node-panel + registry conventions, argparse CLI (matches `src/cli/main.py`).

---

### Task 1: Add new dependencies

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add the two new dependencies**

In `pyproject.toml`, add a new section after the existing `# Document parsing (for Start node file uploads)` block:

```toml
    # Document generation (file-write node — Markdown -> DOCX/PDF)
    "markdown-it-py>=3.0.0",
    "xhtml2pdf>=0.2.16",
```

- [ ] **Step 2: Install and verify**

Run: `uv sync --all-extras`
Run: `uv run python -c "import markdown_it; import xhtml2pdf; print('ok')"`
Expected: prints `ok` with no import errors.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build: add markdown-it-py + xhtml2pdf for file-write node conversion"
```

---

### Task 2: `FileStorageProvider` base + `LocalFilesystemProvider`

**Files:**
- Create: `src/storage_providers/__init__.py`
- Create: `src/storage_providers/base.py`
- Create: `src/storage_providers/local.py`
- Test: `tests/unit/storage_providers/__init__.py`
- Test: `tests/unit/storage_providers/test_local.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/storage_providers/__init__.py
```

```python
# tests/unit/storage_providers/test_local.py
"""Tests for LocalFilesystemProvider."""

import time
from pathlib import Path

import pytest


async def test_write_then_read_round_trips(tmp_path: Path) -> None:
    from src.storage_providers.local import LocalFilesystemProvider

    provider = LocalFilesystemProvider()
    dest = tmp_path / "out"
    await provider.write_file(str(dest), "report.md", b"# Hello")

    ref = (await provider.list_new_files(str(dest)))[0]
    assert ref.name == "report.md"
    content = await provider.read_file(ref)
    assert content == b"# Hello"


async def test_move_file_relocates_and_removes_original(tmp_path: Path) -> None:
    from src.storage_providers.local import LocalFilesystemProvider

    provider = LocalFilesystemProvider()
    source = tmp_path / "in"
    dest = tmp_path / "done"
    source.mkdir()
    (source / "a.txt").write_bytes(b"data")

    refs = await provider.list_new_files(str(source))
    assert len(refs) == 0  # not yet stable (first poll always sees nothing new)


async def test_list_new_files_requires_two_stable_polls(tmp_path: Path) -> None:
    """A file is only 'new' once its size/mtime is unchanged across two
    consecutive polls — guards against claiming a file mid-write."""
    from src.storage_providers.local import LocalFilesystemProvider

    provider = LocalFilesystemProvider()
    source = tmp_path / "in"
    source.mkdir()
    (source / "a.txt").write_bytes(b"data")

    first_poll = await provider.list_new_files(str(source))
    assert first_poll == []  # first sighting — not stable yet

    second_poll = await provider.list_new_files(str(source))
    assert len(second_poll) == 1
    assert second_poll[0].name == "a.txt"


async def test_list_new_files_ignores_actively_growing_file(tmp_path: Path) -> None:
    from src.storage_providers.local import LocalFilesystemProvider

    provider = LocalFilesystemProvider()
    source = tmp_path / "in"
    source.mkdir()
    target = source / "a.txt"
    target.write_bytes(b"data")

    await provider.list_new_files(str(source))  # first sighting
    target.write_bytes(b"data-more")  # still being written between polls
    second_poll = await provider.list_new_files(str(source))
    assert second_poll == []  # size changed — reset stability tracking, not yet claimed

    third_poll = await provider.list_new_files(str(source))
    assert len(third_poll) == 1  # stable across this pair of polls now


async def test_move_file_after_claim(tmp_path: Path) -> None:
    from src.storage_providers.local import LocalFilesystemProvider

    provider = LocalFilesystemProvider()
    source = tmp_path / "in"
    dest = tmp_path / "done"
    source.mkdir()
    (source / "a.txt").write_bytes(b"data")

    await provider.list_new_files(str(source))
    refs = await provider.list_new_files(str(source))
    ref = refs[0]

    await provider.move_file(ref, str(dest))

    assert not (source / "a.txt").exists()
    assert (dest / "a.txt").read_bytes() == b"data"


async def test_write_file_creates_destination_dir(tmp_path: Path) -> None:
    from src.storage_providers.local import LocalFilesystemProvider

    provider = LocalFilesystemProvider()
    dest = tmp_path / "nested" / "out"
    await provider.write_file(str(dest), "x.txt", b"hi")
    assert (dest / "x.txt").read_bytes() == b"hi"


async def test_health_check_ok_for_local() -> None:
    from src.storage_providers.local import LocalFilesystemProvider

    status = await LocalFilesystemProvider().health_check()
    assert status.ok is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/storage_providers/ -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.storage_providers'`

- [ ] **Step 3: Implement the base ABC**

```python
# src/storage_providers/__init__.py
"""File Storage Provider Framework.

Backs two node types: file-trigger (read side — list/read/move) and
file-write (write side — write). One shared interface serves both, so a
future S3/Google Drive/OneDrive implementation only needs to be written
once. See docs/archive/phase-history/specs/2026-07-11-file-storage-provider-framework-design.md.
"""
```

```python
# src/storage_providers/base.py
"""FileStorageProvider ABC — modeled on src/tools/base.py's ToolProvider."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, kw_only=True)
class FileRef:
    """Opaque handle a provider hands back — identifier semantics are
    provider-specific (local: absolute path; s3 future: object key)."""

    identifier: str
    name: str
    size_bytes: int
    modified_at: datetime


@dataclass(frozen=True, kw_only=True)
class HealthStatus:
    ok: bool
    message: str


class FileStorageProvider(ABC):
    name: str

    @abstractmethod
    async def list_new_files(self, source: str) -> list[FileRef]:
        """Return files ready to be claimed — implementations decide their
        own 'ready' criteria (e.g. local: stable across two polls)."""

    @abstractmethod
    async def read_file(self, ref: FileRef) -> bytes: ...

    @abstractmethod
    async def move_file(self, ref: FileRef, dest: str) -> None: ...

    @abstractmethod
    async def write_file(self, dest: str, filename: str, content: bytes) -> None: ...

    async def health_check(self) -> HealthStatus:
        return HealthStatus(ok=True, message="no health check defined")


__all__ = ["FileRef", "FileStorageProvider", "HealthStatus"]
```

- [ ] **Step 4: Implement `LocalFilesystemProvider`**

```python
# src/storage_providers/local.py
"""Local-filesystem FileStorageProvider — polling-based, partial-write safe."""

import shutil
from datetime import UTC, datetime
from pathlib import Path

from src.storage_providers.base import FileRef, FileStorageProvider, HealthStatus


class LocalFilesystemProvider(FileStorageProvider):
    name = "local"

    def __init__(self) -> None:
        # {absolute_path: (size_bytes, mtime)} seen on the previous poll of
        # each source directory. A file must appear identical across two
        # consecutive list_new_files() calls before it's returned — guards
        # against claiming a file that's still being copied/written.
        self._seen: dict[str, tuple[int, float]] = {}

    async def list_new_files(self, source: str) -> list[FileRef]:
        source_path = Path(source)
        if not source_path.is_dir():
            return []

        stable: list[FileRef] = []
        current_snapshot: dict[str, tuple[int, float]] = {}
        for entry in source_path.iterdir():
            if not entry.is_file():
                continue
            stat = entry.stat()
            key = str(entry.resolve())
            current_snapshot[key] = (stat.st_size, stat.st_mtime)
            previous = self._seen.get(key)
            if previous is not None and previous == current_snapshot[key]:
                stable.append(
                    FileRef(
                        identifier=key,
                        name=entry.name,
                        size_bytes=stat.st_size,
                        modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
                    )
                )

        self._seen = current_snapshot
        return stable

    async def read_file(self, ref: FileRef) -> bytes:
        return Path(ref.identifier).read_bytes()

    async def move_file(self, ref: FileRef, dest: str) -> None:
        dest_dir = Path(dest)
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(ref.identifier, str(dest_dir / ref.name))
        self._seen.pop(ref.identifier, None)

    async def write_file(self, dest: str, filename: str, content: bytes) -> None:
        dest_dir = Path(dest)
        dest_dir.mkdir(parents=True, exist_ok=True)
        (dest_dir / filename).write_bytes(content)

    async def health_check(self) -> HealthStatus:
        return HealthStatus(ok=True, message="local filesystem — always available")


__all__ = ["LocalFilesystemProvider"]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/storage_providers/ -v`
Expected: PASS (all 7)

- [ ] **Step 6: Commit**

```bash
git add src/storage_providers tests/unit/storage_providers
git commit -m "feat(storage): add FileStorageProvider framework + LocalFilesystemProvider"
```

---

### Task 3: `file-trigger` node type (visual-only, like `note`)

**Files:**
- Modify: `src/engine/workflow.py`
- Modify: `src/engine/graph_builder.py`
- Test: `tests/unit/engine/test_workflow.py` (locate via `grep -rl "NoteNodeData\|NoteNode\b" tests/unit/engine/`)
- Test: `tests/unit/engine/test_graph_builder.py` (locate via `grep -rl "node.type == .note.\|visual-only" tests/unit/engine/`)

- [ ] **Step 1: Read existing note-node tests to match conventions**

Run: `grep -rl "NoteNode\|node.type == .note." tests/unit/engine/`
Read the returned file(s).

- [ ] **Step 2: Write the failing tests**

Add to the workflow-schema test file:

```python
def test_file_trigger_node_parses_full_config() -> None:
    from src.engine.workflow import FileTriggerNode

    node = FileTriggerNode.model_validate(
        {
            "id": "ft1",
            "type": "file-trigger",
            "position": {"x": 0, "y": 0},
            "data": {
                "label": "File Trigger",
                "provider": "local",
                "sourcePath": "/watch/in",
                "destPath": "/watch/done",
                "errorPath": "/watch/error",
                "targetInputVariable": "requirements_doc",
                "pollIntervalSeconds": 15,
            },
        }
    )
    assert node.data.source_path == "/watch/in"
    assert node.data.dest_path == "/watch/done"
    assert node.data.error_path == "/watch/error"
    assert node.data.target_input_variable == "requirements_doc"
    assert node.data.poll_interval_seconds == 15


def test_file_trigger_node_defaults() -> None:
    from src.engine.workflow import FileTriggerNode

    node = FileTriggerNode.model_validate(
        {
            "id": "ft1",
            "type": "file-trigger",
            "position": {"x": 0, "y": 0},
            "data": {"label": "File Trigger"},
        }
    )
    assert node.data.provider == "local"
    assert node.data.poll_interval_seconds == 30
    assert node.data.source_path is None
```

Add to the graph_builder test file:

```python
def test_file_trigger_node_is_visual_only_like_note() -> None:
    """file-trigger is skipped at build time and allowed disconnected from
    start, exactly like note — the real work happens in the `composer watch`
    CLI, not the execution graph."""
    from src.engine.graph_builder import validate_workflow_shape
    from src.engine.workflow import Workflow

    wf = Workflow.model_validate(
        {
            "id": "w",
            "name": "t",
            "nodes": [
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                {"id": "e", "type": "end", "position": {"x": 1, "y": 0}, "data": {"label": "E"}},
                {
                    "id": "ft1",
                    "type": "file-trigger",
                    "position": {"x": -1, "y": 0},
                    "data": {"label": "File Trigger"},
                },
            ],
            "edges": [{"id": "e1", "source": "s", "target": "e"}],
        }
    )
    validate_workflow_shape(wf)  # must not raise despite ft1 being disconnected


async def test_build_graph_skips_file_trigger_node() -> None:
    from langgraph.checkpoint.memory import MemorySaver

    from src.engine.graph_builder import build_graph
    from src.engine.state import initial_state
    from src.engine.workflow import Workflow

    wf = Workflow.model_validate(
        {
            "id": "w",
            "name": "t",
            "nodes": [
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                {"id": "e", "type": "end", "position": {"x": 1, "y": 0}, "data": {"label": "E"}},
                {
                    "id": "ft1",
                    "type": "file-trigger",
                    "position": {"x": -1, "y": 0},
                    "data": {"label": "File Trigger"},
                },
            ],
            "edges": [{"id": "e1", "source": "s", "target": "e"}],
        }
    )
    compiled = build_graph(wf, MemorySaver())
    result = await compiled.ainvoke(
        initial_state(), config={"configurable": {"thread_id": "t1"}}
    )
    assert "ft1" not in (result.get("node_results") or {})
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/unit/engine/ -k file_trigger -v`
Expected: FAIL

- [ ] **Step 4: Add `FileTriggerNodeData`/`FileTriggerNode` to `workflow.py`**

Add near `NoteNodeData`/`NoteNode`:

```python
# ─── file-trigger (visual-only; executor is a no-op; graph_builder skips) ─


class FileTriggerNodeData(BaseNodeData):
    provider: Literal["local"] = "local"
    source_path: str | None = Field(default=None, alias="sourcePath")
    dest_path: str | None = Field(default=None, alias="destPath")
    error_path: str | None = Field(default=None, alias="errorPath")
    target_input_variable: str | None = Field(default=None, alias="targetInputVariable")
    poll_interval_seconds: int = Field(default=30, alias="pollIntervalSeconds")


class FileTriggerNode(BaseModel):
    id: str
    type: Literal["file-trigger"]
    position: Position
    data: FileTriggerNodeData
```

Add `FileTriggerNode` to the discriminated union (find where `NoteNode` is listed in the `Annotated[Union[...], Field(discriminator="type")]` and add `FileTriggerNode` alongside it — read the exact union definition first, since it's a single large `Union[...]` block; insert in the same alphabetical/logical position as the other node types). Add `"FileTriggerNode"`, `"FileTriggerNodeData"` to `__all__`.

- [ ] **Step 5: Update `graph_builder.py`'s four `"note"`-check sites**

Replace the four `if node.type == "note":` / `if source_node.type == "note":` / `if nodes_by_id[edge.target].type == "note":` checks with a shared constant. Add near the top of the file:

```python
_VISUAL_ONLY_TYPES = {"note", "file-trigger"}
```

Then change each of the four checks (in `_check_reachability`, and the three in `build_graph`) from `== "note"` to `in _VISUAL_ONLY_TYPES`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/unit/engine/ -v`
Expected: PASS, no regressions in existing `note`-related tests (they should still pass unchanged since `_VISUAL_ONLY_TYPES` is a superset behaving identically for `"note"`)

- [ ] **Step 7: Commit**

```bash
git add src/engine/workflow.py src/engine/graph_builder.py tests/unit/engine/
git commit -m "feat(engine): add file-trigger node type (visual-only, like note)"
```

---

### Task 4: `file-trigger` frontend wiring

**Files:**
- Create: `frontend/components/composer/canvas/node-panels/file-trigger.tsx`
- Modify: `frontend/components/composer/canvas/node-visuals.ts`
- Modify: `frontend/components/composer/canvas/tools-palette.tsx`
- Modify: `frontend/components/composer/canvas/property-panel.tsx`
- Modify: `frontend/components/composer/canvas/workflow-canvas.tsx`

- [ ] **Step 1: Add the palette entry**

In `tools-palette.tsx`'s `COMPOSER_NODE_PALETTE` array, add (after `"vector-db"`, before `"jira"`, or anywhere in the list — order doesn't matter functionally):

```ts
{ nodeType: "file-trigger", label: "File Trigger" },
```

- [ ] **Step 2: Add the node-visuals entry**

In `node-visuals.ts`, import `FolderInput` from `lucide-react` (add to the existing import block), then add to `NODE_VISUALS`:

```ts
"file-trigger": {
  icon: FolderInput,
  iconWrapClass: "bg-amber-100 text-amber-700",
  accent: "text-amber-700",
  label: "File Trigger",
},
```

- [ ] **Step 3: Register it as a standard (non-branching) node in the canvas**

In `workflow-canvas.tsx`'s `COMPOSER_NODE_TYPES`, add:

```ts
"file-trigger": InnerNode,
```

(This is precisely the registry the Jira-node review found missing for a prior node type — do not skip this step.)

- [ ] **Step 4: Create the node panel**

```tsx
// frontend/components/composer/canvas/node-panels/file-trigger.tsx
"use client";

import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";

export default function FileTriggerPanel({
  data,
  onChange,
}: {
  data: Record<string, unknown>;
  onChange: (patch: Record<string, unknown>) => void;
}) {
  return (
    <div className="space-y-4">
      <div className="rounded-md border bg-muted/20 p-3">
        <p className="mb-2 text-xs font-semibold text-muted-foreground">
          Watch configuration
        </p>
        <p className="mb-3 text-[10px] text-muted-foreground">
          This node is visual-only — it does not run as part of the
          workflow. It configures the <code>composer watch</code> CLI,
          which polls the source folder and triggers this workflow (via the
          production external-invoke API) when a new file is claimed.
          Publish this workflow (Production toggle) before running the
          watcher.
        </p>
        <div className="space-y-2">
          <div className="space-y-1">
            <Label htmlFor="ft-provider" className="text-xs">
              Provider
            </Label>
            <NativeSelect
              id="ft-provider"
              value={(data.provider as string) ?? "local"}
              onValueChange={(v) => onChange({ provider: v })}
              options={[{ value: "local", label: "Local filesystem" }]}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="ft-source" className="text-xs">
              Source path
            </Label>
            <Input
              id="ft-source"
              value={(data.sourcePath as string) ?? ""}
              onChange={(e) => onChange({ sourcePath: e.target.value })}
              placeholder="/watch/in"
              className="font-mono text-xs"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="ft-dest" className="text-xs">
              Destination path (on successful claim)
            </Label>
            <Input
              id="ft-dest"
              value={(data.destPath as string) ?? ""}
              onChange={(e) => onChange({ destPath: e.target.value })}
              placeholder="/watch/done"
              className="font-mono text-xs"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="ft-error" className="text-xs">
              Error path (on extraction/trigger failure)
            </Label>
            <Input
              id="ft-error"
              value={(data.errorPath as string) ?? ""}
              onChange={(e) => onChange({ errorPath: e.target.value })}
              placeholder="/watch/error"
              className="font-mono text-xs"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="ft-target-var" className="text-xs">
              Target Start input variable
            </Label>
            <Input
              id="ft-target-var"
              value={(data.targetInputVariable as string) ?? ""}
              onChange={(e) => onChange({ targetInputVariable: e.target.value })}
              placeholder="requirements_doc"
              className="font-mono text-xs"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="ft-poll" className="text-xs">
              Poll interval (seconds)
            </Label>
            <Input
              id="ft-poll"
              type="number"
              min={1}
              value={
                typeof data.pollIntervalSeconds === "number"
                  ? String(data.pollIntervalSeconds)
                  : "30"
              }
              onChange={(e) => {
                const n = parseInt(e.target.value, 10);
                onChange({ pollIntervalSeconds: Number.isFinite(n) ? n : 30 });
              }}
              className="font-mono text-xs"
            />
          </div>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Register the panel**

In `property-panel.tsx`, add the import:

```tsx
import FileTriggerPanel from "./node-panels/file-trigger";
```

Add to `PANEL_MAP`:

```ts
"file-trigger": FileTriggerPanel,
```

Add to whatever display-label map sits alongside `note: "Note"` (the `PANEL_MAP`-adjacent labels object seen at line ~83 in the file read earlier):

```ts
"file-trigger": "File Trigger",
```

- [ ] **Step 6: Verify**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors

- [ ] **Step 7: Commit**

```bash
git add frontend/components/composer/canvas
git commit -m "feat(frontend): file-trigger node — palette, visuals, panel, registries"
```

---

### Task 5: `file-write` node type + executor (Markdown format only)

**Files:**
- Modify: `src/engine/workflow.py`
- Create: `src/executors/file_write.py`
- Test: `tests/unit/engine/test_workflow.py`
- Test: `tests/unit/executors/test_file_write.py`

- [ ] **Step 1: Write the failing schema test**

```python
def test_file_write_node_parses_full_config() -> None:
    from src.engine.workflow import FileWriteNode

    node = FileWriteNode.model_validate(
        {
            "id": "fw1",
            "type": "file-write",
            "position": {"x": 0, "y": 0},
            "data": {
                "label": "File Write",
                "provider": "local",
                "destinationPath": "/out",
                "filename": "report",
                "format": "docx",
                "content": "# Hi",
            },
        }
    )
    assert node.data.destination_path == "/out"
    assert node.data.filename == "report"
    assert node.data.format == "docx"
    assert node.data.content == "# Hi"


def test_file_write_node_defaults_to_md() -> None:
    from src.engine.workflow import FileWriteNode

    node = FileWriteNode.model_validate(
        {
            "id": "fw1",
            "type": "file-write",
            "position": {"x": 0, "y": 0},
            "data": {"label": "File Write"},
        }
    )
    assert node.data.format == "md"
    assert node.data.provider == "local"
```

- [ ] **Step 2: Write the failing executor tests**

```python
# tests/unit/executors/test_file_write.py
"""Tests for FileWriteExecutor."""

from pathlib import Path

import pytest


def _node(tmp_path: Path, **data_overrides: object):
    from src.engine.workflow import FileWriteNode

    data = {
        "label": "File Write",
        "destinationPath": str(tmp_path),
        "filename": "report",
        "format": "md",
        "content": "# Hello {{name}}",
    }
    data.update(data_overrides)
    return FileWriteNode.model_validate(
        {"id": "fw1", "type": "file-write", "position": {"x": 0, "y": 0}, "data": data}
    )


async def test_writes_markdown_file_with_substitution(tmp_path: Path) -> None:
    from src.engine.state import initial_state
    from src.executors.file_write import FileWriteExecutor

    state = initial_state()
    state["variables"]["name"] = "Ada"
    node = _node(tmp_path)
    delta = await FileWriteExecutor(node).arun(state)

    written = tmp_path / "report.md"
    assert written.read_text(encoding="utf-8") == "# Hello Ada"
    assert delta["variables"]["lastOutput"] == str(written)
    assert delta["node_results"]["fw1"]["status"] == "completed"


async def test_filename_and_destination_support_substitution(tmp_path: Path) -> None:
    from src.engine.state import initial_state
    from src.executors.file_write import FileWriteExecutor

    state = initial_state()
    state["variables"]["project"] = "acme"
    node = _node(tmp_path, filename="{{project}}-report")
    await FileWriteExecutor(node).arun(state)
    assert (tmp_path / "acme-report.md").exists()


async def test_unknown_provider_raises(tmp_path: Path) -> None:
    from src.engine.state import initial_state
    from src.executors.file_write import FileWriteExecutor, UnknownStorageProviderError

    node = _node(tmp_path, provider="s3")
    with pytest.raises(UnknownStorageProviderError):
        await FileWriteExecutor(node).arun(initial_state())
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/unit/engine/ tests/unit/executors/test_file_write.py -k "file_write or writes_markdown or filename_and_destination or unknown_provider" -v`
Expected: FAIL

- [ ] **Step 4: Add `FileWriteNodeData`/`FileWriteNode` to `workflow.py`**

```python
# ─── file-write (writes generated content to a file — PDF/DOCX/MD) ───────


class FileWriteNodeData(BaseNodeData):
    provider: Literal["local"] = "local"
    destination_path: str | None = Field(default=None, alias="destinationPath")
    filename: str | None = None
    format: Literal["md", "docx", "pdf"] = "md"
    content: str | None = None


class FileWriteNode(BaseModel):
    id: str
    type: Literal["file-write"]
    position: Position
    data: FileWriteNodeData
```

Add `FileWriteNode` to the discriminated union alongside `FileTriggerNode` (Task 3). Add `"FileWriteNode"`, `"FileWriteNodeData"` to `__all__`.

- [ ] **Step 5: Implement `FileWriteExecutor` (Markdown-only for now; docx/pdf added in Tasks 6–7)**

```python
# src/executors/file_write.py
"""FileWriteExecutor — the `file-write` node type.

Writes substituted content to a file via a FileStorageProvider, converting
Markdown -> DOCX/PDF as needed. See
docs/archive/phase-history/specs/2026-07-11-file-storage-provider-framework-design.md §C.
"""

from typing import Any

from src.engine.state import WorkflowStateDict
from src.engine.workflow import FileWriteNode
from src.executors.base import register_executor
from src.storage_providers.base import FileStorageProvider
from src.storage_providers.local import LocalFilesystemProvider
from src.variable_substitution import substitute

_PROVIDERS: dict[str, type[FileStorageProvider]] = {
    "local": LocalFilesystemProvider,
}

_EXTENSIONS = {"md": "md", "docx": "docx", "pdf": "pdf"}


class UnknownStorageProviderError(ValueError):
    """Raised when a file-write/file-trigger node names an unregistered provider."""


@register_executor("file-write")
class FileWriteExecutor:
    def __init__(self, node: FileWriteNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        provider_cls = _PROVIDERS.get(self.node.data.provider)
        if provider_cls is None:
            raise UnknownStorageProviderError(
                f"file-write node {self.node.id!r} has unknown provider "
                f"{self.node.data.provider!r}; registered: {sorted(_PROVIDERS)}"
            )

        destination = substitute(self.node.data.destination_path or "", state)
        filename = substitute(self.node.data.filename or "output", state)
        content = substitute(self.node.data.content or "", state)
        fmt = self.node.data.format

        converted = _convert(content, fmt)
        ext = _EXTENSIONS[fmt]
        full_filename = f"{filename}.{ext}"

        provider = provider_cls()
        await provider.write_file(destination, full_filename, converted)

        written_path = f"{destination.rstrip('/')}/{full_filename}"
        return {
            "variables": {"lastOutput": written_path},
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "input": {"filename": full_filename, "format": fmt},
                    "output": written_path,
                }
            },
        }


def _convert(content: str, fmt: str) -> bytes:
    if fmt == "md":
        return content.encode("utf-8")
    if fmt == "docx":
        from src.conversion.markdown_to_docx import markdown_to_docx

        return markdown_to_docx(content)
    if fmt == "pdf":
        from src.conversion.markdown_to_pdf import markdown_to_pdf

        return markdown_to_pdf(content)
    raise UnknownStorageProviderError(f"unknown format {fmt!r}")  # unreachable given Literal typing


__all__ = ["FileWriteExecutor", "UnknownStorageProviderError"]
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/unit/engine/ tests/unit/executors/test_file_write.py -v`
Expected: PASS (the `md` format tests; `docx`/`pdf` paths are unreachable from these tests since Pydantic's `Literal["md", "docx", "pdf"]` on `FileWriteNodeData.format` prevents constructing a node with `provider="s3"` from raising at the wrong layer — the `unknown_provider` test uses `provider`, not `format`, so it exercises `_PROVIDERS.get(...)` returning `None`, not `_convert`)

- [ ] **Step 7: Register the executor + graph_builder wiring**

`file-write` is a **normal** node (unlike `file-trigger`) — it must NOT be added to `_VISUAL_ONLY_TYPES`. Since `@register_executor("file-write")` (Step 5) already wires it into `src/executors/base.py`'s registry, `build_executor()` picks it up automatically — no additional `graph_builder.py` change needed for this node type. Verify with a quick smoke test:

Run: `uv run python -c "
from src.executors.base import build_executor
from src.engine.workflow import FileWriteNode
n = FileWriteNode.model_validate({'id':'x','type':'file-write','position':{'x':0,'y':0},'data':{'label':'FW'}})
print(build_executor(n))
"`
Expected: prints `<src.executors.file_write.FileWriteExecutor object at ...>` with no error.

- [ ] **Step 8: Commit**

```bash
git add src/engine/workflow.py src/executors/file_write.py tests/unit/engine/ tests/unit/executors/test_file_write.py
git commit -m "feat(executors): add file-write node type (Markdown format)"
```

---

### Task 6: Markdown → DOCX conversion

**Files:**
- Create: `src/conversion/__init__.py`
- Create: `src/conversion/markdown_to_docx.py`
- Test: `tests/unit/conversion/__init__.py`
- Test: `tests/unit/conversion/test_markdown_to_docx.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/conversion/__init__.py
```

```python
# tests/unit/conversion/test_markdown_to_docx.py
"""Tests for the Markdown -> DOCX renderer."""

import io

from docx import Document


def _load(content: bytes) -> Document:
    return Document(io.BytesIO(content))


def test_heading_becomes_docx_heading() -> None:
    from src.conversion.markdown_to_docx import markdown_to_docx

    doc = _load(markdown_to_docx("# Title\n\nSome text."))
    styles = [p.style.name for p in doc.paragraphs if p.text]
    assert any(s.startswith("Heading") for s in styles)
    texts = [p.text for p in doc.paragraphs]
    assert "Title" in texts
    assert "Some text." in texts


def test_bullet_list_becomes_list_paragraphs() -> None:
    from src.conversion.markdown_to_docx import markdown_to_docx

    doc = _load(markdown_to_docx("- one\n- two\n- three"))
    texts = [p.text for p in doc.paragraphs if p.text]
    assert texts == ["one", "two", "three"]


def test_table_becomes_docx_table() -> None:
    from src.conversion.markdown_to_docx import markdown_to_docx

    md = "| A | B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |"
    doc = _load(markdown_to_docx(md))
    assert len(doc.tables) == 1
    table = doc.tables[0]
    assert [c.text for c in table.rows[0].cells] == ["A", "B"]
    assert [c.text for c in table.rows[1].cells] == ["1", "2"]
    assert [c.text for c in table.rows[2].cells] == ["3", "4"]


def test_bold_and_italic_preserved_as_text() -> None:
    """Fidelity target: run-level bold/italic on simple inline spans, not a
    full round-trip of every possible markdown construct."""
    from src.conversion.markdown_to_docx import markdown_to_docx

    doc = _load(markdown_to_docx("**bold** and *italic*"))
    all_text = " ".join(p.text for p in doc.paragraphs)
    assert "bold" in all_text
    assert "italic" in all_text


def test_empty_content_produces_valid_empty_document() -> None:
    from src.conversion.markdown_to_docx import markdown_to_docx

    doc = _load(markdown_to_docx(""))
    assert doc is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/conversion/test_markdown_to_docx.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement the renderer**

```python
# src/conversion/__init__.py
```

```python
# src/conversion/markdown_to_docx.py
"""Markdown -> DOCX renderer for the file-write node.

Purpose-built for the structured, table-heavy document shape this node
targets (BRDs and similar) — not a general-purpose, full-fidelity Markdown
converter. Walks markdown-it-py's token stream and maps the constructs that
matter for that shape (headings, paragraphs, bullet/numbered lists, tables,
bold/italic) onto python-docx calls. See
docs/archive/phase-history/specs/2026-07-11-file-storage-provider-framework-design.md §C
for the scope/fidelity trade-off this makes against a pandoc-based approach.
"""

import io

from docx import Document
from docx.document import Document as DocumentType
from markdown_it import MarkdownIt
from markdown_it.token import Token

_md = MarkdownIt("commonmark").enable("table")


def _add_inline_runs(paragraph: object, children: list[Token]) -> None:
    """Render inline tokens (text/strong/em) onto a docx paragraph as runs."""
    bold = False
    italic = False
    for child in children:
        if child.type == "strong_open":
            bold = True
        elif child.type == "strong_close":
            bold = False
        elif child.type == "em_open":
            italic = True
        elif child.type == "em_close":
            italic = False
        elif child.type == "text":
            run = paragraph.add_run(child.content)  # type: ignore[attr-defined]
            run.bold = bold
            run.italic = italic
        elif child.type == "softbreak" or child.type == "hardbreak":
            paragraph.add_run(" ")  # type: ignore[attr-defined]


def markdown_to_docx(content: str) -> bytes:
    tokens = _md.parse(content)
    doc: DocumentType = Document()

    i = 0
    list_stack: list[str] = []  # tracks "bullet" | "ordered" for nesting depth
    while i < len(tokens):
        tok = tokens[i]

        if tok.type == "heading_open":
            level = int(tok.tag[1])  # "h1" -> 1
            inline = tokens[i + 1]
            para = doc.add_heading(level=min(level, 9))
            _add_inline_runs(para, inline.children or [])
            i += 3  # heading_open, inline, heading_close
            continue

        if tok.type == "paragraph_open":
            # Inside a list item, markdown-it-py still wraps the item's text
            # in paragraph_open/inline/paragraph_close (just marked
            # `hidden=True` for HTML rendering) — it does NOT emit a bare
            # `inline` token directly under list_item_open. Verified against
            # the installed markdown-it-py directly; do not "simplify" this
            # back to a standalone `inline`-token branch, it won't fire.
            inline = tokens[i + 1]
            if list_stack:
                style = "List Bullet" if list_stack[-1] == "bullet" else "List Number"
                para = doc.add_paragraph(style=style)
            else:
                para = doc.add_paragraph()
            _add_inline_runs(para, inline.children or [])
            i += 3
            continue

        if tok.type in ("bullet_list_open", "ordered_list_open"):
            list_stack.append("bullet" if tok.type == "bullet_list_open" else "ordered")
            i += 1
            continue
        if tok.type in ("bullet_list_close", "ordered_list_close"):
            list_stack.pop()
            i += 1
            continue

        if tok.type == "list_item_open":
            i += 1
            continue
        if tok.type == "list_item_close":
            i += 1
            continue

        if tok.type == "table_open":
            rows: list[list[str]] = []
            j = i + 1
            while tokens[j].type != "table_close":
                if tokens[j].type == "tr_open":
                    row: list[str] = []
                    k = j + 1
                    while tokens[k].type != "tr_close":
                        if tokens[k].type == "inline":
                            row.append(tokens[k].content)
                        k += 1
                    rows.append(row)
                    j = k
                j += 1
            if rows:
                table = doc.add_table(rows=len(rows), cols=len(rows[0]))
                table.style = "Table Grid"
                for r_idx, row in enumerate(rows):
                    for c_idx, cell_text in enumerate(row):
                        table.rows[r_idx].cells[c_idx].text = cell_text
            i = j + 1
            continue

        i += 1

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


__all__ = ["markdown_to_docx"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/conversion/test_markdown_to_docx.py -v`
Expected: PASS. If the table or list token-walk doesn't match markdown-it-py's exact emitted token sequence for a given case, adjust the walk against the real output — run `uv run python -c "from markdown_it import MarkdownIt; [print(t) for t in MarkdownIt('commonmark').enable('table').parse('| A | B |\n|---|---|\n| 1 | 2 |')]"` to inspect the exact token stream if a test fails unexpectedly.

- [ ] **Step 5: Wire `format="docx"` into `FileWriteExecutor`'s tests**

Add to `tests/unit/executors/test_file_write.py`:

```python
async def test_writes_docx_file(tmp_path: Path) -> None:
    from src.engine.state import initial_state
    from src.executors.file_write import FileWriteExecutor

    node = _node(tmp_path, format="docx", content="# Title\n\nBody text.")
    await FileWriteExecutor(node).arun(initial_state())

    written = tmp_path / "report.docx"
    assert written.exists()
    from docx import Document

    doc = Document(str(written))
    assert any(p.text == "Title" for p in doc.paragraphs)
```

Run: `uv run pytest tests/unit/executors/test_file_write.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/conversion/__init__.py src/conversion/markdown_to_docx.py tests/unit/conversion tests/unit/executors/test_file_write.py
git commit -m "feat(conversion): add Markdown to DOCX renderer, wire into file-write"
```

---

### Task 7: Markdown → PDF conversion

**Files:**
- Create: `src/conversion/markdown_to_pdf.py`
- Test: `tests/unit/conversion/test_markdown_to_pdf.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/conversion/test_markdown_to_pdf.py
"""Tests for the Markdown -> PDF renderer.

xhtml2pdf output isn't practical to assert on structurally the way python-
docx's object model is — these tests check for a well-formed PDF (correct
magic bytes, non-trivial size) rather than parsing rendered content.
"""


def test_produces_valid_pdf_bytes() -> None:
    from src.conversion.markdown_to_pdf import markdown_to_pdf

    result = markdown_to_pdf("# Title\n\nSome body text.\n\n- one\n- two")
    assert result.startswith(b"%PDF-")
    assert len(result) > 100


def test_empty_content_still_produces_valid_pdf() -> None:
    from src.conversion.markdown_to_pdf import markdown_to_pdf

    result = markdown_to_pdf("")
    assert result.startswith(b"%PDF-")


def test_table_content_does_not_raise() -> None:
    from src.conversion.markdown_to_pdf import markdown_to_pdf

    md = "| A | B |\n|---|---|\n| 1 | 2 |"
    result = markdown_to_pdf(md)
    assert result.startswith(b"%PDF-")


def test_conversion_failure_raises_clear_error(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from src.conversion.markdown_to_pdf import MarkdownToPdfError, markdown_to_pdf
    import src.conversion.markdown_to_pdf as mod

    def _boom(*a: object, **kw: object) -> int:
        return 1  # xhtml2pdf's pisaDocument returns a status object with .err; simulate failure

    class _FakeStatus:
        err = 1

    monkeypatch.setattr(mod, "pisa", type("P", (), {"CreatePDF": staticmethod(lambda *a, **kw: _FakeStatus())}))
    import pytest

    with pytest.raises(MarkdownToPdfError):
        markdown_to_pdf("# Title")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/conversion/test_markdown_to_pdf.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement the renderer**

```python
# src/conversion/markdown_to_pdf.py
"""Markdown -> PDF renderer for the file-write node.

Markdown -> HTML (markdown-it-py) -> PDF (xhtml2pdf) — pure-Python, no
system binaries/packages, keeping the existing multi-stage Docker image
lean. See docs/archive/phase-history/specs/2026-07-11-file-storage-provider-framework-design.md §C
for why this was chosen over a pandoc-based approach.
"""

import io

from markdown_it import MarkdownIt
from xhtml2pdf import pisa

_md = MarkdownIt("commonmark").enable("table")

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
    status = pisa.CreatePDF(io.StringIO(full_html), dest=buf)
    if status.err:
        raise MarkdownToPdfError(f"xhtml2pdf failed with err={status.err}")
    return buf.getvalue()


__all__ = ["MarkdownToPdfError", "markdown_to_pdf"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/conversion/test_markdown_to_pdf.py -v`
Expected: PASS

- [ ] **Step 5: Wire `format="pdf"` into `FileWriteExecutor`'s tests**

Add to `tests/unit/executors/test_file_write.py`:

```python
async def test_writes_pdf_file(tmp_path: Path) -> None:
    from src.engine.state import initial_state
    from src.executors.file_write import FileWriteExecutor

    node = _node(tmp_path, format="pdf", content="# Title\n\nBody.")
    await FileWriteExecutor(node).arun(initial_state())

    written = tmp_path / "report.pdf"
    assert written.exists()
    assert written.read_bytes().startswith(b"%PDF-")
```

Run: `uv run pytest tests/unit/executors/test_file_write.py -v`
Expected: PASS (all format variants: md, docx, pdf)

- [ ] **Step 6: Commit**

```bash
git add src/conversion/markdown_to_pdf.py tests/unit/conversion/test_markdown_to_pdf.py tests/unit/executors/test_file_write.py
git commit -m "feat(conversion): add Markdown to PDF renderer, wire into file-write"
```

---

### Task 8: `file-write` frontend wiring

**Files:**
- Create: `frontend/components/composer/canvas/node-panels/file-write.tsx`
- Modify: `frontend/components/composer/canvas/node-visuals.ts`
- Modify: `frontend/components/composer/canvas/tools-palette.tsx`
- Modify: `frontend/components/composer/canvas/property-panel.tsx`
- Modify: `frontend/components/composer/canvas/workflow-canvas.tsx`

- [ ] **Step 1: Palette entry**

In `tools-palette.tsx`'s `COMPOSER_NODE_PALETTE`:

```ts
{ nodeType: "file-write", label: "File Write" },
```

- [ ] **Step 2: Node-visuals entry**

Import `FolderOutput` from `lucide-react` in `node-visuals.ts`, add:

```ts
"file-write": {
  icon: FolderOutput,
  iconWrapClass: "bg-teal-100 text-teal-700",
  accent: "text-teal-700",
  label: "File Write",
},
```

- [ ] **Step 3: Register as a standard node**

In `workflow-canvas.tsx`'s `COMPOSER_NODE_TYPES`:

```ts
"file-write": InnerNode,
```

- [ ] **Step 4: Create the node panel**

```tsx
// frontend/components/composer/canvas/node-panels/file-write.tsx
"use client";

import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { PromptField } from "../prompt-field";

export default function FileWritePanel({
  data,
  onChange,
  allNodes,
  currentNodeId,
}: {
  data: Record<string, unknown>;
  onChange: (patch: Record<string, unknown>) => void;
  allNodes?: import("reactflow").Node[];
  currentNodeId?: string;
}) {
  return (
    <div className="space-y-4">
      <div className="rounded-md border bg-muted/20 p-3">
        <p className="mb-2 text-xs font-semibold text-muted-foreground">
          Destination
        </p>
        <div className="space-y-2">
          <div className="space-y-1">
            <Label htmlFor="fw-provider" className="text-xs">
              Provider
            </Label>
            <NativeSelect
              id="fw-provider"
              value={(data.provider as string) ?? "local"}
              onValueChange={(v) => onChange({ provider: v })}
              options={[{ value: "local", label: "Local filesystem" }]}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="fw-dest" className="text-xs">
              Destination path
            </Label>
            <Input
              id="fw-dest"
              value={(data.destinationPath as string) ?? ""}
              onChange={(e) => onChange({ destinationPath: e.target.value })}
              placeholder="/out or {{output_dir}}"
              className="font-mono text-xs"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="fw-filename" className="text-xs">
              Filename (no extension)
            </Label>
            <Input
              id="fw-filename"
              value={(data.filename as string) ?? ""}
              onChange={(e) => onChange({ filename: e.target.value })}
              placeholder="{{project_name}}-BRD"
              className="font-mono text-xs"
            />
            <p className="text-[10px] text-muted-foreground">
              The extension is added automatically based on Format below.
            </p>
          </div>
          <div className="space-y-1">
            <Label htmlFor="fw-format" className="text-xs">
              Format
            </Label>
            <NativeSelect
              id="fw-format"
              value={(data.format as string) ?? "md"}
              onValueChange={(v) => onChange({ format: v })}
              options={[
                { value: "md", label: "Markdown (.md)" },
                { value: "docx", label: "Word (.docx)" },
                { value: "pdf", label: "PDF (.pdf)" },
              ]}
            />
          </div>
        </div>
      </div>

      <PromptField
        label="Content"
        value={(data.content as string) ?? ""}
        onChange={(next) => onChange({ content: next })}
        nodes={allNodes ?? []}
        currentNodeId={currentNodeId ?? ""}
        rows={8}
        placeholder="Reference an upstream node's output, e.g. {{draft_brd}}"
      />
    </div>
  );
}
```

- [ ] **Step 5: Register the panel**

In `property-panel.tsx`, add:

```tsx
import FileWritePanel from "./node-panels/file-write";
```

```ts
"file-write": FileWritePanel,
```

```ts
"file-write": "File Write",
```

- [ ] **Step 6: Verify**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors

- [ ] **Step 7: Commit**

```bash
git add frontend/components/composer/canvas
git commit -m "feat(frontend): file-write node — palette, visuals, panel, registries"
```

---

### Task 9: `composer watch` CLI

**Files:**
- Modify: `src/cli/main.py`
- Create: `src/cli/watch.py`
- Test: `tests/unit/cli/test_watch.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/cli/test_watch.py
"""Tests for the `composer watch` CLI's per-file claim/trigger/move logic.

These test the pure claim-one-file function, not the infinite poll loop
(covered by a smoke test at the end) or real network calls (httpx is
mocked throughout).
"""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest


def _trigger_config(source: Path, dest: Path, error: Path) -> "object":
    from src.cli.watch import WatchConfig

    return WatchConfig(
        workflow_api_url="https://api.example.com",
        external_slug="my-workflow",
        api_key="ck_test",
        provider="local",
        source_path=str(source),
        dest_path=str(dest),
        error_path=str(error),
        target_input_variable="requirements_doc",
        poll_interval_seconds=1,
    )


async def test_claim_extracts_text_and_moves_to_dest_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.cli.watch import claim_file
    from src.storage_providers.local import LocalFilesystemProvider

    source = tmp_path / "in"
    dest = tmp_path / "done"
    error = tmp_path / "error"
    source.mkdir()
    (source / "note.md").write_bytes(b"# Hello")

    config = _trigger_config(source, dest, error)
    provider = LocalFilesystemProvider()
    await provider.list_new_files(str(source))  # first sighting
    refs = await provider.list_new_files(str(source))
    ref = refs[0]

    post_mock = AsyncMock(return_value=None)
    monkeypatch.setattr("src.cli.watch._trigger_workflow", post_mock)

    await claim_file(provider, ref, config)

    post_mock.assert_awaited_once()
    call_kwargs = post_mock.await_args.kwargs
    assert call_kwargs["input_payload"] == {"requirements_doc": "# Hello"}
    assert not (source / "note.md").exists()
    assert (dest / "note.md").exists()


async def test_claim_moves_to_error_path_on_trigger_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.cli.watch import claim_file
    from src.storage_providers.local import LocalFilesystemProvider

    source = tmp_path / "in"
    dest = tmp_path / "done"
    error = tmp_path / "error"
    source.mkdir()
    (source / "note.txt").write_bytes(b"content")

    config = _trigger_config(source, dest, error)
    provider = LocalFilesystemProvider()
    await provider.list_new_files(str(source))
    ref = (await provider.list_new_files(str(source)))[0]

    async def _boom(**kwargs: object) -> None:
        raise RuntimeError("backend unreachable")

    monkeypatch.setattr("src.cli.watch._trigger_workflow", _boom)

    await claim_file(provider, ref, config)

    assert not (source / "note.txt").exists()
    assert (error / "note.txt").exists()
    assert not (dest / "note.txt").exists()


async def test_claim_moves_to_error_path_on_unsupported_file_type(
    tmp_path: Path,
) -> None:
    from src.cli.watch import claim_file
    from src.storage_providers.local import LocalFilesystemProvider

    source = tmp_path / "in"
    dest = tmp_path / "done"
    error = tmp_path / "error"
    source.mkdir()
    (source / "image.png").write_bytes(b"\x89PNG\r\n")

    config = _trigger_config(source, dest, error)
    provider = LocalFilesystemProvider()
    await provider.list_new_files(str(source))
    ref = (await provider.list_new_files(str(source)))[0]

    await claim_file(provider, ref, config)

    assert (error / "image.png").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/cli/test_watch.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `src/cli/watch.py`**

```python
"""`composer watch` — polls a FileStorageProvider source, extracts text from
newly-claimed files, triggers a production workflow via the existing
external-invoke API, and moves the file to dest/error based on outcome.

Reuses POST /api/run/{slug} (ck_ bearer auth) rather than a new endpoint —
this is precisely the automated, non-interactive triggering mechanism that
endpoint already exists for. See
docs/archive/phase-history/specs/2026-07-11-file-storage-provider-framework-design.md §B.
"""

import asyncio
import io
import logging
from dataclasses import dataclass

import httpx

from src.storage_providers.base import FileRef, FileStorageProvider
from src.storage_providers.local import LocalFilesystemProvider

logger = logging.getLogger(__name__)

_PROVIDERS: dict[str, type[FileStorageProvider]] = {
    "local": LocalFilesystemProvider,
}


@dataclass(frozen=True, kw_only=True)
class WatchConfig:
    workflow_api_url: str  # backend base URL, e.g. https://api.example.com
    external_slug: str
    api_key: str  # ck_...
    provider: str
    source_path: str
    dest_path: str
    error_path: str
    target_input_variable: str
    poll_interval_seconds: int


class UnsupportedFileTypeError(ValueError):
    """Raised when a claimed file's extension has no known text-extraction path."""


def _extract_text(filename: str, raw: bytes) -> str:
    lower = filename.lower()
    if lower.endswith((".txt", ".md", ".markdown")):
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("utf-8-sig")
    if lower.endswith(".pdf"):
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(raw))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    if lower.endswith(".docx"):
        from docx import Document

        doc = Document(io.BytesIO(raw))
        return "\n".join(p.text for p in doc.paragraphs)
    raise UnsupportedFileTypeError(f"no extraction path for {filename!r}")


async def _trigger_workflow(*, config: WatchConfig, input_payload: dict[str, str]) -> None:
    url = f"{config.workflow_api_url}/api/run/{config.external_slug}"
    headers = {"Authorization": f"Bearer {config.api_key}"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
        resp = await client.post(url, headers=headers, json={"input": input_payload})
    if resp.status_code >= 400:
        raise RuntimeError(f"trigger failed (HTTP {resp.status_code}): {resp.text[:500]}")


async def claim_file(provider: FileStorageProvider, ref: FileRef, config: WatchConfig) -> None:
    """Process exactly one claimed file: extract, trigger, then move to
    dest (success) or error (extraction or trigger failure). One file's
    failure never raises — it's logged and the file lands in error_path."""
    try:
        raw = await provider.read_file(ref)
        text = _extract_text(ref.name, raw)
        await _trigger_workflow(
            config=config, input_payload={config.target_input_variable: text}
        )
    except Exception:
        logger.exception("composer watch: failed to claim %s", ref.name)
        await provider.move_file(ref, config.error_path)
        return

    await provider.move_file(ref, config.dest_path)


async def run_watch_loop(config: WatchConfig) -> None:
    """Poll forever until cancelled (Ctrl+C)."""
    provider_cls = _PROVIDERS.get(config.provider)
    if provider_cls is None:
        raise ValueError(f"unknown provider {config.provider!r}; registered: {sorted(_PROVIDERS)}")
    provider = provider_cls()

    logger.info(
        "composer watch: polling %s every %ds -> workflow slug %s",
        config.source_path,
        config.poll_interval_seconds,
        config.external_slug,
    )
    while True:
        try:
            refs = await provider.list_new_files(config.source_path)
            for ref in refs:
                await claim_file(provider, ref, config)
        except Exception:
            logger.exception("composer watch: unexpected error in poll loop")
        await asyncio.sleep(config.poll_interval_seconds)


__all__ = ["WatchConfig", "claim_file", "run_watch_loop"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/cli/test_watch.py -v`
Expected: PASS

- [ ] **Step 5: Wire the CLI subcommand**

In `src/cli/main.py`, add to `_build_parser()`:

```python
    # watch
    p_watch = sub.add_parser("watch", help="Poll a folder and trigger a production workflow")
    p_watch.add_argument("--api-url", required=True, help="Backend base URL, e.g. https://api.example.com")
    p_watch.add_argument("--slug", required=True, help="Production workflow's externalSlug")
    p_watch.add_argument("--api-key", required=True, help="ck_... API key")
    p_watch.add_argument("--provider", default="local", choices=["local"])
    p_watch.add_argument("--source", required=True, help="Source folder to watch")
    p_watch.add_argument("--dest", required=True, help="Destination folder on successful claim")
    p_watch.add_argument("--error", required=True, help="Destination folder on failed claim")
    p_watch.add_argument("--target-var", required=True, help="Start input variable to populate")
    p_watch.add_argument("--interval", type=int, default=30, help="Poll interval in seconds")
```

Add the dispatch branch in `main()`:

```python
    if args.subcommand == "watch":
        from src.cli.watch import WatchConfig, run_watch_loop

        config = WatchConfig(
            workflow_api_url=args.api_url,
            external_slug=args.slug,
            api_key=args.api_key,
            provider=args.provider,
            source_path=args.source,
            dest_path=args.dest,
            error_path=args.error,
            target_input_variable=args.target_var,
            poll_interval_seconds=args.interval,
        )
        try:
            asyncio.run(run_watch_loop(config))
        except KeyboardInterrupt:
            pass
        sys.exit(0)
```

- [ ] **Step 6: Update the module docstring**

At the top of `src/cli/main.py`, add `watch:      poll a folder and trigger a production workflow (2026-07-11)` to the subcommand list in the docstring.

- [ ] **Step 7: Manual smoke test**

Run: `uv run composer watch --help`
Expected: prints the new subcommand's usage with all flags listed, no error.

- [ ] **Step 8: Commit**

```bash
git add src/cli/watch.py src/cli/main.py tests/unit/cli/test_watch.py
git commit -m "feat(cli): add composer watch subcommand"
```

---

### Task 10: Docs

**Files:**
- Modify: `docs/designer-guide.md` (two new node-reference sections; node-count bump to 22)
- Modify: `docs/decisions.md` (new ADR)
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Update the designer guide**

Add `#### \`file-trigger\`` and `#### \`file-write\`` sections to `docs/designer-guide.md`'s node reference (following the exact format of the existing `jira` section added 2026-07-10), and bump every "20 node types" reference to "22". Document: `file-trigger` is visual-only and requires the `composer watch` CLI + a published (production) workflow to actually do anything; `file-write`'s three formats and the "filename has no extension, format supplies it" convention.

- [ ] **Step 2: Add the ADR**

Append `## ADR-0030: File storage provider framework — pluggable, local-first, inline-per-node config` to `docs/decisions.md`, following the ADR-0027/0028/0029 format. Cover: why `file-trigger` is visual-only rather than a real execution node; why it reuses `POST /api/run/{slug}` instead of a new trigger endpoint; why DOCX/PDF conversion is pure-Python over pandoc; why storage config is inline-per-node rather than a shared registry.

- [ ] **Step 3: Add the CHANGELOG entry**

Add `### Added — File storage provider framework (2026-07-11)` under `## [Unreleased]`, following the existing entries' Added/Notes format, listing every new file and the two new dependencies.

- [ ] **Step 4: Commit**

```bash
git add docs/designer-guide.md docs/decisions.md CHANGELOG.md
git commit -m "docs: file storage provider framework design record + designer guide + changelog"
```

---

### Final verification

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run pyright src tests
uv run pytest -m "not integration"
cd frontend && npx tsc --noEmit && npx vitest run
```

Expected: everything green, 0 pyright errors, no regressions.
