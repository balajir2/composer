# Phase 6a — Note + Join-Chunks: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the `join-chunks` executor + lock `note`'s visual-only behavior with a test. No external APIs. Completes the "local" executor catalog before Phase 6b/6c/6d add external integrations.

**Architecture.** New `src/executors/join_chunks.py` with `@register_executor("join-chunks")`. Tightens existing `JoinChunksNodeData` Pydantic model with explicit fields (replaces the Phase 1 placeholder `config: dict[str, Any]`). Note is already skipped at graph-build; Phase 6a just adds a regression-guard test.

**Tech Stack:** Pure Python, pytest, existing executor registry + variable pattern.

**Spec:** [`docs/superpowers/specs/2026-04-21-phase-6a-note-join-chunks-design.md`](../specs/2026-04-21-phase-6a-note-join-chunks-design.md)

---

## Sequencing and discipline

5 tasks, one commit each. Every task ends green on:

```bash
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m ruff format --check src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

Integration test (Task 4) runs at phase-exit (Task 5) with real Neon.

**⚠️ Forbidden files:** `pyproject.toml`, `.github/workflows/*`, `CLAUDE.md` (except Task 5), `docs/design/*`, `docs/superpowers/plans/*`, `docs/superpowers/specs/*`, Prisma schema + migrations.

Commit footer every commit:
```
Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
```

Stay on `main`.

---

## Task 1: Tighten `JoinChunksNodeData` Pydantic model

**Files:**
- Modify: `src/engine/workflow.py`
- Modify: `tests/unit/engine/test_workflow_models.py` (or wherever node-data tests live — check and append)

- [ ] **Step 1: Read the current definition**

```bash
grep -n "JoinChunksNodeData\|JoinChunksNode\b" src/engine/workflow.py
```

The current block (around lines 328-340):
```python
class JoinChunksNodeData(BaseNodeData):
    # TODO(phase-6): tighten when OAB's join-chunks executor is studied.
    config: dict[str, Any] = Field(default_factory=dict)


class JoinChunksNode(BaseModel):
    id: str
    type: Literal["join-chunks"]
    position: Position
    data: JoinChunksNodeData
```

- [ ] **Step 2: Replace `JoinChunksNodeData` with explicit fields**

```python
class JoinChunksNodeData(BaseNodeData):
    model_config = ConfigDict(populate_by_name=True)

    variable: str = Field(alias="joinChunksVariable")
    separator: str = Field(default="\n\n", alias="joinChunksSeparator")
    prefix: str = Field(default="", alias="joinChunksPrefix")
    suffix: str = Field(default="", alias="joinChunksSuffix")
    include_metadata: bool = Field(default=False, alias="joinChunksIncludeMetadata")
```

Confirm `ConfigDict` is already imported at the top of `workflow.py` from `pydantic`. If not, add it.

- [ ] **Step 3: Add tests**

First check if there's an existing `tests/unit/engine/test_workflow_models.py`. If yes, append; if no, create a minimal new file for JoinChunksNodeData only. Tests:

```python
from src.engine.workflow import JoinChunksNode, JoinChunksNodeData


def test_join_chunks_node_data_parses_camelcase_aliases() -> None:
    data = JoinChunksNodeData.model_validate({
        "label": "JC",
        "joinChunksVariable": "chunks",
        "joinChunksSeparator": "\n---\n",
        "joinChunksPrefix": "> ",
        "joinChunksSuffix": " <",
        "joinChunksIncludeMetadata": True,
    })
    assert data.variable == "chunks"
    assert data.separator == "\n---\n"
    assert data.prefix == "> "
    assert data.suffix == " <"
    assert data.include_metadata is True


def test_join_chunks_node_data_defaults() -> None:
    data = JoinChunksNodeData.model_validate({
        "label": "JC",
        "joinChunksVariable": "chunks",
    })
    assert data.variable == "chunks"
    assert data.separator == "\n\n"
    assert data.prefix == ""
    assert data.suffix == ""
    assert data.include_metadata is False


def test_join_chunks_node_data_missing_variable_raises() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        JoinChunksNodeData.model_validate({"label": "JC"})


def test_join_chunks_node_full_round_trip() -> None:
    node = JoinChunksNode.model_validate({
        "id": "jc1",
        "type": "join-chunks",
        "position": {"x": 0, "y": 0},
        "data": {
            "label": "JC",
            "joinChunksVariable": "chunks",
        },
    })
    assert node.id == "jc1"
    assert node.type == "join-chunks"
    assert node.data.variable == "chunks"
```

If the conventional path is `tests/unit/engine/test_workflow.py` or `test_workflow_models.py`, use whatever already exists. Confirm with a quick `ls tests/unit/engine/`.

- [ ] **Step 4: Run + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/engine -v -k "join_chunks"
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

Expected: 4 new tests pass; overall +4 (~405 from Phase 5b 401).

```bash
git add src/engine/workflow.py tests/unit/engine/test_workflow*.py
git commit -m "feat(workflow): tighten JoinChunksNodeData Pydantic model (Phase 6a)

Replaces the Phase 1 placeholder {config: dict} with explicit fields:
  - variable (required, alias joinChunksVariable)
  - separator (default '\\n\\n', alias joinChunksSeparator)
  - prefix / suffix (default '', aliases joinChunksPrefix/Suffix)
  - include_metadata (default False, alias joinChunksIncludeMetadata)

Matches OAB lib/workflow/types.ts:132-137.  populate_by_name=True lets
Python code use snake_case while JSON uses camelCase.

See Phase 6a spec §4.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `JoinChunksExecutor`

**Files:**
- Create: `src/executors/join_chunks.py`
- Modify: `src/engine/graph_builder.py` (side-effect import)
- Create: `tests/unit/executors/test_join_chunks.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/executors/test_join_chunks.py`:

```python
"""Tests for the join-chunks executor."""

from typing import Any

import pytest

from src.engine.state import initial_state
from src.engine.workflow import JoinChunksNode
from src.executors.join_chunks import JoinChunksExecutor, JoinChunksNodeError


def _node(**overrides: Any) -> JoinChunksNode:
    data: dict[str, Any] = {"label": "JC", "joinChunksVariable": "chunks"}
    data.update(overrides)
    return JoinChunksNode.model_validate({
        "id": "jc",
        "type": "join-chunks",
        "position": {"x": 0, "y": 0},
        "data": data,
    })


async def test_string_chunks_default_separator() -> None:
    state = initial_state()
    state["variables"]["chunks"] = ["alpha", "beta", "gamma"]
    delta = await JoinChunksExecutor(_node()).arun(state)
    assert delta["variables"]["lastOutput"] == "alpha\n\nbeta\n\ngamma"


async def test_string_chunks_custom_separator_prefix_suffix() -> None:
    state = initial_state()
    state["variables"]["chunks"] = ["a", "b"]
    node = _node(
        joinChunksSeparator="\n---\n",
        joinChunksPrefix="> ",
        joinChunksSuffix=" <",
    )
    delta = await JoinChunksExecutor(node).arun(state)
    assert delta["variables"]["lastOutput"] == "> a <\n---\n> b <"


async def test_dict_chunks_with_content_key() -> None:
    state = initial_state()
    state["variables"]["chunks"] = [
        {"content": "first", "metadata": {"id": 1}},
        {"content": "second", "metadata": {"id": 2}},
    ]
    delta = await JoinChunksExecutor(_node()).arun(state)
    assert delta["variables"]["lastOutput"] == "first\n\nsecond"


async def test_dict_chunks_without_content_json_serialized() -> None:
    state = initial_state()
    state["variables"]["chunks"] = [{"foo": "bar"}, {"x": 1}]
    delta = await JoinChunksExecutor(_node()).arun(state)
    joined = delta["variables"]["lastOutput"]
    assert '{"foo": "bar"}' in joined
    assert '{"x": 1}' in joined


async def test_include_metadata_appends_metadata_line() -> None:
    state = initial_state()
    state["variables"]["chunks"] = [
        {"content": "hello", "metadata": {"source": "a.txt"}},
    ]
    delta = await JoinChunksExecutor(_node(joinChunksIncludeMetadata=True)).arun(state)
    out = delta["variables"]["lastOutput"]
    assert "hello" in out
    assert '[metadata: {"source": "a.txt"}]' in out


async def test_include_metadata_false_omits_metadata() -> None:
    state = initial_state()
    state["variables"]["chunks"] = [
        {"content": "hello", "metadata": {"source": "a.txt"}},
    ]
    delta = await JoinChunksExecutor(_node(joinChunksIncludeMetadata=False)).arun(state)
    assert "metadata" not in delta["variables"]["lastOutput"]


async def test_mixed_string_and_dict_chunks() -> None:
    state = initial_state()
    state["variables"]["chunks"] = ["plain", {"content": "wrapped"}, {"no_content": True}]
    delta = await JoinChunksExecutor(_node()).arun(state)
    out = delta["variables"]["lastOutput"]
    parts = out.split("\n\n")
    assert parts[0] == "plain"
    assert parts[1] == "wrapped"
    assert '"no_content"' in parts[2]


async def test_empty_list_returns_empty_string() -> None:
    state = initial_state()
    state["variables"]["chunks"] = []
    delta = await JoinChunksExecutor(_node()).arun(state)
    assert delta["variables"]["lastOutput"] == ""


async def test_missing_variable_raises() -> None:
    state = initial_state()
    with pytest.raises(JoinChunksNodeError, match="chunks"):
        await JoinChunksExecutor(_node()).arun(state)


async def test_non_list_value_raises() -> None:
    state = initial_state()
    state["variables"]["chunks"] = "not a list"
    with pytest.raises(JoinChunksNodeError, match="not a list"):
        await JoinChunksExecutor(_node()).arun(state)


async def test_executor_is_registered() -> None:
    import src.executors.join_chunks  # noqa: F401  # pyright: ignore[reportUnusedImport]

    from src.executors.base import build_executor

    node = _node()
    executor = build_executor(node)
    assert isinstance(executor, JoinChunksExecutor)


async def test_node_result_shape() -> None:
    state = initial_state()
    state["variables"]["chunks"] = ["a", "b"]
    delta = await JoinChunksExecutor(_node()).arun(state)
    result = delta["node_results"]["jc"]
    assert result["status"] == "completed"
    assert result["input"]["variable"] == "chunks"
    assert result["input"]["chunk_count"] == 2
    assert result["output"] == "a\n\nb"
```

- [ ] **Step 2: Verify fail**

```bash
.venv/Scripts/python -m pytest tests/unit/executors/test_join_chunks.py -v
```
Expected: ImportError on `src.executors.join_chunks`.

- [ ] **Step 3: Implement `src/executors/join_chunks.py`**

```python
"""join-chunks node executor.

Concatenates an array of chunks from state.variables[variable] with
configurable separator/prefix/suffix, optionally appending metadata.

Chunk shapes supported:
  - str: used as-is
  - dict with 'content' key: use the content string
  - dict without 'content': JSON-serialize the whole dict (compact)
  - other: str(chunk)

Output lands on lastOutput (consistent with http/transform/extract).

See Phase 6a spec §5.
"""

from __future__ import annotations

import json
from typing import Any

from src.engine.state import WorkflowStateDict
from src.engine.workflow import JoinChunksNode
from src.executors.base import register_executor


class JoinChunksNodeError(RuntimeError):
    """Raised when the join-chunks executor can't resolve its input."""


@register_executor("join-chunks")
class JoinChunksExecutor:
    def __init__(self, node: JoinChunksNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        variables = state.get("variables") or {}
        variable_name = self.node.data.variable
        chunks_raw: Any = variables.get(variable_name)

        if chunks_raw is None:
            raise JoinChunksNodeError(
                f"join-chunks node {self.node.id!r}: variable {variable_name!r} "
                f"not found in state"
            )
        if not isinstance(chunks_raw, list):
            raise JoinChunksNodeError(
                f"join-chunks node {self.node.id!r}: variable {variable_name!r} "
                f"is not a list; got {type(chunks_raw).__name__}"
            )

        rendered_items: list[str] = []
        for chunk in chunks_raw:
            content = self._render_content(chunk)
            rendered = f"{self.node.data.prefix}{content}{self.node.data.suffix}"
            if (
                self.node.data.include_metadata
                and isinstance(chunk, dict)
                and chunk.get("metadata")
            ):
                rendered += f"\n[metadata: {json.dumps(chunk['metadata'])}]"
            rendered_items.append(rendered)

        joined = self.node.data.separator.join(rendered_items)

        return {
            "variables": {"lastOutput": joined},
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "input": {
                        "variable": variable_name,
                        "chunk_count": len(chunks_raw),
                    },
                    "output": joined,
                }
            },
        }

    @staticmethod
    def _render_content(chunk: Any) -> str:
        if isinstance(chunk, str):
            return chunk
        if isinstance(chunk, dict):
            content = chunk.get("content")
            if isinstance(content, str):
                return content
            return json.dumps(chunk)
        return str(chunk)


__all__ = ["JoinChunksExecutor", "JoinChunksNodeError"]
```

- [ ] **Step 4: Register side-effect import in `graph_builder.py`**

Read the file. Add alphabetically (between `if_else` and `mcp`, OR wherever matches the existing ordering):

```python
from src.executors import (
    join_chunks as _join_chunks_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
```

Do NOT touch other graph_builder logic.

- [ ] **Step 5: Run + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/executors/test_join_chunks.py -v
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

Expected: 12 new tests pass; overall +12 (~417 from 405 after Task 1). Pyright baseline (0 errors) unchanged.

```bash
git add src/executors/join_chunks.py src/engine/graph_builder.py tests/unit/executors/test_join_chunks.py
git commit -m "feat(executors): join-chunks — concat list with separator + metadata (Phase 6a)

Reads state.variables[variable] (expected list), renders each chunk
(str as-is; dict with 'content' uses the string; dict without
'content' JSON-serialized), optionally appends [metadata: {json}]
when include_metadata=True, joins with separator.  Prefix/suffix
wrap each rendered item.  Output lands on lastOutput.

Errors: missing variable → JoinChunksNodeError; non-list value →
JoinChunksNodeError.  Empty list → empty string (legitimate case).

Matches OAB types.ts:132-137 field semantics.  OAB did NOT ship an
executor for this node type — Composer implements the behavior per
the declared data shape.

See Phase 6a spec §5.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Note affirmation lock-test

**Files:**
- Modify: `tests/unit/engine/test_graph_builder.py` (append)

- [ ] **Step 1: Write test**

Append to `tests/unit/engine/test_graph_builder.py`:

```python
def test_build_graph_skips_note_nodes() -> None:
    """Note nodes are visual-only: graph_builder skips them, and they are
    allowed to be disconnected from the rest of the workflow."""
    from langgraph.checkpoint.memory import MemorySaver

    from src.engine.graph_builder import build_graph
    from src.engine.workflow import Workflow

    wf = Workflow.model_validate({
        "id": "w",
        "name": "note test",
        "nodes": [
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {
                "id": "n",
                "type": "note",
                "position": {"x": 0, "y": 100},
                "data": {"label": "some note", "text": "FYI — this is a design note"},
            },
            {"id": "e", "type": "end", "position": {"x": 100, "y": 0}, "data": {"label": "E"}},
        ],
        # Note deliberately disconnected — visual-only nodes are allowed to float
        "edges": [{"id": "e1", "source": "s", "target": "e"}],
    })
    compiled = build_graph(wf, MemorySaver())
    assert compiled is not None
```

If the existing test file uses a different fixture style (e.g., `_mk` helper or module-level imports at the top), match the prevailing pattern. Read the first 60 lines of the file first.

- [ ] **Step 2: Run + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/engine/test_graph_builder.py -v -k "note"
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

Expected: 1 new test passes; overall +1 (~418 from 417 after Task 2).

```bash
git add tests/unit/engine/test_graph_builder.py
git commit -m "test(engine): lock note-node skip behavior (Phase 6a)

Regression test: workflow with a disconnected note node compiles
successfully.  graph_builder.build_graph's 'if node.type == \"note\":
continue' check is the guarantee this exercises.

See Phase 6a spec §3.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Integration test — real Neon

**Files:**
- Create: `tests/integration/test_join_chunks.py`

- [ ] **Step 1: Pick a list-producing upstream executor**

`set-state` with `stateValue=[...]` may or may not support lists (check its data model in `src/engine/workflow.py`). If it does, use it. If it doesn't, use `transform` with a simpleeval expression that evaluates to a list (e.g., `"['alpha', 'beta', 'gamma']"`) — simpleeval supports list literals.

Read `src/engine/workflow.py` for `SetStateNodeData` and `TransformNodeData` before writing the test. Pick the cleanest path.

- [ ] **Step 2: Write `tests/integration/test_join_chunks.py`**

```python
"""Integration — start → [list producer] → join-chunks → end (real Neon)."""

import asyncio
from typing import Any

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def _poll_until_status(
    client: AsyncClient,
    execution_id: str,
    target: set[str],
    timeout: float = 20.0,
) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        resp = await client.get(f"/executions/{execution_id}")
        body: dict[str, Any] = resp.json()
        if body["status"] in target:
            return body
        await asyncio.sleep(0.2)
    raise AssertionError(
        f"Execution {execution_id} did not reach {target} within {timeout}s"
    )


async def test_join_chunks_concatenates_list_from_upstream(client: AsyncClient) -> None:
    """transform produces a list literal; join-chunks concatenates; end."""
    wf = await client.post(
        "/workflows",
        json={
            "name": "Phase 6a join-chunks integration",
            "nodes": [
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                {
                    "id": "t",
                    "type": "transform",
                    "position": {"x": 100, "y": 0},
                    "data": {
                        "label": "T",
                        # transform writes the result to a configurable state key.
                        # Confirm exact field names from TransformNodeData before
                        # writing this; alternative is set-state if it supports list values.
                        "transformExpression": "['alpha', 'beta', 'gamma']",
                        "transformOutputVariable": "chunks",
                    },
                },
                {
                    "id": "jc",
                    "type": "join-chunks",
                    "position": {"x": 200, "y": 0},
                    "data": {
                        "label": "JC",
                        "joinChunksVariable": "chunks",
                        "joinChunksSeparator": " | ",
                    },
                },
                {"id": "e", "type": "end", "position": {"x": 300, "y": 0}, "data": {"label": "E"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "t"},
                {"id": "e2", "source": "t", "target": "jc"},
                {"id": "e3", "source": "jc", "target": "e"},
            ],
        },
    )
    assert wf.status_code == 201, wf.text
    start = await client.post("/executions", json={"workflowId": wf.json()["id"], "input": ""})
    execution_id = start.json()["id"]

    done = await _poll_until_status(client, execution_id, {"completed", "failed"})
    assert done["status"] == "completed", f"Got: {done}"

    variables = done.get("variables") or {}
    assert isinstance(variables, dict)
    assert variables.get("lastOutput") == "alpha | beta | gamma"
```

**IMPORTANT:** Before committing, verify the exact field names used by `TransformNodeData` (or `SetStateNodeData` if you pick that alternative). Look up the Pydantic model in `src/engine/workflow.py`. If `transformExpression` / `transformOutputVariable` aren't the right aliases, adjust.

- [ ] **Step 3: Quality gates**

```bash
.venv/Scripts/python -m ruff check tests/integration/test_join_chunks.py
.venv/Scripts/python -m ruff format tests/integration/test_join_chunks.py
.venv/Scripts/python -m pyright tests/integration/test_join_chunks.py
.venv/Scripts/python -m pytest tests/integration/test_join_chunks.py --collect-only -q
```

Expected: 1 test collected; pyright 0 errors.

**Do NOT run the integration test against real Neon here** — that happens in Task 5.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_join_chunks.py
git commit -m "test(integration): join-chunks concatenates list from upstream (Phase 6a)

Real Neon.  transform produces a list literal via simpleeval; join-chunks
reads the resulting variable, concatenates with custom separator,
writes to lastOutput.  Asserts the final lastOutput matches the
expected joined string.

See Phase 6a spec §7.3.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Phase-exit — CHANGELOG + CLAUDE.md + real-Neon run

**Authorized:** `CHANGELOG.md`, `CLAUDE.md`.

- [ ] **Step 1: Run exit checklist + integration tests**

```bash
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m ruff format --check src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

Then the integration suite (controller runs this, not a subagent):
```bash
.venv/Scripts/python -c "
from dotenv import load_dotenv
import os, subprocess, sys
load_dotenv('.env', override=True)
os.environ['TEST_DATABASE_URL'] = os.environ.get('DATABASE_URL', '')
r = subprocess.run(
    [sys.executable, '-m', 'pytest', '-v', '--tb=short', '--no-cov',
     '-m', 'integration',
     'tests/integration/test_join_chunks.py'],
    cwd='.'
)
sys.exit(r.returncode)
"
```

If integration fails, fix in a separate `fix(phase-6a): ...` commit BEFORE updating CHANGELOG.

- [ ] **Step 2: Update CHANGELOG.md**

Insert above the Phase 5b section:

```markdown
### Phase 6a — Note + Join-Chunks (2026-04-21)

#### Added
- [Phase 6a design spec](docs/superpowers/specs/2026-04-21-phase-6a-note-join-chunks-design.md).
- `src/executors/join_chunks.py` — `JoinChunksExecutor` concatenates a list of chunks (strings, or dicts with `content`/`metadata`) with configurable separator/prefix/suffix; optionally appends `[metadata: {json}]` per chunk. Output to `lastOutput`. Errors: missing variable / non-list value → `JoinChunksNodeError`; empty list → empty string.
- `src/engine/workflow.py` — `JoinChunksNodeData` tightened with explicit fields (replaces Phase 1 `config: dict[str, Any]` placeholder). Matches OAB `types.ts` aliases.
- Regression test in `tests/unit/engine/test_graph_builder.py` locks the note-node skip behavior: a workflow with a disconnected note node compiles cleanly.
- Integration test against real Neon: `start → transform(→ list) → join-chunks → end`, asserts final `lastOutput`.

#### Notes
- OAB did NOT ship an executor for `join-chunks` (the type was declared but unwired). Composer implements the behavior per the declared field semantics.
- `note` is visual-only; already skipped at graph-build time since Phase 1. Phase 6a adds a lock-test so the behavior doesn't regress.

### Phase 5b — SSE streaming (2026-04-21)
```

- [ ] **Step 3: Update CLAUDE.md phase table**

Change:
```markdown
| 5b — SSE streaming | ✅ Complete | `GET /executions/{id}/events` with in-process asyncio bus; 5 event types; full taxonomy verified against real Neon |
| 6 — Guardrails, Note, Vector-DB, Gamma, Arcade | ⏭ Next | visual + data executors (see design §6) |
```
To:
```markdown
| 5b — SSE streaming | ✅ Complete | `GET /executions/{id}/events` with in-process asyncio bus; 5 event types; full taxonomy verified against real Neon |
| 6a — Note + Join-Chunks | ✅ Complete | note visual-only (skip lock-test); join-chunks concatenates chunk lists with separator/prefix/suffix/metadata; verified against real Neon |
| 6b — Guardrails | ⏭ Next | moderation-style executor (tool-provider pattern) |
| 6c — Gamma-AI + Arcade | ⏸ | HTTP integrations |
| 6d — Vector-DB | ⏸ | provider framework (embed/upsert/query) |
```

- [ ] **Step 4: Commit + push**

```bash
git add CHANGELOG.md CLAUDE.md
git commit -m "docs(phase-6a): mark Phase 6a complete

Note + join-chunks shipped.  JoinChunksExecutor concatenates list
chunks (strings or {content, metadata} dicts) with configurable
separator/prefix/suffix and optional metadata appending.  note
remains visual-only (graph_builder skip locked with regression test).

Real-Neon integration: transform → join-chunks happy path green.

Phase 6b (guardrails) is next.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"

git push origin main
```

---

## Spec coverage self-review

| Spec section | Tasks |
|---|---|
| §3 note skip + lock-test | Task 3 |
| §4 JoinChunksNodeData tightening | Task 1 |
| §5 JoinChunksExecutor | Task 2 |
| §6 error model | Task 2 (tests 9, 10) |
| §7.1 unit tests | Task 2 (12 tests) |
| §7.2 note affirmation test | Task 3 |
| §7.3 integration test | Task 4 |
| §8 phase-exit | Task 5 |

No placeholders. Type consistency:
- `JoinChunksNodeData` field names match across spec §4, Task 1 code, and Task 2 executor reads.
- `lastOutput` output path consistent with §5 and Task 2 test expectations.
- `JoinChunksNodeError` message format (§6 / Task 2 test assertions / Task 2 executor raises) aligned.

---

## Execution handoff

Plan saved. Controller proceeds to `superpowers:subagent-driven-development` for Tasks 1–5. Same cadence as Phases 1–5b. Task 5 runs integration suite against real Neon before phase-exit.
