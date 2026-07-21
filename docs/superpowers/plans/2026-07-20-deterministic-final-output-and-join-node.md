# Deterministic Final-Output and Join Node Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the race where two `End` nodes firing in the same LangGraph superstep silently lose one branch's output, and add an explicit `join` node type plus a validation rule (every `End` node must have exactly one incoming edge) so designers have a self-documenting way to converge parallel branches.

**Architecture:** A new `final_outputs` state field, keyed by `End` node id (mirroring `node_results`'s already-proven collision-safe per-node-id keying), replaces the single shared `variables["finalOutput"]` key `EndExecutor` currently races on. `_mark_completed` collapses a single-entry `final_outputs` dict back to a scalar (byte-for-byte identical behavior for every existing single-`End` workflow) and persists the full dict only when genuinely multiple `End` nodes fire. A new `join` node type (real, non-visual-only; N incoming edges, exactly 1 outgoing edge) gives designers an explicit way to converge branches, paired with a `validate_workflow_shape` rule requiring every `End` node to have exactly one incoming edge.

**Tech Stack:** Python 3.11, Pydantic v2, LangGraph Python, pytest (backend). Next.js/React, vitest (frontend).

**Spec:** `docs/superpowers/specs/2026-07-20-deterministic-final-output-design.md`.

---

## Scope note (platform vs. customer flow)

Generic platform capability — no Macy's-specific or other customer-specific literals appear anywhere in this plan's code.

---

### Task 1: `final_outputs` state field

**Files:**
- Modify: `src/engine/state.py`
- Test: `tests/unit/engine/test_state.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/engine/test_state.py`:

```python
def test_initial_state_includes_final_outputs() -> None:
    s = initial_state()
    assert s["final_outputs"] == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/engine/test_state.py -v`

Expected: FAIL — `KeyError: 'final_outputs'`.

- [ ] **Step 3: Add the state field**

In `src/engine/state.py`, update `WorkflowStateDict` (currently):

```python
class WorkflowStateDict(TypedDict):
    variables: Annotated[dict[str, Any], merge_dict]
    chat_history: Annotated[list[ChatMessage], add]
    current_node_id: Annotated[str, last_wins]
    node_results: Annotated[dict[str, NodeExecutionResult], merge_dict]
    pending_auth: Annotated[dict[str, Any] | None, last_wins]
    loop_results: Annotated[list[Any], add]
    user_id: NotRequired[str]  # Phase 7a: propagated from WorkflowExecution.userId
```

to:

```python
class WorkflowStateDict(TypedDict):
    variables: Annotated[dict[str, Any], merge_dict]
    chat_history: Annotated[list[ChatMessage], add]
    current_node_id: Annotated[str, last_wins]
    node_results: Annotated[dict[str, NodeExecutionResult], merge_dict]
    pending_auth: Annotated[dict[str, Any] | None, last_wins]
    loop_results: Annotated[list[Any], add]
    # Keyed by End node id -- collision-safe by construction, the same
    # pattern node_results already uses, so two End nodes firing in the
    # same superstep never race (see src/executors/end.py).
    final_outputs: Annotated[dict[str, Any], merge_dict]
    user_id: NotRequired[str]  # Phase 7a: propagated from WorkflowExecution.userId
```

And update `initial_state()` (currently):

```python
def initial_state(raw_input: Any = "") -> WorkflowStateDict:
    """Build the default starting state, matching OAB's Annotation defaults."""
    return {
        "variables": {"input": raw_input, "lastOutput": ""},
        "chat_history": [],
        "current_node_id": "",
        "node_results": {},
        "pending_auth": None,
        "loop_results": [],
    }
```

to:

```python
def initial_state(raw_input: Any = "") -> WorkflowStateDict:
    """Build the default starting state, matching OAB's Annotation defaults."""
    return {
        "variables": {"input": raw_input, "lastOutput": ""},
        "chat_history": [],
        "current_node_id": "",
        "node_results": {},
        "pending_auth": None,
        "loop_results": [],
        "final_outputs": {},
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/engine/test_state.py -v`

Expected: PASS (all tests, including every pre-existing one)

- [ ] **Step 5: Run the full backend test suite and lint/format checks**

Run: `.venv/Scripts/python.exe -m pytest -m "not integration" -q && .venv/Scripts/python.exe -m ruff check src tests && .venv/Scripts/python.exe -m ruff format --check src tests`

Expected: some existing tests in `tests/unit/executors/test_end.py` and `tests/unit/engine/test_langgraph_executor.py` will now FAIL — that's expected, they're fixed in Tasks 2 and 3. Everything else should be clean.

- [ ] **Step 6: Commit**

```bash
git add src/engine/state.py tests/unit/engine/test_state.py
git commit -m "$(cat <<'EOF'
feat(state): add final_outputs field, keyed by End node id

Mirrors node_results' already-proven collision-safe per-node-id keying
-- two End nodes writing to their own key in this dict merge cleanly
regardless of concurrent execution order, unlike the single shared
variables.finalOutput key EndExecutor currently races on. EndExecutor
itself is updated in the next task.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `EndExecutor` writes to `final_outputs`

**Files:**
- Modify: `src/executors/end.py`
- Test: `tests/unit/executors/test_end.py`

- [ ] **Step 1: Update the two existing tests to the new mechanism**

Replace the two existing tests in `tests/unit/executors/test_end.py` (currently):

```python
async def test_end_returns_last_output(end_node: EndNode) -> None:
    state = initial_state()
    state["variables"]["lastOutput"] = {"answer": 42}
    delta = await EndExecutor(end_node).arun(state)
    assert delta["variables"]["finalOutput"] == {"answer": 42}
    assert delta["current_node_id"] == "e"
    assert delta["node_results"]["e"]["status"] == "completed"


async def test_end_tolerates_missing_last_output(end_node: EndNode) -> None:
    state = initial_state()
    # variables has {"input": "", "lastOutput": ""} from initial_state
    state["variables"].pop("lastOutput")
    delta = await EndExecutor(end_node).arun(state)
    assert delta["variables"]["finalOutput"] is None
```

with:

```python
async def test_end_returns_last_output(end_node: EndNode) -> None:
    state = initial_state()
    state["variables"]["lastOutput"] = {"answer": 42}
    delta = await EndExecutor(end_node).arun(state)
    assert delta["final_outputs"] == {"e": {"answer": 42}}
    assert delta["current_node_id"] == "e"
    assert delta["node_results"]["e"]["status"] == "completed"


async def test_end_tolerates_missing_last_output(end_node: EndNode) -> None:
    state = initial_state()
    # variables has {"input": "", "lastOutput": ""} from initial_state
    state["variables"].pop("lastOutput")
    delta = await EndExecutor(end_node).arun(state)
    assert delta["final_outputs"] == {"e": None}
```

`test_end_is_registered` is unchanged.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/executors/test_end.py -v`

Expected: FAIL — `delta["final_outputs"]` raises `KeyError` since `EndExecutor` still writes `variables["finalOutput"]`.

- [ ] **Step 3: Update `EndExecutor`**

Replace the full contents of `src/executors/end.py`:

```python
"""End node executor.

Port of OAB's lib/workflow/langgraph.ts:653-654. Reads
state.variables.lastOutput and surfaces it keyed by this End node's own
id in final_outputs -- not a shared "finalOutput" key -- so two End
nodes firing in the same LangGraph superstep (parallel fan-out, each
branch terminating at its own End) never race: merge_dict's shallow
union accumulates disjoint per-node-id keys correctly regardless of
concurrent execution order, the same pattern node_results already
relies on. src/engine/langgraph_executor.py's _mark_completed collapses
a single-entry final_outputs dict back to a plain scalar for the
common (single-End) case.
"""

from typing import Any

from src.engine.state import WorkflowStateDict
from src.engine.workflow import EndNode
from src.executors.base import register_executor


@register_executor("end")
class EndExecutor:
    def __init__(self, node: EndNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        last_output = state["variables"].get("lastOutput")
        return {
            "final_outputs": {self.node.id: last_output},
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "output": last_output,
                }
            },
        }


__all__ = ["EndExecutor"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/executors/test_end.py -v`

Expected: PASS (all 3 tests)

- [ ] **Step 5: Run the full backend test suite and lint/format checks**

Run: `.venv/Scripts/python.exe -m pytest -m "not integration" -q && .venv/Scripts/python.exe -m ruff check src tests && .venv/Scripts/python.exe -m ruff format --check src tests`

Expected: the two `_mark_completed` tests in `tests/unit/engine/test_langgraph_executor.py` will still FAIL — fixed in Task 3. Everything else clean.

- [ ] **Step 6: Commit**

```bash
git add src/executors/end.py tests/unit/executors/test_end.py
git commit -m "$(cat <<'EOF'
fix(end-node): write to final_outputs keyed by node id, not shared finalOutput

Closes the actual race: two End nodes writing to the same
variables.finalOutput key in the same LangGraph superstep silently lose
one branch's output via merge_dict's shallow last-write-wins overwrite.
Keying by this node's own id (mirroring node_results' already-proven
pattern) makes concurrent writes collision-safe by construction.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `_mark_completed` derives `output` from `final_outputs`

**Files:**
- Modify: `src/engine/langgraph_executor.py`
- Test: `tests/unit/engine/test_langgraph_executor.py`

- [ ] **Step 1: Update the two existing tests, add a third**

Replace the two existing tests in `tests/unit/engine/test_langgraph_executor.py` (currently):

```python
async def test_mark_completed_preserves_falsy_final_output() -> None:
    """P0-7 regression guard: `finalOutput or lastOutput` discards a
    legitimate falsy finalOutput (0, False, "", [], {}) and silently
    substitutes lastOutput instead, since Python treats a present-but-
    falsy value the same as absent under `or`."""
    db = MagicMock()
    db.workflowexecution = MagicMock()
    db.workflowexecution.update = AsyncMock()
    executor = LangGraphExecutor(db=db, checkpointer=MemorySaver())

    final_state = {"variables": {"finalOutput": 0, "lastOutput": "should not be used"}}
    await executor._mark_completed("ex1", final_state)  # pyright: ignore[reportPrivateUsage]

    assert db.workflowexecution.update.await_args is not None
    update_kwargs = db.workflowexecution.update.await_args.kwargs["data"]
    assert update_kwargs["output"].data == 0


async def test_mark_completed_falls_back_to_last_output_when_final_output_absent() -> None:
    """The fallback itself is correct behavior — only guard against
    `finalOutput` being SET (even falsy) getting overridden."""
    db = MagicMock()
    db.workflowexecution = MagicMock()
    db.workflowexecution.update = AsyncMock()
    executor = LangGraphExecutor(db=db, checkpointer=MemorySaver())

    final_state = {"variables": {"lastOutput": "fallback value"}}
    await executor._mark_completed("ex1", final_state)  # pyright: ignore[reportPrivateUsage]

    assert db.workflowexecution.update.await_args is not None
    update_kwargs = db.workflowexecution.update.await_args.kwargs["data"]
    assert update_kwargs["output"].data == "fallback value"
```

with:

```python
async def test_mark_completed_preserves_falsy_final_output() -> None:
    """P0-7 regression guard: a legitimate falsy final output (0, False,
    "", [], {}) must not be discarded in favor of lastOutput."""
    db = MagicMock()
    db.workflowexecution = MagicMock()
    db.workflowexecution.update = AsyncMock()
    executor = LangGraphExecutor(db=db, checkpointer=MemorySaver())

    final_state = {
        "final_outputs": {"end-1": 0},
        "variables": {"lastOutput": "should not be used"},
    }
    await executor._mark_completed("ex1", final_state)  # pyright: ignore[reportPrivateUsage]

    assert db.workflowexecution.update.await_args is not None
    update_kwargs = db.workflowexecution.update.await_args.kwargs["data"]
    assert update_kwargs["output"].data == 0


async def test_mark_completed_falls_back_to_last_output_when_no_end_reached() -> None:
    """No End node reached (e.g. a run that errored before completion) --
    falls back to lastOutput, matching the historical fallback behavior
    for that case."""
    db = MagicMock()
    db.workflowexecution = MagicMock()
    db.workflowexecution.update = AsyncMock()
    executor = LangGraphExecutor(db=db, checkpointer=MemorySaver())

    final_state = {"final_outputs": {}, "variables": {"lastOutput": "fallback value"}}
    await executor._mark_completed("ex1", final_state)  # pyright: ignore[reportPrivateUsage]

    assert db.workflowexecution.update.await_args is not None
    update_kwargs = db.workflowexecution.update.await_args.kwargs["data"]
    assert update_kwargs["output"].data == "fallback value"


async def test_mark_completed_persists_dict_when_multiple_ends_fire() -> None:
    """Genuinely multiple End nodes contributing -- output is the full
    dict, keyed by End node id, not a single collapsed scalar."""
    db = MagicMock()
    db.workflowexecution = MagicMock()
    db.workflowexecution.update = AsyncMock()
    executor = LangGraphExecutor(db=db, checkpointer=MemorySaver())

    final_state = {
        "final_outputs": {"end-a": "value-a", "end-b": "value-b"},
        "variables": {"lastOutput": "should not be used"},
    }
    await executor._mark_completed("ex1", final_state)  # pyright: ignore[reportPrivateUsage]

    assert db.workflowexecution.update.await_args is not None
    update_kwargs = db.workflowexecution.update.await_args.kwargs["data"]
    assert update_kwargs["output"].data == {"end-a": "value-a", "end-b": "value-b"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/engine/test_langgraph_executor.py -v -k mark_completed`

Expected: FAIL — `_mark_completed` still reads `final_vars["finalOutput"]`, which is never set by the new `final_state` shape.

- [ ] **Step 3: Update `_mark_completed`**

In `src/engine/langgraph_executor.py`, find `_mark_completed` (currently):

```python
    async def _mark_completed(self, execution_id: str, final_state: dict[str, Any]) -> None:
        """Persist completed status + output/variables/nodeResults."""
        final_vars: dict[str, Any] = final_state.get("variables") or {}
        await self.db.workflowexecution.update(  # pyright: ignore[reportAttributeAccessIssue]
            where={"id": execution_id},
            data={
                "status": "completed",
                # `finalOutput` is an explicit override if a node sets it;
                # otherwise surface whatever the last node produced so the
                # execution panel never shows an empty "Final output" on a
                # successful run. Presence check (not `or`) so a
                # legitimately falsy finalOutput (0, False, "", [], {})
                # isn't discarded in favor of lastOutput (P0-7).
                "output": Json(
                    final_vars["finalOutput"]
                    if "finalOutput" in final_vars
                    else final_vars.get("lastOutput")
                ),
                "variables": Json(final_vars),
                "nodeResults": Json(final_state.get("node_results") or {}),
                "completedAt": datetime.now(UTC),
            },
        )
```

Replace with:

```python
    async def _mark_completed(self, execution_id: str, final_state: dict[str, Any]) -> None:
        """Persist completed status + output/variables/nodeResults."""
        final_vars: dict[str, Any] = final_state.get("variables") or {}
        final_outputs: dict[str, Any] = final_state.get("final_outputs") or {}
        # final_outputs is keyed by End-node id (see src/executors/end.py).
        # The common case is exactly one End node -- collapse to a plain
        # scalar so the persisted `output` is byte-for-byte identical to
        # every existing single-End workflow's historical behavior.
        # Genuinely multiple End nodes (e.g. independent parallel
        # branches, or if-else branches each with their own End)
        # persist the full dict instead of picking one arbitrarily.
        # Tuple-unpack / falsy checks (not `or`) so a legitimately falsy
        # value (0, False, "", [], {}) isn't discarded (P0-7).
        if len(final_outputs) == 1:
            (output_value,) = final_outputs.values()
        elif len(final_outputs) > 1:
            output_value = final_outputs
        else:
            output_value = final_vars.get("lastOutput")
        await self.db.workflowexecution.update(  # pyright: ignore[reportAttributeAccessIssue]
            where={"id": execution_id},
            data={
                "status": "completed",
                "output": Json(output_value),
                "variables": Json(final_vars),
                "nodeResults": Json(final_state.get("node_results") or {}),
                "completedAt": datetime.now(UTC),
            },
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/engine/test_langgraph_executor.py -v`

Expected: PASS (all tests, including every pre-existing one — `test_run_completes_start_to_end` and others that run a real single-End workflow end-to-end are unaffected since the collapse-to-scalar logic preserves their exact output value)

- [ ] **Step 5: Run the full backend test suite and lint/format checks**

Run: `.venv/Scripts/python.exe -m pytest -m "not integration" -q && .venv/Scripts/python.exe -m ruff check src tests && .venv/Scripts/python.exe -m ruff format --check src tests`

Expected: all clean — this closes out every test that was failing after Tasks 1-2.

- [ ] **Step 6: Commit**

```bash
git add src/engine/langgraph_executor.py tests/unit/engine/test_langgraph_executor.py
git commit -m "$(cat <<'EOF'
fix(langgraph-executor): derive persisted output from final_outputs

Collapses a single-entry final_outputs dict to a plain scalar (byte-
for-byte identical to every existing single-End workflow's historical
output), and persists the full dict only when genuinely multiple End
nodes contributed -- the WorkflowExecution.output column is already
Json?, so this is a value-shape change, not a schema migration.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: New `join` node type

**Files:**
- Modify: `src/engine/workflow.py`
- Create: `src/executors/join.py`
- Modify: `src/engine/graph_builder.py` (side-effect import registration)
- Test: `tests/unit/executors/test_join.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/executors/test_join.py`:

```python
"""Tests for the Join node executor."""

import pytest

from src.engine.state import initial_state
from src.engine.workflow import JoinNode
from src.executors.join import JoinExecutor


@pytest.fixture
def join_node() -> JoinNode:
    return JoinNode.model_validate(
        {
            "id": "j",
            "type": "join",
            "position": {"x": 100, "y": 0},
            "data": {"label": "Join"},
        }
    )


async def test_join_returns_minimal_delta(join_node: JoinNode) -> None:
    state = initial_state()
    delta = await JoinExecutor(join_node).arun(state)
    assert delta == {
        "current_node_id": "j",
        "node_results": {"j": {"node_id": "j", "status": "completed"}},
    }


async def test_join_is_registered() -> None:
    from src.executors.base import build_executor

    node = JoinNode.model_validate(
        {"id": "j", "type": "join", "position": {"x": 0, "y": 0}, "data": {"label": "J"}}
    )
    executor = build_executor(node)
    assert isinstance(executor, JoinExecutor)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/executors/test_join.py -v`

Expected: FAIL — `ImportError: cannot import name 'JoinNode' from 'src.engine.workflow'`.

- [ ] **Step 3: Add the schema**

In `src/engine/workflow.py`, add this block immediately after the `EndNode` class (currently lines 77-85, right before the `# ─── note` comment):

```python
# ─── join ────────────────────────────────────────────────────────────────


class JoinNodeData(BaseNodeData):
    pass


class JoinNode(BaseModel):
    id: str
    type: Literal["join"]
    position: Position
    data: JoinNodeData
```

Add `| JoinNode` to the end of the `WorkflowNode` union (currently ending `| DownloadPdfNode,`):

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
    | ConfluenceNode
    | JiraNode
    | DownloadPdfNode
    | JoinNode,
    Field(discriminator="type"),
]
```

Add `"JoinNode"` and `"JoinNodeData"` to `__all__`, in alphabetical position right after `"JoinChunksNodeData"` and before `"McpNode"`.

- [ ] **Step 4: Create the executor**

Create `src/executors/join.py`:

```python
"""JoinExecutor -- the `join` node type.

Explicit convergence point for parallel branches: any number of
incoming edges, exactly one outgoing edge (enforced by
validate_workflow_shape in src/engine/graph_builder.py). Purely
structural -- concurrent branches' state already merges correctly via
merge_dict once LangGraph reaches this node in the same superstep, the
same proven node_results/variables merge behavior parallel branches
already rely on today -- so this executor does no work beyond standard
node bookkeeping.
"""

from typing import Any

from src.engine.state import WorkflowStateDict
from src.engine.workflow import JoinNode
from src.executors.base import register_executor


@register_executor("join")
class JoinExecutor:
    def __init__(self, node: JoinNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        return {
            "current_node_id": self.node.id,
            "node_results": {self.node.id: {"node_id": self.node.id, "status": "completed"}},
        }


__all__ = ["JoinExecutor"]
```

- [ ] **Step 5: Register in the graph builder**

In `src/engine/graph_builder.py`, insert this import between the existing `jira` and `join_chunks` imports (currently lines 67-72):

```python
from src.executors import (
    jira as _jira_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from src.executors import (
    join as _join_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from src.executors import (
    join_chunks as _join_chunks_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/executors/test_join.py -v`

Expected: PASS

- [ ] **Step 7: Run the full backend test suite and lint/format checks**

Run: `.venv/Scripts/python.exe -m pytest -m "not integration" -q && .venv/Scripts/python.exe -m ruff check src tests && .venv/Scripts/python.exe -m ruff format --check src tests`

Expected: all clean

- [ ] **Step 8: Commit**

```bash
git add src/engine/workflow.py src/executors/join.py src/engine/graph_builder.py tests/unit/executors/test_join.py
git commit -m "$(cat <<'EOF'
feat(join-node): add the join node type

A real, non-visual-only structural node: N incoming edges, exactly one
outgoing edge (validated in the next task). Gives designers an
explicit, self-documenting way to converge parallel branches instead
of relying on the implicit "multiple edges happen to target the same
node" pattern. Purely a pass-through executor -- concurrent branches'
state already merges correctly by the time LangGraph reaches this node.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Validation — `End` needs exactly one incoming edge, `join` needs exactly one outgoing edge

**Files:**
- Modify: `src/engine/graph_builder.py`
- Test: `tests/unit/engine/test_graph_builder.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/engine/test_graph_builder.py` (this file already has a `_mk(nodes, edges)` helper and imports `WorkflowValidationError`/`validate_workflow_shape` — reuse them, do not redefine):

```python
def test_end_with_zero_incoming_edges_fails() -> None:
    wf = _mk(
        nodes=[
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {"id": "e1", "type": "end", "position": {"x": 1, "y": 0}, "data": {"label": "E1"}},
            {"id": "e2", "type": "end", "position": {"x": 2, "y": 0}, "data": {"label": "E2"}},
        ],
        edges=[{"id": "edge1", "source": "s", "target": "e1"}],
    )
    with pytest.raises(WorkflowValidationError, match=r"End node 'e2' must have exactly one incoming edge"):
        validate_workflow_shape(wf)


def test_end_with_two_incoming_edges_fails() -> None:
    wf = _mk(
        nodes=[
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {
                "id": "a",
                "type": "set-state",
                "position": {"x": 1, "y": 0},
                "data": {"label": "A", "stateKey": "x", "stateValue": "1"},
            },
            {
                "id": "b",
                "type": "set-state",
                "position": {"x": 1, "y": 1},
                "data": {"label": "B", "stateKey": "y", "stateValue": "2"},
            },
            {"id": "e", "type": "end", "position": {"x": 2, "y": 0}, "data": {"label": "E"}},
        ],
        edges=[
            {"id": "e1", "source": "s", "target": "a"},
            {"id": "e2", "source": "s", "target": "b"},
            {"id": "e3", "source": "a", "target": "e"},
            {"id": "e4", "source": "b", "target": "e"},
        ],
    )
    with pytest.raises(WorkflowValidationError, match=r"End node 'e' must have exactly one incoming edge"):
        validate_workflow_shape(wf)


def test_end_note_edge_does_not_count_toward_incoming_tally() -> None:
    wf = _mk(
        nodes=[
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {"id": "e", "type": "end", "position": {"x": 1, "y": 0}, "data": {"label": "E"}},
            {"id": "n", "type": "note", "position": {"x": -1, "y": 0}, "data": {"label": "N"}},
        ],
        edges=[
            {"id": "e1", "source": "s", "target": "e"},
            {"id": "e2", "source": "n", "target": "e"},
        ],
    )
    validate_workflow_shape(wf)  # must not raise -- the Note edge is decoration, not counted


def test_join_with_zero_outgoing_edges_fails() -> None:
    wf = _mk(
        nodes=[
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {"id": "j", "type": "join", "position": {"x": 1, "y": 0}, "data": {"label": "J"}},
            {"id": "e", "type": "end", "position": {"x": 2, "y": 0}, "data": {"label": "E"}},
        ],
        edges=[{"id": "e1", "source": "s", "target": "j"}],
    )
    with pytest.raises(WorkflowValidationError, match=r"Join node 'j' must have exactly one outgoing edge"):
        validate_workflow_shape(wf)


def test_join_with_two_outgoing_edges_fails() -> None:
    wf = _mk(
        nodes=[
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {"id": "j", "type": "join", "position": {"x": 1, "y": 0}, "data": {"label": "J"}},
            {"id": "e1", "type": "end", "position": {"x": 2, "y": 0}, "data": {"label": "E1"}},
            {"id": "e2", "type": "end", "position": {"x": 2, "y": 1}, "data": {"label": "E2"}},
        ],
        edges=[
            {"id": "e1e", "source": "s", "target": "j"},
            {"id": "e2e", "source": "j", "target": "e1"},
            {"id": "e3e", "source": "j", "target": "e2"},
        ],
    )
    with pytest.raises(WorkflowValidationError, match=r"Join node 'j' must have exactly one outgoing edge"):
        validate_workflow_shape(wf)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/engine/test_graph_builder.py -v -k "end_with or join_with or note_edge"`

Expected: FAIL — none of these checks exist yet in `validate_workflow_shape`, so no `WorkflowValidationError` is raised for any of them.

- [ ] **Step 3: Add the validation checks**

In `src/engine/graph_builder.py`'s `validate_workflow_shape` (currently):

```python
    end_ids = [n.id for n in workflow.nodes if n.type == "end"]
    if not end_ids:
        raise WorkflowValidationError("Workflow must contain at least one end node.")

    _check_edges(workflow.edges, set(nodes.keys()))
```

Replace with:

```python
    end_ids = [n.id for n in workflow.nodes if n.type == "end"]
    if not end_ids:
        raise WorkflowValidationError("Workflow must contain at least one end node.")

    for end_id in end_ids:
        incoming = [
            e
            for e in workflow.edges
            if e.target == end_id and nodes[e.source].type not in _VISUAL_ONLY_TYPES
        ]
        if len(incoming) != 1:
            raise WorkflowValidationError(
                f"End node {end_id!r} must have exactly one incoming edge from an "
                f"executable node (found {len(incoming)}). Converge multiple branches "
                "through a join node before reaching a single End."
            )

    join_ids = [n.id for n in workflow.nodes if n.type == "join"]
    for join_id in join_ids:
        outgoing = [
            e
            for e in workflow.edges
            if e.source == join_id and nodes[e.target].type not in _VISUAL_ONLY_TYPES
        ]
        if len(outgoing) != 1:
            raise WorkflowValidationError(
                f"Join node {join_id!r} must have exactly one outgoing edge (found {len(outgoing)})."
            )

    _check_edges(workflow.edges, set(nodes.keys()))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/engine/test_graph_builder.py -v`

Expected: PASS (all tests, including every pre-existing one — every existing workflow fixture in this file already has exactly one `End` node with exactly one incoming edge, so this new rule doesn't newly reject anything that previously passed)

- [ ] **Step 5: Run the full backend test suite and lint/format checks**

Run: `.venv/Scripts/python.exe -m pytest -m "not integration" -q && .venv/Scripts/python.exe -m ruff check src tests && .venv/Scripts/python.exe -m ruff format --check src tests`

Expected: all clean. If any *other* test file's fixture workflow has an `End` node with more than one incoming edge, it will now fail here — if so, that fixture needs a `join` node inserted before its `End`, following the same pattern as this task's own passing tests; do not weaken the new validation rule to make a fixture pass.

- [ ] **Step 6: Commit**

```bash
git add src/engine/graph_builder.py tests/unit/engine/test_graph_builder.py
git commit -m "$(cat <<'EOF'
feat(graph-builder): require exactly one incoming edge per End, one outgoing edge per join

Closes the loop on the join node added in the previous task: designers
can no longer have multiple branches converge directly onto a shared
End node (the exact topology that used to race silently) -- they route
through an explicit join first. if-else/while branches with a
different End per branch are unaffected, since each branch target
already has exactly one edge. Visual-only Note/File-Trigger edges are
excluded from both counts, matching how the rest of this file already
treats them as non-executable decoration.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Definitive fan-out/fan-in proof tests

**Files:**
- Test: `tests/unit/engine/test_graph_builder.py`

These are the tests that actually close the P0-8 gap: real, compiled graphs run end-to-end via `ainvoke`, not just validation checks.

- [ ] **Step 1: Write the tests**

Append to `tests/unit/engine/test_graph_builder.py`:

```python
async def test_fan_out_through_join_converges_safely() -> None:
    """The recommended pattern: two parallel branches converge through an
    explicit join node before reaching a single End. Both branches use
    the IDENTICAL stateValue deliberately: set-state also aliases its
    value into variables.lastOutput (see src/executors/set_state.py),
    so two branches racing on THAT shared key would be a separate,
    already-flagged, out-of-scope concern if they wrote DIFFERENT
    values -- using the same value keeps this test a clean, honest proof
    of only what this fix claims: that both branches' OWN distinctly-
    named variables survive the merge with no data loss."""
    from langgraph.checkpoint.memory import MemorySaver

    from src.engine.graph_builder import build_graph
    from src.engine.state import initial_state

    wf = _mk(
        nodes=[
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {
                "id": "a",
                "type": "set-state",
                "position": {"x": 1, "y": 0},
                "data": {"label": "A", "stateKey": "branch_a_ran", "stateValue": "yes"},
            },
            {
                "id": "b",
                "type": "set-state",
                "position": {"x": 1, "y": 1},
                "data": {"label": "B", "stateKey": "branch_b_ran", "stateValue": "yes"},
            },
            {"id": "j", "type": "join", "position": {"x": 2, "y": 0}, "data": {"label": "J"}},
            {"id": "e", "type": "end", "position": {"x": 3, "y": 0}, "data": {"label": "E"}},
        ],
        edges=[
            {"id": "e1", "source": "s", "target": "a"},
            {"id": "e2", "source": "s", "target": "b"},
            {"id": "e3", "source": "a", "target": "j"},
            {"id": "e4", "source": "b", "target": "j"},
            {"id": "e5", "source": "j", "target": "e"},
        ],
    )
    compiled = build_graph(wf, MemorySaver())
    result = await compiled.ainvoke(initial_state(), config={"configurable": {"thread_id": "t1"}})

    # Both branches' distinctly-named work survived the merge into the
    # single join/End path -- no data loss on the variables side.
    assert result["variables"]["branch_a_ran"] == "yes"
    assert result["variables"]["branch_b_ran"] == "yes"
    assert result["final_outputs"] == {"e": "yes"}
    assert "j" in result["node_results"]
    assert "e" in result["node_results"]


async def test_fan_out_to_independent_ends_captures_both_outputs() -> None:
    """Two branches never reconverge -- each has its own single-incoming-
    edge End (still legal; see Task 5). Proves final_outputs captures
    BOTH branches' outputs with no data loss, even though both End
    nodes fire in the same LangGraph superstep -- this is the exact
    topology that used to silently lose one branch's output before this
    fix. Both branches deliberately use the SAME stateValue for the same
    reason as the join test above: this isolates the proof to the
    final_outputs write-collision fix under test, without also
    depending on the separate, out-of-scope variables.lastOutput
    collision that would occur if the branches wrote DIFFERENT values."""
    from langgraph.checkpoint.memory import MemorySaver

    from src.engine.graph_builder import build_graph
    from src.engine.state import initial_state

    wf = _mk(
        nodes=[
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {
                "id": "a",
                "type": "set-state",
                "position": {"x": 1, "y": 0},
                "data": {"label": "A", "stateKey": "a_marker", "stateValue": "shared-value"},
            },
            {
                "id": "b",
                "type": "set-state",
                "position": {"x": 1, "y": 1},
                "data": {"label": "B", "stateKey": "b_marker", "stateValue": "shared-value"},
            },
            {"id": "end-a", "type": "end", "position": {"x": 2, "y": 0}, "data": {"label": "EA"}},
            {"id": "end-b", "type": "end", "position": {"x": 2, "y": 1}, "data": {"label": "EB"}},
        ],
        edges=[
            {"id": "e1", "source": "s", "target": "a"},
            {"id": "e2", "source": "s", "target": "b"},
            {"id": "e3", "source": "a", "target": "end-a"},
            {"id": "e4", "source": "b", "target": "end-b"},
        ],
    )
    compiled = build_graph(wf, MemorySaver())
    result = await compiled.ainvoke(initial_state(), config={"configurable": {"thread_id": "t1"}})

    # Both End nodes' own contributions are present -- this is the exact
    # assertion that would have been flaky/lossy before this fix (one of
    # the two keys would sometimes be missing, depending on LangGraph's
    # unspecified concurrent-write application order).
    assert result["final_outputs"] == {"end-a": "shared-value", "end-b": "shared-value"}
    assert result["variables"]["a_marker"] == "shared-value"
    assert result["variables"]["b_marker"] == "shared-value"
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/engine/test_graph_builder.py -v -k "fan_out"`

Expected: PASS. (These tests exercise genuinely new behavior end-to-end, so there is no separate "run to verify it fails first" step in the usual TDD sense here beyond Tasks 1-5 already having been red/green individually — this task is the integration-level proof that the pieces work together.)

- [ ] **Step 3: Run the full backend test suite and lint/format checks**

Run: `.venv/Scripts/python.exe -m pytest -m "not integration" -q && .venv/Scripts/python.exe -m ruff check src tests && .venv/Scripts/python.exe -m ruff format --check src tests`

Expected: all clean

- [ ] **Step 4: Commit**

```bash
git add tests/unit/engine/test_graph_builder.py
git commit -m "$(cat <<'EOF'
test(graph-builder): prove fan-out/fan-in is now safe end-to-end

Two real, compiled-and-executed graphs: parallel branches converging
through a join into one End, and parallel branches independently
reaching their own Ends. Both assert no data loss in final_outputs --
this is the topology that used to silently race before this plan's
fix, now proven deterministic rather than merely asserted to compile.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Designer UI registration for `join`

**Files:**
- Create: `frontend/components/composer/canvas/node-panels/join.tsx`
- Modify: `frontend/components/composer/canvas/property-panel.tsx`
- Modify: `frontend/components/composer/canvas/tools-palette.tsx`
- Modify: `frontend/components/composer/canvas/node-visuals.ts`
- Modify: `frontend/components/composer/canvas/workflow-canvas.tsx`

`end.tsx` has no dedicated test file in this codebase (its panel is equally static/unconfigurable) — `join.tsx` follows the same precedent, no new test file for this task.

- [ ] **Step 1: Create the panel**

Create `frontend/components/composer/canvas/node-panels/join.tsx`:

```tsx
"use client";

export default function JoinPanel(_props: {
  data: Record<string, unknown>;
  onChange: (patch: Record<string, unknown>) => void;
  currentNodeId?: string;
}) {
  return (
    <p className="text-sm text-muted-foreground">
      Join has no configurable fields — it merges multiple incoming branches into a single
      path forward. Connect it to exactly one downstream node.
    </p>
  );
}
```

- [ ] **Step 2: Register in `property-panel.tsx`**

Add the import, alphabetically near the other node-panel imports (after the `download-pdf` import):

```tsx
import DownloadPdfPanel from "./node-panels/download-pdf";
import JoinPanel from "./node-panels/join";
```

Add to `PANEL_MAP`:

```tsx
  "download-pdf": DownloadPdfPanel,
  join: JoinPanel,
```

Add to `TYPE_LABELS`:

```tsx
  "download-pdf": "Download PDF",
  join: "Join",
```

- [ ] **Step 3: Register in the palette — `tools-palette.tsx`**

Add to `COMPOSER_NODE_PALETTE` (after the `download-pdf` entry):

```tsx
  { nodeType: "download-pdf", label: "Download PDF" },
  { nodeType: "join", label: "Join" },
```

- [ ] **Step 4: Add a visual identity — `node-visuals.ts`**

Add `GitMerge` to the `lucide-react` import list (it already imports `GitBranch`, confirmed available in the installed `lucide-react` version):

```ts
import {
  BookOpen,
  Bot,
  CheckCircle2,
  Combine,
  Database,
  FileDown,
  FileSearch,
  FolderInput,
  FolderOutput,
  Mail,
  Gamepad2,
  GitBranch,
  GitMerge,
  Globe,
  HelpCircle,
  Layers,
  ListTodo,
  PlayCircle,
  Plug,
  Presentation,
  Repeat,
  Shield,
  Sparkles,
  StickyNote,
  StopCircle,
  Variable,
  type LucideIcon,
} from "lucide-react";
```

Add an entry to `NODE_VISUALS` (after the `download-pdf` entry):

```ts
  "download-pdf": {
    icon: FileDown,
    iconWrapClass: "bg-rose-100 text-rose-700",
    accent: "text-rose-700",
    label: "Download PDF",
  },
  join: {
    icon: GitMerge,
    iconWrapClass: "bg-purple-100 text-purple-700",
    accent: "text-purple-700",
    label: "Join",
  },
};
```

- [ ] **Step 5: Register in `workflow-canvas.tsx`**

Add to `COMPOSER_NODE_TYPES` (after the `download-pdf` entry):

```tsx
  "download-pdf": InnerNode,
  join: InnerNode,
};
```

- [ ] **Step 6: Run the full frontend test suite and type check**

Run (from `frontend/`): `node_modules/.bin/vitest run && node_modules/.bin/tsc --noEmit`

Expected: PASS, no regressions, no new type errors

- [ ] **Step 7: Commit**

```bash
git add frontend/components/composer/canvas/node-panels/join.tsx frontend/components/composer/canvas/property-panel.tsx frontend/components/composer/canvas/tools-palette.tsx frontend/components/composer/canvas/node-visuals.ts frontend/components/composer/canvas/workflow-canvas.tsx
git commit -m "$(cat <<'EOF'
feat(join-node): register the node type on the canvas

Palette entry, property-panel dispatch (static "nothing to configure"
panel, mirroring end.tsx's pattern exactly), visual identity (GitMerge
icon, purple accent), and ReactFlow node-type registration -- the join
node is now fully usable from the Designer, not just via raw workflow
JSON.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-review notes

- **Spec coverage:** Component 1 (state field) → Task 1. Component 2 (`EndExecutor`) → Task 2. Component 3 (`_mark_completed`) → Task 3. Component 4 (`join` node type) → Task 4. Component 5 (validation) → Task 5. Component 6 (Designer UI) → Task 7. The spec's Testing section's "definitive fan-out/fan-in proof" and "multiple-independent-Ends proof" → Task 6.
- **Placeholder scan:** none found — every step has complete code.
- **Type consistency:** `final_outputs` (state field, Task 1) matches exactly across `EndExecutor` (Task 2), `_mark_completed` (Task 3), and both proof tests (Task 6). `JoinNodeData`/`JoinNode`/`JoinExecutor` names match exactly between Task 4's schema, executor, and tests, and Task 7's frontend registration uses the same `"join"` type string throughout. `WorkflowValidationError`'s exact error-message wording in Task 5's implementation matches the `match=` regexes in its own tests precisely.
- **Ordering dependency check:** Task 2 depends on Task 1 (state field must exist first); Task 3 depends on Task 1 (reads `final_outputs`); Task 5 depends on Task 4 (validates the `join` type, which must be a recognized node type first); Task 6 depends on Tasks 1-5 all being in place (exercises the full system end-to-end); Task 7 (frontend) has no backend runtime dependency but is sequenced last to match this session's established backend-then-frontend convention. Tasks must be executed in the order presented.
