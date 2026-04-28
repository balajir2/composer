# Phase 4b — Control-flow Executors: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `if-else` and `while` node types + conditional-edge emission in `graph_builder.py` + one ADR locking the `branch` label onto `WorkflowEdge`.

**Architecture.** Each executor produces a state delta and records the evaluated condition; a separate router function (built by `graph_builder`) calls `simpleeval.evaluate` and returns a branch key that LangGraph uses to pick the outgoing edge. `WorkflowEdge` gains `branch: str | None`; `graph_builder` validates the edges-out set against the required branch set per source type.

**Tech Stack:** Python 3.11/3.12, LangGraph `add_conditional_edges`, Phase 4a's simpleeval wrapper, pytest + pytest-asyncio, Prisma Python for integration tests.

**Spec:** [`docs/superpowers/specs/2026-04-21-phase-4b-control-flow-design.md`](../specs/2026-04-21-phase-4b-control-flow-design.md)
**ADR:** [ADR-0013](../../design/decisions.md#adr-0013-conditional-edges-carry-a-branch-label-on-workflowedge)

---

## Sequencing and discipline

9 tasks, one commit each. Every task ends green on:

```bash
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m ruff format --check src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

Integration tests (Tasks 6 + 7) + regression (Task 8) run at phase-exit (Task 9) with `TEST_DATABASE_URL` pointing at Neon. No LLM needed for 4b's integration tests — pure state routing.

**⚠️ Forbidden files (all tasks except where explicitly noted):** `pyproject.toml`, `.github/workflows/*`, `CLAUDE.md` (except Task 9), `docs/design/*` (except Task 9 for ADR backfill), `docs/superpowers/plans/*`, `docs/superpowers/specs/*`. Fix pyright strict errors inline with `# pyright: ignore[specific]` — never loosen `pyproject.toml`.

Commit footer every commit:
```
Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
```

Stay on `main`. No feature branches.

---

## Task 1: `WorkflowEdge` gains `branch: str | None`

**Files:**
- Modify: `src/engine/workflow.py`
- Modify: `tests/unit/engine/test_workflow_models.py` (add tests for the new field + its exports)

- [ ] **Step 1: Append failing tests**

Read `tests/unit/engine/test_workflow_models.py` first to see existing style. Append these tests:

```python
def test_workflow_edge_branch_defaults_none() -> None:
    from src.engine.workflow import WorkflowEdge

    edge = WorkflowEdge.model_validate({"id": "e1", "source": "a", "target": "b"})
    assert edge.branch is None


def test_workflow_edge_branch_accepts_string() -> None:
    from src.engine.workflow import WorkflowEdge

    edge = WorkflowEdge.model_validate(
        {"id": "e1", "source": "x", "target": "y", "branch": "true"}
    )
    assert edge.branch == "true"


def test_workflow_edge_branch_round_trip_json() -> None:
    from src.engine.workflow import WorkflowEdge

    edge = WorkflowEdge.model_validate(
        {"id": "e1", "source": "x", "target": "y", "branch": "body"}
    )
    dumped = edge.model_dump(mode="json")
    assert dumped["branch"] == "body"
```

- [ ] **Step 2: Verify fail**

```bash
.venv/Scripts/python -m pytest tests/unit/engine/test_workflow_models.py -v
```
Expected: the three new tests fail — `branch` attribute doesn't exist on `WorkflowEdge`.

- [ ] **Step 3: Implement**

Read `src/engine/workflow.py`. Find the `WorkflowEdge` class. Add the new field:

```python
class WorkflowEdge(BaseModel):
    id: str
    source: str
    target: str
    # Phase 4b: identifies which conditional branch this edge represents.
    # Required when source is if-else or while; forbidden otherwise.
    branch: str | None = None
```

(Keep whatever other existing fields are there — `source_handle`, `target_handle`, etc. — untouched.)

- [ ] **Step 4: Run + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/engine/test_workflow_models.py -v
.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```
Expected: 3 new + all existing pass; overall ~299 (296 baseline + 3 new).

```bash
git add src/engine/workflow.py tests/unit/engine/test_workflow_models.py
git commit -m "feat(schema): WorkflowEdge.branch — conditional-edge label (Phase 4b)

Per ADR-0013, conditional-source edges (from if-else / while nodes)
carry a \`branch: str | None\` identifying which branch they represent:
  - if-else edges: branch ∈ {'true', 'false'}
  - while edges:   branch ∈ {'body', 'exit'}
  - normal edges:  branch is None

Validation (enforced by graph_builder, Task 5) happens at compile time
— missing / duplicate / unexpected branch labels raise
WorkflowValidationError before any execution starts.

Back-compatible: pre-4b workflows (no branch field) still validate as
their defaults null out.

See Phase 4b spec §6, ADR-0013.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `if_else` executor

**Files:**
- Create: `src/executors/if_else.py`
- Modify: `src/engine/graph_builder.py` (side-effect import)
- Create: `tests/unit/executors/test_if_else_executor.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/executors/test_if_else_executor.py`:

```python
"""Tests for the if-else executor."""

from typing import Any

import pytest

from src.engine.state import initial_state
from src.engine.workflow import IfElseNode
from src.executors.if_else import IfElseExecutor, IfElseNodeError


def _if_else_node(**data: Any) -> IfElseNode:
    return IfElseNode.model_validate(
        {
            "id": "ie",
            "type": "if-else",
            "position": {"x": 0, "y": 0},
            "data": {"label": "IE", **data},
        }
    )


async def test_if_else_truthy_condition() -> None:
    node = _if_else_node(condition="variables['x'] > 0")
    state = initial_state()
    state["variables"]["x"] = 5
    delta = await IfElseExecutor(node).arun(state)
    assert delta["current_node_id"] == "ie"
    assert delta["node_results"]["ie"]["status"] == "completed"
    assert delta["node_results"]["ie"]["output"]["taken"] == "true"
    assert delta["node_results"]["ie"]["output"]["evaluated"] is True


async def test_if_else_falsy_condition() -> None:
    node = _if_else_node(condition="variables['x'] > 0")
    state = initial_state()
    state["variables"]["x"] = -1
    delta = await IfElseExecutor(node).arun(state)
    assert delta["node_results"]["ie"]["output"]["taken"] == "false"
    assert delta["node_results"]["ie"]["output"]["evaluated"] is False


async def test_if_else_missing_condition_raises() -> None:
    node = _if_else_node()  # no condition
    with pytest.raises(ValueError, match="condition"):
        await IfElseExecutor(node).arun(initial_state())


async def test_if_else_eval_error_wraps() -> None:
    node = _if_else_node(condition="ghost + 1")
    with pytest.raises(IfElseNodeError):
        await IfElseExecutor(node).arun(initial_state())


async def test_if_else_executor_is_registered() -> None:
    from src.executors.base import build_executor
    import src.executors.if_else  # noqa: F401  # pyright: ignore[reportUnusedImport]
    node = _if_else_node(condition="True")
    executor = build_executor(node)
    assert isinstance(executor, IfElseExecutor)
```

- [ ] **Step 2: Verify fail**

```bash
.venv/Scripts/python -m pytest tests/unit/executors/test_if_else_executor.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implement `src/executors/if_else.py`**

```python
"""if-else node executor.

Evaluates a simpleeval boolean condition and records which branch will be
taken.  The actual routing decision is made by a router closure installed
into the LangGraph StateGraph by `graph_builder._route_if_else`; the
executor's job is to produce an audit row and populate node_results.

See Phase 4b spec §7.
"""

from typing import Any

from src.engine.state import WorkflowStateDict
from src.engine.workflow import IfElseNode
from src.executors._eval import EvalError, evaluate
from src.executors.base import register_executor


class IfElseNodeError(RuntimeError):
    """Wraps EvalError with node-id context."""


@register_executor("if-else")
class IfElseExecutor:
    def __init__(self, node: IfElseNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        expr = self.node.data.condition
        if not expr:
            raise ValueError(f"if-else node {self.node.id!r} requires condition")
        try:
            result = evaluate(expr, state)
        except EvalError as exc:
            raise IfElseNodeError(f"if-else node {self.node.id!r}: {exc}") from exc

        taken = "true" if result else "false"
        return {
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "input": {"condition": expr},
                    "output": {"taken": taken, "evaluated": bool(result)},
                }
            },
        }


__all__ = ["IfElseExecutor", "IfElseNodeError"]
```

- [ ] **Step 4: Register side-effect import in `graph_builder.py`**

Read the file. Existing side-effect imports (post-Phase 4a) include `agent`, `data_transform`, `end`, `extract`, `http`, `mcp`, `set_state`, `start`, `transform`. Add `if_else` alphabetically (between `http` and `mcp`):

```python
from src.executors import (
    if_else as _if_else_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
```

Ruff will reformat if needed.

- [ ] **Step 5: Run + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/executors/test_if_else_executor.py -v
.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```
Expected: 5 new pass; overall ~304 (299 + 5).

```bash
git add src/executors/if_else.py src/engine/graph_builder.py \
        tests/unit/executors/test_if_else_executor.py
git commit -m "feat(executors): if-else — routing executor (Phase 4b)

Evaluates a simpleeval boolean condition via the Phase 4a _eval wrapper
and records the branch decision in node_results[\"output\"][\"taken\"]
(either 'true' or 'false') for audit.

The actual next-node selection is done by the router function the
graph_builder emits (Task 5) — this executor just makes the decision
observable in the execution record.

Missing condition → ValueError. EvalError wraps as IfElseNodeError
with node-id context.

See Phase 4b spec §7.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `while` executor

**Files:**
- Create: `src/executors/while_loop.py`
- Modify: `src/engine/graph_builder.py` (side-effect import)
- Create: `tests/unit/executors/test_while_executor.py`

Verify `WhileNodeData` in `src/engine/workflow.py` first. It should already have:
```python
class WhileNodeData(BaseNodeData):
    condition: str | None = None
    max_iterations: int = Field(default=100, alias="maxIterations")
```
If the field is declared with a different name (e.g., `loop_condition` or no `max_iterations`), amend it to match the spec before continuing. Note any amendment in the commit message.

- [ ] **Step 1: Write failing tests**

Create `tests/unit/executors/test_while_executor.py`:

```python
"""Tests for the while executor."""

from typing import Any

import pytest

from src.engine.state import initial_state
from src.engine.workflow import WhileNode
from src.executors.while_loop import (
    WhileExecutor,
    WhileMaxIterationsError,
    WhileNodeError,
)


def _while_node(**data: Any) -> WhileNode:
    return WhileNode.model_validate(
        {
            "id": "w",
            "type": "while",
            "position": {"x": 0, "y": 0},
            "data": {"label": "W", **data},
        }
    )


async def test_while_condition_true_routes_body() -> None:
    node = _while_node(condition="variables['counter'] > 0")
    state = initial_state()
    state["variables"]["counter"] = 3
    delta = await WhileExecutor(node).arun(state)
    assert delta["node_results"]["w"]["output"]["taken"] == "body"
    assert delta["node_results"]["w"]["output"]["evaluated"] is True


async def test_while_condition_false_routes_exit() -> None:
    node = _while_node(condition="variables['counter'] > 0")
    state = initial_state()
    state["variables"]["counter"] = 0
    delta = await WhileExecutor(node).arun(state)
    assert delta["node_results"]["w"]["output"]["taken"] == "exit"


async def test_while_iteration_counter_bumps() -> None:
    node = _while_node(condition="True")
    state = initial_state()
    # First pass
    delta1 = await WhileExecutor(node).arun(state)
    assert delta1["variables"]["_while_iterations"]["w"] == 1
    # Simulate state after LangGraph merges the delta
    state["variables"]["_while_iterations"] = delta1["variables"]["_while_iterations"]
    delta2 = await WhileExecutor(node).arun(state)
    assert delta2["variables"]["_while_iterations"]["w"] == 2


async def test_while_max_iterations_cap_raises() -> None:
    node = _while_node(condition="True", maxIterations=3)
    state = initial_state()
    # Pre-seed the counter at cap
    state["variables"]["_while_iterations"] = {"w": 3}
    with pytest.raises(WhileMaxIterationsError, match="max_iterations=3"):
        await WhileExecutor(node).arun(state)


async def test_while_missing_condition_raises() -> None:
    node = _while_node()  # no condition
    with pytest.raises(ValueError, match="condition"):
        await WhileExecutor(node).arun(initial_state())


async def test_while_eval_error_wraps() -> None:
    node = _while_node(condition="ghost + 1")
    with pytest.raises(WhileNodeError):
        await WhileExecutor(node).arun(initial_state())


async def test_while_executor_is_registered() -> None:
    from src.executors.base import build_executor
    import src.executors.while_loop  # noqa: F401  # pyright: ignore[reportUnusedImport]
    node = _while_node(condition="True")
    executor = build_executor(node)
    assert isinstance(executor, WhileExecutor)
```

- [ ] **Step 2: Verify fail**

```bash
.venv/Scripts/python -m pytest tests/unit/executors/test_while_executor.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implement `src/executors/while_loop.py`**

```python
"""while node executor — iteration-bounded loop.

Each traversal through the while node:
  1. Bumps state.variables["_while_iterations"][node_id]
  2. Raises WhileMaxIterationsError if the count exceeds max_iterations
  3. Evaluates `condition` via _eval.evaluate; writes {"taken": "body"|"exit"}
     into node_results for audit

The actual next-node selection is made by the router closure
(graph_builder._route_while).

The loop terminates when `condition` becomes false OR the cap is hit.
Infinite loops are prevented by the cap.

See Phase 4b spec §8.
"""

from typing import Any

from src.engine.state import WorkflowStateDict
from src.engine.workflow import WhileNode
from src.executors._eval import EvalError, evaluate
from src.executors.base import register_executor

ITER_STATE_KEY = "_while_iterations"


class WhileNodeError(RuntimeError):
    """Wraps EvalError with node-id context."""


class WhileMaxIterationsError(RuntimeError):
    """Raised when a while loop exceeds its configured max_iterations."""


@register_executor("while")
class WhileExecutor:
    def __init__(self, node: WhileNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        expr = self.node.data.condition
        if not expr:
            raise ValueError(f"while node {self.node.id!r} requires condition")

        # Bump iteration counter; enforce cap before evaluating condition.
        existing = (state.get("variables") or {}).get(ITER_STATE_KEY) or {}
        counts: dict[str, int] = dict(existing)
        counts[self.node.id] = int(counts.get(self.node.id, 0)) + 1
        if counts[self.node.id] > self.node.data.max_iterations:
            raise WhileMaxIterationsError(
                f"while node {self.node.id!r} exceeded max_iterations="
                f"{self.node.data.max_iterations} (count={counts[self.node.id]})"
            )

        try:
            result = evaluate(expr, state)
        except EvalError as exc:
            raise WhileNodeError(f"while node {self.node.id!r}: {exc}") from exc

        taken = "body" if result else "exit"
        return {
            "variables": {ITER_STATE_KEY: counts},
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "input": {"condition": expr, "iteration": counts[self.node.id]},
                    "output": {"taken": taken, "evaluated": bool(result)},
                }
            },
        }


__all__ = [
    "ITER_STATE_KEY",
    "WhileExecutor",
    "WhileMaxIterationsError",
    "WhileNodeError",
]
```

- [ ] **Step 4: Register side-effect import in `graph_builder.py`**

Add alphabetically (after `transform`, at the end of the block):

```python
from src.executors import (
    while_loop as _while_loop_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
```

- [ ] **Step 5: Run + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/executors/test_while_executor.py -v
.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```
Expected: 7 new pass; overall ~311 (304 + 7).

```bash
git add src/executors/while_loop.py src/engine/graph_builder.py \
        tests/unit/executors/test_while_executor.py
git commit -m "feat(executors): while — iteration-bounded loop (Phase 4b)

Each traversal bumps variables._while_iterations[node_id] and caps at
max_iterations (default 100, configurable per-node via maxIterations).
Hit → WhileMaxIterationsError, execution fails with node id.

Evaluates condition via Phase 4a's _eval.evaluate.  Body / exit
routing decision recorded in node_results[\"output\"][\"taken\"].

EvalError wraps as WhileNodeError; missing condition → ValueError.

See Phase 4b spec §8.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: `graph_builder` — conditional-edge emission + branch validation

**Files:**
- Modify: `src/engine/graph_builder.py`
- Modify: `tests/unit/engine/test_graph_builder.py` (add new tests)

This is the load-bearing task. Read `src/engine/graph_builder.py` fully first to understand the current shape.

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/engine/test_graph_builder.py` (read the file first to understand its fixtures):

```python
def test_conditional_edges_compile_for_if_else() -> None:
    from langgraph.checkpoint.memory import MemorySaver

    from src.engine.graph_builder import build_graph
    from src.engine.workflow import Workflow

    wf = Workflow.model_validate(
        {
            "id": "w1",
            "name": "if-else test",
            "nodes": [
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                {
                    "id": "ie",
                    "type": "if-else",
                    "position": {"x": 100, "y": 0},
                    "data": {"label": "IE", "condition": "variables['x'] > 0"},
                },
                {"id": "a", "type": "end", "position": {"x": 200, "y": 0}, "data": {"label": "A"}},
                {"id": "b", "type": "end", "position": {"x": 200, "y": 100}, "data": {"label": "B"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "ie"},
                {"id": "e2", "source": "ie", "target": "a", "branch": "true"},
                {"id": "e3", "source": "ie", "target": "b", "branch": "false"},
            ],
        }
    )
    compiled = build_graph(wf, MemorySaver())
    assert compiled is not None  # graph compiled successfully


def test_conditional_edges_compile_for_while() -> None:
    from langgraph.checkpoint.memory import MemorySaver

    from src.engine.graph_builder import build_graph
    from src.engine.workflow import Workflow

    wf = Workflow.model_validate(
        {
            "id": "w1",
            "name": "while test",
            "nodes": [
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                {
                    "id": "w",
                    "type": "while",
                    "position": {"x": 100, "y": 0},
                    "data": {"label": "W", "condition": "variables['n'] > 0"},
                },
                {
                    "id": "body",
                    "type": "set-state",
                    "position": {"x": 200, "y": 0},
                    "data": {"label": "body", "stateKey": "n", "stateValue": 0},
                },
                {"id": "e", "type": "end", "position": {"x": 300, "y": 0}, "data": {"label": "E"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "w"},
                {"id": "e2", "source": "w", "target": "body", "branch": "body"},
                {"id": "e3", "source": "w", "target": "e", "branch": "exit"},
                {"id": "e4", "source": "body", "target": "w"},  # loop-back, normal edge
            ],
        }
    )
    compiled = build_graph(wf, MemorySaver())
    assert compiled is not None


def test_conditional_source_missing_branch_raises() -> None:
    from langgraph.checkpoint.memory import MemorySaver

    from src.engine.graph_builder import WorkflowValidationError, build_graph
    from src.engine.workflow import Workflow

    wf = Workflow.model_validate(
        {
            "id": "w1",
            "name": "bad",
            "nodes": [
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                {
                    "id": "ie",
                    "type": "if-else",
                    "position": {"x": 100, "y": 0},
                    "data": {"label": "IE", "condition": "True"},
                },
                {"id": "a", "type": "end", "position": {"x": 200, "y": 0}, "data": {"label": "A"}},
                {"id": "b", "type": "end", "position": {"x": 200, "y": 100}, "data": {"label": "B"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "ie"},
                {"id": "e2", "source": "ie", "target": "a"},  # no branch
                {"id": "e3", "source": "ie", "target": "b", "branch": "false"},
            ],
        }
    )
    with pytest.raises(WorkflowValidationError, match="branch"):
        build_graph(wf, MemorySaver())


def test_normal_source_with_branch_raises() -> None:
    from langgraph.checkpoint.memory import MemorySaver

    from src.engine.graph_builder import WorkflowValidationError, build_graph
    from src.engine.workflow import Workflow

    wf = Workflow.model_validate(
        {
            "id": "w1",
            "name": "bad-normal-branch",
            "nodes": [
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                {"id": "e", "type": "end", "position": {"x": 100, "y": 0}, "data": {"label": "E"}},
            ],
            "edges": [
                # Start is a normal source → branch should be None
                {"id": "e1", "source": "s", "target": "e", "branch": "true"},
            ],
        }
    )
    with pytest.raises(WorkflowValidationError, match="branch"):
        build_graph(wf, MemorySaver())


def test_conditional_source_wrong_branch_name_raises() -> None:
    from langgraph.checkpoint.memory import MemorySaver

    from src.engine.graph_builder import WorkflowValidationError, build_graph
    from src.engine.workflow import Workflow

    wf = Workflow.model_validate(
        {
            "id": "w1",
            "name": "bad-branch-name",
            "nodes": [
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                {
                    "id": "ie",
                    "type": "if-else",
                    "position": {"x": 100, "y": 0},
                    "data": {"label": "IE", "condition": "True"},
                },
                {"id": "a", "type": "end", "position": {"x": 200, "y": 0}, "data": {"label": "A"}},
                {"id": "b", "type": "end", "position": {"x": 200, "y": 100}, "data": {"label": "B"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "ie"},
                {"id": "e2", "source": "ie", "target": "a", "branch": "yes"},  # not "true"/"false"
                {"id": "e3", "source": "ie", "target": "b", "branch": "false"},
            ],
        }
    )
    with pytest.raises(WorkflowValidationError, match="yes"):
        build_graph(wf, MemorySaver())


def test_conditional_source_duplicate_branch_raises() -> None:
    from langgraph.checkpoint.memory import MemorySaver

    from src.engine.graph_builder import WorkflowValidationError, build_graph
    from src.engine.workflow import Workflow

    wf = Workflow.model_validate(
        {
            "id": "w1",
            "name": "dup-branch",
            "nodes": [
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                {
                    "id": "ie",
                    "type": "if-else",
                    "position": {"x": 100, "y": 0},
                    "data": {"label": "IE", "condition": "True"},
                },
                {"id": "a", "type": "end", "position": {"x": 200, "y": 0}, "data": {"label": "A"}},
                {"id": "b", "type": "end", "position": {"x": 200, "y": 100}, "data": {"label": "B"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "ie"},
                {"id": "e2", "source": "ie", "target": "a", "branch": "true"},
                {"id": "e3", "source": "ie", "target": "b", "branch": "true"},  # duplicate
            ],
        }
    )
    with pytest.raises(WorkflowValidationError, match="[Dd]uplicate"):
        build_graph(wf, MemorySaver())
```

- [ ] **Step 2: Verify fail**

```bash
.venv/Scripts/python -m pytest tests/unit/engine/test_graph_builder.py -v
```
Expected: 6 new tests fail (`build_graph` still raises `NotImplementedError("Phase 4")` or similar).

- [ ] **Step 3: Modify `src/engine/graph_builder.py`**

Read the file. Current structure:
- Imports block (includes side-effect executor imports)
- `WorkflowValidationError`
- `_nodes_by_id`, `_check_edges`, `_check_reachability`
- `validate_workflow_shape`
- `CONDITIONAL_SOURCE_TYPES = {"if-else", "while", "user-approval"}`
- `build_graph(workflow, checkpointer)` — currently raises `NotImplementedError` for if-else / while edges

Required changes:

**(a) Add imports near the top of the file.** Add these alongside existing imports:

```python
from collections.abc import Callable

from src.engine.workflow import IfElseNode, WhileNode
from src.executors._eval import EvalError, evaluate
```

**(b) Add branch-label validation to `validate_workflow_shape`.** After the existing `_check_edges` call, add:

```python
    # Phase 4b: branch labels must match source type
    for edge in workflow.edges:
        source = nodes[edge.source]
        if source.type in {"if-else", "while"}:
            if edge.branch is None:
                raise WorkflowValidationError(
                    f"Edge {edge.id!r} leaves {source.type} node {source.id!r} "
                    f"but has no branch label."
                )
        else:
            if edge.branch is not None:
                raise WorkflowValidationError(
                    f"Edge {edge.id!r} has branch={edge.branch!r} but its source "
                    f"{source.id!r} is not a conditional node."
                )
```

**(c) Add `_branch_mapping` helper** after `_check_reachability`:

```python
def _branch_mapping(
    node: WorkflowNode,
    edges: list[WorkflowEdge],
    required_branches: set[str],
) -> dict[str, str]:
    """Build a {branch: target_node_id} mapping for a conditional source.

    Raises WorkflowValidationError if any edge has no branch label, duplicate
    branch labels exist, or the edge set doesn't exactly match
    `required_branches`.
    """
    out_edges = [e for e in edges if e.source == node.id]
    seen: dict[str, str] = {}
    for e in out_edges:
        if e.branch is None:
            raise WorkflowValidationError(
                f"Edge {e.id!r} leaving {node.type} node {node.id!r} has no branch label."
            )
        if e.branch in seen:
            raise WorkflowValidationError(
                f"Duplicate branch {e.branch!r} on edges leaving {node.id!r}."
            )
        if e.branch not in required_branches:
            raise WorkflowValidationError(
                f"Edge {e.id!r} leaving {node.type} node {node.id!r} has "
                f"unexpected branch {e.branch!r}; allowed: {sorted(required_branches)}."
            )
        seen[e.branch] = e.target
    missing = required_branches - seen.keys()
    if missing:
        raise WorkflowValidationError(
            f"{node.type} node {node.id!r} branches mismatch: "
            f"missing={sorted(missing)}, got={sorted(seen.keys())}."
        )
    return seen
```

**(d) Add router factories** after `_branch_mapping`:

```python
def _route_if_else(node: IfElseNode) -> Callable[[WorkflowStateDict], str]:
    expr = node.data.condition or ""

    def _router(state: WorkflowStateDict) -> str:
        if not expr:
            return "false"
        try:
            result = evaluate(expr, state)
        except EvalError:
            return "false"
        return "true" if result else "false"

    return _router


def _route_while(node: WhileNode) -> Callable[[WorkflowStateDict], str]:
    expr = node.data.condition or ""

    def _router(state: WorkflowStateDict) -> str:
        if not expr:
            return "exit"
        try:
            result = evaluate(expr, state)
        except EvalError:
            return "exit"
        return "body" if result else "exit"

    return _router
```

**(e) Rewrite the edge loop inside `build_graph`.** Replace the existing `for edge in workflow.edges:` block (which raises NotImplementedError for if-else/while) with:

```python
    # Emit normal edges first; conditional edges emitted in the next pass
    for edge in workflow.edges:
        source_node = nodes_by_id[edge.source]
        if source_node.type in {"if-else", "while"}:
            continue  # handled by conditional-edges pass below
        if source_node.type == "user-approval":
            raise NotImplementedError(
                "Conditional edges from node type 'user-approval' land in Phase 5."
            )
        if source_node.type == "note":
            continue
        if nodes_by_id[edge.target].type == "note":
            continue
        builder.add_edge(edge.source, edge.target)

    # Conditional edges pass
    for node in workflow.nodes:
        if node.type == "if-else":
            # IfElseNode pydantic narrowing
            assert isinstance(node, IfElseNode)  # pyright narrowing aid
            mapping = _branch_mapping(node, list(workflow.edges), {"true", "false"})
            builder.add_conditional_edges(  # pyright: ignore[reportUnknownMemberType]
                node.id, _route_if_else(node), mapping
            )
        elif node.type == "while":
            assert isinstance(node, WhileNode)
            mapping = _branch_mapping(node, list(workflow.edges), {"body", "exit"})
            builder.add_conditional_edges(  # pyright: ignore[reportUnknownMemberType]
                node.id, _route_while(node), mapping
            )
```

(Keep the Start-wiring and End-wiring blocks that come after it unchanged.)

- [ ] **Step 4: Run + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/engine/test_graph_builder.py -v
.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```
Expected: 6 new + all existing pass; overall ~317 (311 + 6).

```bash
git add src/engine/graph_builder.py tests/unit/engine/test_graph_builder.py
git commit -m "feat(engine): emit add_conditional_edges for if-else / while

Removes the Phase-4 NotImplementedError block in build_graph.  Replaces
with:
  - Branch-label validation (validate_workflow_shape): conditional
    sources require a branch; normal sources forbid one
  - _branch_mapping helper: validates edge set matches required
    branches exactly — no missing, no extras, no duplicates, no unknown
    branch names
  - _route_if_else / _route_while closures: call simpleeval's evaluate
    fresh on each traversal; fallback to 'false' / 'exit' on EvalError
    so stalls don't hang the graph
  - Conditional-edges pass after normal-edges pass; order matters for
    add_conditional_edges semantics

user-approval stays unimplemented (Phase 5) — the error message now
names Phase 5 explicitly instead of the generic conditional-sources
message.

See Phase 4b spec §9, ADR-0013.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Integration test — if-else routing (no LLM)

**Files:**
- Create: `tests/integration/test_if_else_routing.py`

- [ ] **Step 1: Write**

Create `tests/integration/test_if_else_routing.py`:

```python
"""Integration — Start → Set-State → If-Else → [A|B] → End.

Real Neon, no LLM needed.  Verifies conditional-edge routing works end
to end for the if-else node type.
"""

import asyncio

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def _poll_until_terminal(
    client: AsyncClient,
    execution_id: str,
    timeout: float = 30.0,
) -> dict[str, object]:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        resp = await client.get(f"/executions/{execution_id}")
        body: dict[str, object] = resp.json()
        if body["status"] in {"completed", "failed"}:
            return body
        await asyncio.sleep(0.2)
    raise AssertionError(f"Execution {execution_id} did not terminate within {timeout}s")


async def test_if_else_takes_true_branch(client: AsyncClient) -> None:
    wf = await client.post(
        "/workflows",
        json={
            "name": "Phase 4b if-else (true branch)",
            "nodes": [
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                {
                    "id": "init",
                    "type": "set-state",
                    "position": {"x": 100, "y": 0},
                    "data": {"label": "init", "stateKey": "x", "stateValue": 5},
                },
                {
                    "id": "ie",
                    "type": "if-else",
                    "position": {"x": 200, "y": 0},
                    "data": {"label": "IE", "condition": "variables['x'] > 0"},
                },
                {
                    "id": "ta",
                    "type": "set-state",
                    "position": {"x": 300, "y": -50},
                    "data": {"label": "Ta", "stateKey": "result", "stateValue": "A"},
                },
                {
                    "id": "tb",
                    "type": "set-state",
                    "position": {"x": 300, "y": 50},
                    "data": {"label": "Tb", "stateKey": "result", "stateValue": "B"},
                },
                {"id": "e", "type": "end", "position": {"x": 400, "y": 0}, "data": {"label": "E"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "init"},
                {"id": "e2", "source": "init", "target": "ie"},
                {"id": "e3", "source": "ie", "target": "ta", "branch": "true"},
                {"id": "e4", "source": "ie", "target": "tb", "branch": "false"},
                {"id": "e5", "source": "ta", "target": "e"},
                {"id": "e6", "source": "tb", "target": "e"},
            ],
        },
    )
    assert wf.status_code == 201, wf.text
    start = await client.post(
        "/executions",
        json={"workflowId": wf.json()["id"], "input": ""},
    )
    final = await _poll_until_terminal(client, start.json()["id"])
    assert final["status"] == "completed", f"Got: {final}"
    variables = final.get("variables") or {}
    assert isinstance(variables, dict)
    assert variables.get("result") == "A"


async def test_if_else_takes_false_branch(client: AsyncClient) -> None:
    wf = await client.post(
        "/workflows",
        json={
            "name": "Phase 4b if-else (false branch)",
            "nodes": [
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                {
                    "id": "init",
                    "type": "set-state",
                    "position": {"x": 100, "y": 0},
                    "data": {"label": "init", "stateKey": "x", "stateValue": -3},
                },
                {
                    "id": "ie",
                    "type": "if-else",
                    "position": {"x": 200, "y": 0},
                    "data": {"label": "IE", "condition": "variables['x'] > 0"},
                },
                {
                    "id": "ta",
                    "type": "set-state",
                    "position": {"x": 300, "y": -50},
                    "data": {"label": "Ta", "stateKey": "result", "stateValue": "A"},
                },
                {
                    "id": "tb",
                    "type": "set-state",
                    "position": {"x": 300, "y": 50},
                    "data": {"label": "Tb", "stateKey": "result", "stateValue": "B"},
                },
                {"id": "e", "type": "end", "position": {"x": 400, "y": 0}, "data": {"label": "E"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "init"},
                {"id": "e2", "source": "init", "target": "ie"},
                {"id": "e3", "source": "ie", "target": "ta", "branch": "true"},
                {"id": "e4", "source": "ie", "target": "tb", "branch": "false"},
                {"id": "e5", "source": "ta", "target": "e"},
                {"id": "e6", "source": "tb", "target": "e"},
            ],
        },
    )
    assert wf.status_code == 201, wf.text
    start = await client.post(
        "/executions",
        json={"workflowId": wf.json()["id"], "input": ""},
    )
    final = await _poll_until_terminal(client, start.json()["id"])
    assert final["status"] == "completed", f"Got: {final}"
    variables = final.get("variables") or {}
    assert isinstance(variables, dict)
    assert variables.get("result") == "B"
```

- [ ] **Step 2: Commit**

```bash
.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
.venv/Scripts/python -m pytest tests/integration/test_if_else_routing.py --collect-only -q
```
Expected: 2 new integration tests collected; non-integration baseline unchanged.

```bash
git add tests/integration/test_if_else_routing.py
git commit -m "test(integration): if-else routing takes correct branch

Two workflows that differ only in the initial value of variables.x:
  - x=5 (positive)  → condition true  → result='A'
  - x=-3 (negative) → condition false → result='B'

Real Neon; no LLM.  Verifies conditional-edge emission + router closure
evaluate condition freshly at traversal time.

See Phase 4b spec §12.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Integration test — while countdown (no LLM)

**Files:**
- Create: `tests/integration/test_while_countdown.py`

- [ ] **Step 1: Write**

Create `tests/integration/test_while_countdown.py`:

```python
"""Integration — Start → Set-State(n=3) → While(n>0) body: Transform(n-1) → Set-State(n) → loop → End.

Real Neon; no LLM.  Verifies:
  - Conditional edge from while loops back to itself via the body nodes
  - Iteration counter increments as expected
  - When condition becomes false, exit branch is taken and workflow completes
"""

import asyncio

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def _poll_until_terminal(
    client: AsyncClient,
    execution_id: str,
    timeout: float = 30.0,
) -> dict[str, object]:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        resp = await client.get(f"/executions/{execution_id}")
        body: dict[str, object] = resp.json()
        if body["status"] in {"completed", "failed"}:
            return body
        await asyncio.sleep(0.2)
    raise AssertionError(f"Execution {execution_id} did not terminate within {timeout}s")


async def test_while_countdown_from_three(client: AsyncClient) -> None:
    wf = await client.post(
        "/workflows",
        json={
            "name": "Phase 4b while countdown",
            "nodes": [
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                {
                    "id": "init",
                    "type": "set-state",
                    "position": {"x": 100, "y": 0},
                    "data": {"label": "init", "stateKey": "n", "stateValue": 3},
                },
                {
                    "id": "w",
                    "type": "while",
                    "position": {"x": 200, "y": 0},
                    "data": {"label": "W", "condition": "variables['n'] > 0"},
                },
                {
                    "id": "dec",
                    "type": "transform",
                    "position": {"x": 300, "y": 0},
                    "data": {"label": "Decrement", "transformScript": "variables['n'] - 1"},
                },
                {
                    "id": "write",
                    "type": "set-state",
                    "position": {"x": 400, "y": 0},
                    "data": {"label": "Write n", "stateKey": "n", "stateValue": "{{lastOutput}}"},
                },
                {"id": "e", "type": "end", "position": {"x": 500, "y": 0}, "data": {"label": "E"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "init"},
                {"id": "e2", "source": "init", "target": "w"},
                {"id": "e3", "source": "w", "target": "dec", "branch": "body"},
                {"id": "e4", "source": "w", "target": "e", "branch": "exit"},
                {"id": "e5", "source": "dec", "target": "write"},
                {"id": "e6", "source": "write", "target": "w"},  # loop back
            ],
        },
    )
    assert wf.status_code == 201, wf.text
    start = await client.post(
        "/executions",
        json={"workflowId": wf.json()["id"], "input": ""},
    )
    final = await _poll_until_terminal(client, start.json()["id"])
    assert final["status"] == "completed", f"Got: {final}"
    variables = final.get("variables") or {}
    assert isinstance(variables, dict)
    # After 3 iterations, n should be 0 (stored as int or "0" depending on substitution)
    n = variables.get("n")
    assert n == 0 or n == "0", f"Expected n=0 after countdown, got {n!r}"
    # Iteration counter exists and hit 4 (3 body traversals + 1 exit check)
    iter_counts = variables.get("_while_iterations") or {}
    assert iter_counts.get("w") == 4, f"Expected 4 while iterations, got {iter_counts}"
```

**Note on `{{lastOutput}}` substitution**: `substitute_in_value` returns a string when given a template. Simpleeval evaluates `variables['n'] > 0` — if `variables['n']` is the string `"2"`, Python's `"2" > 0` raises `TypeError`. We may need to adjust the workflow to ensure `n` stays an int. Two options:
1. Use a `transform` node to coerce: `int(variables['n'])` instead of reading the substituted string
2. Set `stateValue` directly to the expression output without template rendering — requires `set-state` to accept non-string values, which it already does

The cleanest fix: set `stateValue` to the expression-rendered value (Python int from the transform node's output). When `substitute_in_value` sees `"{{lastOutput}}"` as the complete value, it returns the int directly (the template engine returns the raw substituted value when the whole string is a single template).

Check the existing `substitute` behavior in `src/variable_substitution.py` — if `substitute("{{lastOutput}}", state)` returns the raw int (not str(int)), the test works as written. If it returns `str(int)`, adjust by making the transform produce `int(variables['n']) - 1` AND making the while condition `int(variables['n']) > 0`.

If the integration test fails because of string/int coercion, update the workflow's condition to `int(variables['n']) > 0` — simpleeval supports `int()` as a built-in.

- [ ] **Step 2: Commit**

```bash
.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
.venv/Scripts/python -m pytest tests/integration/test_while_countdown.py --collect-only -q
```
Expected: 1 new integration test collected.

```bash
git add tests/integration/test_while_countdown.py
git commit -m "test(integration): while-loop countdown from 3 to 0

Start → Set-State(n=3) → While(n>0) body: Transform(n-1) → Set-State(n)
→ (loop back) → End.

Real Neon; no LLM.  Verifies:
  - Conditional body-branch edge routes into the loop-body subgraph
  - Body completes and a normal edge loops back to the while node
  - While's iteration counter increments per traversal
  - When condition becomes false (n=0), exit branch is taken
  - Execution completes with the final state visible in variables

See Phase 4b spec §12.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: OAB regression port — if-else branching contract

**Files:**
- Create: `tests/regression/test_oab_if_else_routing.py`

- [ ] **Step 1: Write**

Create `tests/regression/test_oab_if_else_routing.py`:

```python
"""Regression — if-else behavioural contract (OAB parity).

OAB reference: lib/workflow/executors/if-else.ts
  - Condition truthy → true branch target node runs next
  - Condition falsy  → false branch target node runs next
  - Missing condition → execution fails at node build time

Tests the executor + router in isolation via direct invocation.
No Neon required for this regression; it's pure logic.
"""

from typing import Any

import pytest

from src.engine.state import initial_state
from src.engine.workflow import IfElseNode
from src.executors.if_else import IfElseExecutor, IfElseNodeError

pytestmark = pytest.mark.integration


def _if_else_node(**data: Any) -> IfElseNode:
    return IfElseNode.model_validate(
        {
            "id": "ie",
            "type": "if-else",
            "position": {"x": 0, "y": 0},
            "data": {"label": "IE", **data},
        }
    )


async def test_oab_regression_truthy_condition_records_true_branch() -> None:
    node = _if_else_node(condition="variables['answer'] == 42")
    state = initial_state()
    state["variables"]["answer"] = 42
    delta = await IfElseExecutor(node).arun(state)
    assert delta["node_results"]["ie"]["output"]["taken"] == "true"


async def test_oab_regression_falsy_condition_records_false_branch() -> None:
    node = _if_else_node(condition="variables['answer'] == 42")
    state = initial_state()
    state["variables"]["answer"] = 41
    delta = await IfElseExecutor(node).arun(state)
    assert delta["node_results"]["ie"]["output"]["taken"] == "false"


async def test_oab_regression_missing_condition_fails_at_execution() -> None:
    node = _if_else_node()  # no condition
    with pytest.raises(ValueError, match="condition"):
        await IfElseExecutor(node).arun(initial_state())


async def test_oab_regression_undefined_variable_wraps() -> None:
    node = _if_else_node(condition="variables['ghost'] > 0")
    state = initial_state()
    with pytest.raises(IfElseNodeError):
        await IfElseExecutor(node).arun(state)
```

- [ ] **Step 2: Commit**

```bash
.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest tests/regression/test_oab_if_else_routing.py -v
```
Expected: 4 regression tests pass (they run without Neon; no marker gate applied because `TEST_DATABASE_URL`-based skip applies only when running `-m integration` explicitly).

Actually — the tests are marked `@pytest.mark.integration`, so they run under `-m integration`. Verify they're collected:

```bash
.venv/Scripts/python -m pytest tests/regression/test_oab_if_else_routing.py --collect-only -q
```

```bash
git add tests/regression/test_oab_if_else_routing.py
git commit -m "test(regression): if-else behavioural contract (OAB parity)

Four regressions capture OAB's if-else executor contract:
  1. Truthy condition records 'true' branch in node_results
  2. Falsy condition records 'false' branch
  3. Missing condition → ValueError
  4. Undefined variable in condition → IfElseNodeError (wraps EvalError)

Pure executor logic — no Neon required.  Serves as the canary if a
future refactor silently drops or changes any of these four behaviours.

See Phase 4b spec §12.3.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: (reserved) — adjustments from integration verification

This task may be empty if the integration suite passes clean.  If it fails, fix any bugs caught in this task with a dedicated commit matching the Phase 3a/3b/4a pattern ("fix: X bugs caught by real-API integration testing").

If no fixes are needed, skip to Task 9.

---

## Task 9: Phase-exit — CHANGELOG + CLAUDE.md + ADR-0013 backfill

**Authorized:** `CHANGELOG.md`, `CLAUDE.md` phase-status table, `docs/design/decisions.md` ADR-0013 `Implemented by` line.

- [ ] **Step 1: Verify exit checklist**

```bash
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m ruff format --check src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```
All four green.

Run Phase 4b integration + regression (controller, not subagent):
```bash
.venv/Scripts/python -c "
from dotenv import load_dotenv
import os, subprocess, sys
load_dotenv('.env', override=True)
os.environ['TEST_DATABASE_URL'] = os.environ.get('DATABASE_URL', '')
r = subprocess.run(
    [sys.executable, '-m', 'pytest', '-v', '--tb=short', '--no-cov',
     '-m', 'integration',
     'tests/integration/test_if_else_routing.py',
     'tests/integration/test_while_countdown.py',
     'tests/regression/test_oab_if_else_routing.py'],
    cwd='.'
)
sys.exit(r.returncode)
"
```
All pass → proceed. Any fail → this informs Task 8.

- [ ] **Step 2: Append to `CHANGELOG.md`**

Insert above the Phase 4a section:

```markdown
### Phase 4b — Control-flow executors (if-else, while) (2026-04-21)

#### Added
- [Phase 4b design spec](docs/superpowers/specs/2026-04-21-phase-4b-control-flow-design.md) + ADR-0013.
- `WorkflowEdge.branch: str | None` field for conditional-edge labels (ADR-0013).
- `src/executors/if_else.py` — records the evaluated branch decision; actual routing done by the builder's closure.
- `src/executors/while_loop.py` — iteration-bounded loop with `max_iterations` cap (default 100); raises `WhileMaxIterationsError` on overflow.
- `graph_builder` emits `add_conditional_edges` for if-else / while; router closures call simpleeval fresh on each traversal.
- `_branch_mapping` helper validates edge-branch sets against required branches (exact match: no missing, no extras, no duplicates, no wrong names).
- Integration tests: two if-else workflows (true + false branch) + one while countdown — all run against real Neon without an LLM.
- Regression port: 4 OAB if-else behavioural-contract tests.

#### Changed
- `src/engine/workflow.py`: `WorkflowEdge` gains `branch: str | None = None`.
- `src/engine/graph_builder.py`: `validate_workflow_shape` validates branch labels against source type; `build_graph` emits conditional edges after normal edges.
- Phase 4 is now ✅ Complete overall (4a linear executors + 4b control-flow).

#### Deliberate design choices (see ADR-0013 + spec)
- Routing by edge label, not runtime inference from node-data fields. Single source of truth.
- Router closures evaluate condition fresh on each traversal (slight redundancy vs. reading from node_results, but deterministic; OAB does the same).
- No `break` / `continue` keywords in while body — chain an inner `if-else` routing to the exit target for early termination.

### Phase 4a — Linear Executors (http, set-state, transform, data-transform, extract) (2026-04-21)
```

- [ ] **Step 3: Update `CLAUDE.md` phase table**

Change:
```markdown
| 4a — Linear executors (http, set-state, transform, data-transform, extract) | ✅ Complete | 5 executors + simpleeval wrapper + recursive substitution; integration verified against real Anthropic |
| 4b — Control-flow (if-else, while) | ⏭ Next | Needs graph_builder surgery for conditional/loop edges |
| 5 — User-approval + SSE streaming | ⏸ | LangGraph `interrupt()` |
```
To:
```markdown
| 4a — Linear executors (http, set-state, transform, data-transform, extract) | ✅ Complete | 5 executors + simpleeval wrapper + recursive substitution; integration verified against real Anthropic |
| 4b — Control-flow (if-else, while) | ✅ Complete | add_conditional_edges; while cap 100; both if-else branches + while countdown verified against real Neon |
| 5 — User-approval + SSE streaming | ⏭ Next | LangGraph `interrupt()` |
```

- [ ] **Step 4: Backfill ADR-0013 `Implemented by`**

In `docs/design/decisions.md`, change:
```markdown
**Implemented by.** Phase 4b (commits TBD).
```
To:
```markdown
**Implemented by.** Phase 4b (commits `<first>`..`<last>` on `main`, 2026-04-21).
```

Get the range via:
```bash
git log --oneline 4f8314a..HEAD
```
(starts just after Phase 4a exit).

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md CLAUDE.md docs/design/decisions.md
git commit -m "docs(phase-4b): mark Phase 4b complete — Phase 4 done overall

Two control-flow executors shipped (if-else, while) + graph_builder
teaches LangGraph to emit conditional edges via add_conditional_edges.
WorkflowEdge.branch label locks in single-source-of-truth routing
(ADR-0013).

Real-Neon integration + regression all green.  Phase 4a + 4b close
out Phase 4.  Phase 5 (user-approval + SSE streaming) is next.

ADR-0013 Implemented by backfilled with commit range.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Spec coverage self-review

| Spec section | Tasks |
|---|---|
| §6 WorkflowEdge.branch | Task 1 |
| §7 IfElseExecutor | Task 2 |
| §8 WhileExecutor | Task 3 |
| §9 graph_builder conditional-edges | Task 4 |
| §10.1 unit tests | Tasks 2, 3, 4 |
| §10.2 integration tests | Tasks 5, 6 |
| §10.3 regression | Task 7 |
| §11 phase-exit checklist | Task 9 |

No placeholder steps. Type consistency verified:
- `WorkflowEdge.branch: str | None` — same shape across Tasks 1, 4.
- Executor error hierarchy: `IfElseNodeError`, `WhileNodeError`, `WhileMaxIterationsError` — consistent `<Node>NodeError` pattern.
- `_route_if_else` / `_route_while` return `str` (branch key) — matches `add_conditional_edges` mapping type.
- `ITER_STATE_KEY = "_while_iterations"` — exported from `while_loop.py`, consumed by tests in Task 3.

---

## Execution handoff

Plan saved. Controller proceeds to `superpowers:subagent-driven-development` for Tasks 1–9. Same cadence as Phases 1–4a. Task 9 runs the integration + regression suite against real Neon before the phase-exit commit lands.
