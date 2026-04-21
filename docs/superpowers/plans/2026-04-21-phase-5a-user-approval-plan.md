# Phase 5a — User-approval + interrupt/resume: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the `user-approval` node executor + `POST /executions/{id}/resume` endpoint. Uses LangGraph's `interrupt()` + our existing `PrismaCheckpointSaver` for pause; resumes via a new `BackgroundTask`. Dedicated `Approval` Prisma table stores decisions. Unblocks the last conditional-source sentinel in `graph_builder.py`.

**Architecture.** Executor calls `interrupt({node_id, prompt})`. `LangGraphExecutor.run` catches `GraphInterrupt`, marks `waiting_approval`. `POST /resume` writes `Approval` row + schedules `LangGraphExecutor.resume(execution_id, decision)` via FastAPI's `BackgroundTasks`, which calls `compiled.ainvoke(Command(resume=decision))`. `graph_builder` extends the Phase 4b conditional-edges pass with the `user-approval` branch, routing `{approved, rejected}`.

**Tech Stack:** LangGraph v0.4+ (`interrupt`, `Command`, `GraphInterrupt`), Prisma Python, FastAPI BackgroundTasks, pytest.

**Spec:** [`docs/superpowers/specs/2026-04-21-phase-5a-user-approval-design.md`](../specs/2026-04-21-phase-5a-user-approval-design.md)
**ADR:** [ADR-0016](../../design/decisions.md#adr-0016-user-approval-uses-langgraph-interrupt--background-task-resume)

---

## Sequencing and discipline

10 tasks, one commit each. Every task ends green on:

```bash
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m ruff format --check src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

Integration tests (Tasks 7 + 8) + regression (Task 9) run at phase-exit (Task 10) with real Neon.

**⚠️ Forbidden files (all tasks except where explicitly noted):** `pyproject.toml`, `.github/workflows/*`, `CLAUDE.md` (except Task 10), `docs/design/*` (except Task 10 for ADR backfill), `docs/superpowers/plans/*`, `docs/superpowers/specs/*`. Fix pyright strict errors with `# pyright: ignore[specific]` inline — never loosen `pyproject.toml`.

Commit footer every commit:
```
Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
```

Stay on `main`.

---

## Task 1: Prisma schema — `Approval` table + `ApprovalDecision` enum + migration

**Files:**
- Modify: `prisma/schema.prisma`
- Create: `prisma/migrations/<timestamp>_phase_5a_approval/migration.sql` (generated)

**Authorized:** `prisma/schema.prisma` for this task.

- [ ] **Step 1: Append to `prisma/schema.prisma`**

Append after the existing `User` model (or anywhere after WorkflowExecution):

```prisma
enum ApprovalDecision {
  approved
  rejected
}

model Approval {
  id               String            @id @default(cuid())
  executionId      String            @map("execution_id")
  nodeId           String            @map("node_id")
  approverUserId   String            @map("approver_user_id")
  decision         ApprovalDecision
  note             String?
  createdAt        DateTime          @default(now()) @map("created_at")

  execution        WorkflowExecution @relation(fields: [executionId], references: [id], onDelete: Cascade)

  @@map("approvals")
  @@index([executionId])
  @@index([approverUserId])
}
```

Add the back-reference to `WorkflowExecution`. Find the existing `WorkflowExecution` model and add to its body:

```prisma
  approvals        Approval[]
```

(Place it near other relation fields in that model.)

- [ ] **Step 2: Generate + migrate**

```bash
uv run prisma generate
uv run prisma migrate dev --name phase_5a_approval
```
Fall back to `.venv/Scripts/python -m prisma <cmd>` if `uv` isn't on PATH. If Neon cold-starts (P1001), use:
```bash
.venv/Scripts/python -m prisma migrate deploy
```

- [ ] **Step 3: Verify**

```bash
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```
Expected: 0 pyright errors, 364 tests pass (Phase 7a exit baseline).

- [ ] **Step 4: Commit**

```bash
git add prisma/schema.prisma prisma/migrations/
git commit -m "feat(schema): Approval table + ApprovalDecision enum (Phase 5a)

One row per user-approval decision: executionId, nodeId,
approverUserId (who submitted /resume), decision enum (approved |
rejected), optional note.

Cascade-delete when the WorkflowExecution is deleted.  Index on
executionId (list-approvals-for-execution queries) + approverUserId
(list-my-approvals queries).

WorkflowExecution gains back-relation: approvals Approval[].

See Phase 5a spec §5, ADR-0016.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `UserApprovalExecutor` — calls `interrupt()`

**Files:**
- Create: `src/executors/user_approval.py`
- Modify: `src/engine/graph_builder.py` (side-effect import)
- Create: `tests/unit/executors/test_user_approval_executor.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/executors/test_user_approval_executor.py`:

```python
"""Tests for the user-approval executor."""

from typing import Any
from unittest.mock import MagicMock

import pytest

from src.engine.state import initial_state
from src.engine.workflow import UserApprovalNode
from src.executors.user_approval import UserApprovalExecutor, UserApprovalNodeError


def _user_approval_node(**data: Any) -> UserApprovalNode:
    return UserApprovalNode.model_validate(
        {
            "id": "ua",
            "type": "user-approval",
            "position": {"x": 0, "y": 0},
            "data": {"label": "UA", **data},
        }
    )


async def test_user_approval_raises_interrupt_on_first_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First call to arun() must propagate GraphInterrupt (LangGraph raises it
    inside interrupt() when there's no prior checkpoint to resume from)."""
    from langgraph.errors import GraphInterrupt

    node = _user_approval_node(prompt="Please approve")

    # Stub interrupt() to simulate LangGraph's first-pass behavior
    import src.executors.user_approval as ua_mod

    def _fake_interrupt(value: Any) -> Any:
        raise GraphInterrupt(value)

    monkeypatch.setattr(ua_mod, "interrupt", _fake_interrupt)

    with pytest.raises(GraphInterrupt):
        await UserApprovalExecutor(node).arun(initial_state())


async def test_user_approval_returns_delta_on_resume_approved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When resume value is 'approved', executor records it in variables."""
    node = _user_approval_node(prompt="Approve?")

    import src.executors.user_approval as ua_mod

    monkeypatch.setattr(ua_mod, "interrupt", lambda value: "approved")  # pyright: ignore[reportUnknownLambdaType]

    delta = await UserApprovalExecutor(node).arun(initial_state())
    assert delta["variables"]["_approval_ua"] == "approved"
    assert delta["current_node_id"] == "ua"
    assert delta["node_results"]["ua"]["output"]["decision"] == "approved"


async def test_user_approval_returns_delta_on_resume_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node = _user_approval_node(prompt="Approve?")

    import src.executors.user_approval as ua_mod

    monkeypatch.setattr(ua_mod, "interrupt", lambda value: "rejected")  # pyright: ignore[reportUnknownLambdaType]

    delta = await UserApprovalExecutor(node).arun(initial_state())
    assert delta["variables"]["_approval_ua"] == "rejected"
    assert delta["node_results"]["ua"]["output"]["decision"] == "rejected"


async def test_user_approval_invalid_decision_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node = _user_approval_node(prompt="Approve?")

    import src.executors.user_approval as ua_mod

    monkeypatch.setattr(ua_mod, "interrupt", lambda value: "maybe")  # pyright: ignore[reportUnknownLambdaType]

    with pytest.raises(UserApprovalNodeError, match="maybe"):
        await UserApprovalExecutor(node).arun(initial_state())


async def test_user_approval_prompt_substitution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node = _user_approval_node(prompt="Approve {{user_name}}'s request?")

    captured: dict[str, Any] = {}

    def _fake_interrupt(value: Any) -> Any:
        captured["value"] = value
        return "approved"

    import src.executors.user_approval as ua_mod

    monkeypatch.setattr(ua_mod, "interrupt", _fake_interrupt)

    state = initial_state()
    state["variables"]["user_name"] = "Alice"
    await UserApprovalExecutor(node).arun(state)

    assert captured["value"]["prompt"] == "Approve Alice's request?"
    assert captured["value"]["node_id"] == "ua"


async def test_user_approval_executor_is_registered() -> None:
    import src.executors.user_approval  # noqa: F401  # pyright: ignore[reportUnusedImport]

    from src.executors.base import build_executor

    node = _user_approval_node(prompt="Approve?")
    executor = build_executor(node)
    assert isinstance(executor, UserApprovalExecutor)
```

- [ ] **Step 2: Verify fail**

```bash
.venv/Scripts/python -m pytest tests/unit/executors/test_user_approval_executor.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implement `src/executors/user_approval.py`**

```python
"""user-approval node executor.

Calls LangGraph's `interrupt()` to pause execution.  On first pass,
interrupt() raises GraphInterrupt (caught by LangGraphExecutor.run,
which marks the execution 'waiting_approval').  On resume, interrupt()
returns the value passed to Command(resume=<decision>) — "approved" or
"rejected".

The executor records `_approval_<node_id>` in variables so that
graph_builder's router (built in Task 3) can dispatch to the
'approved' or 'rejected' branch.

See Phase 5a spec §6, ADR-0016.
"""

from typing import Any

from langgraph.types import interrupt  # pyright: ignore[reportUnknownVariableType]

from src.engine.state import WorkflowStateDict
from src.engine.workflow import UserApprovalNode
from src.executors.base import register_executor
from src.variable_substitution import substitute


class UserApprovalNodeError(RuntimeError):
    """Raised when the resumed decision is neither 'approved' nor 'rejected'."""


@register_executor("user-approval")
class UserApprovalExecutor:
    def __init__(self, node: UserApprovalNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        prompt_template = self.node.data.prompt or "Please approve"
        prompt = substitute(prompt_template, state)

        decision = interrupt({"node_id": self.node.id, "prompt": prompt})

        if decision not in {"approved", "rejected"}:
            raise UserApprovalNodeError(
                f"user-approval node {self.node.id!r} received invalid decision "
                f"{decision!r}; expected 'approved' or 'rejected'"
            )

        return {
            "variables": {f"_approval_{self.node.id}": decision},
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "input": {"prompt": prompt},
                    "output": {"decision": decision},
                }
            },
        }


__all__ = ["UserApprovalExecutor", "UserApprovalNodeError"]
```

- [ ] **Step 4: Register side-effect import in `graph_builder.py`**

Read the file. The existing side-effect imports follow the pattern. Add `user_approval` alphabetically (between `transform` and `while_loop`):

```python
from src.executors import (
    user_approval as _user_approval_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
```

- [ ] **Step 5: Run + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/executors/test_user_approval_executor.py -v
.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```
Expected: 6 new tests pass; overall +6 (~370).

```bash
git add src/executors/user_approval.py src/engine/graph_builder.py \
        tests/unit/executors/test_user_approval_executor.py
git commit -m "feat(executors): user-approval — interrupt-based pause (Phase 5a)

Executor calls LangGraph's interrupt({node_id, prompt}).  First pass:
interrupt() raises GraphInterrupt (caught by LangGraphExecutor.run in
Task 4, which persists state + marks execution 'waiting_approval').
On resume: interrupt() returns the decision passed to Command(
resume=...) — 'approved' or 'rejected'.

Records _approval_<node_id> in variables so graph_builder's router
(Task 3) can route to the matching branch.

Invalid decision values raise UserApprovalNodeError (shouldn't reach
here in production — API validates the decision enum).

Prompt templates support {{...}} substitution; tested.

See Phase 5a spec §6, ADR-0016.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `graph_builder` — conditional-edges for user-approval

**Files:**
- Modify: `src/engine/graph_builder.py`
- Modify: `tests/unit/engine/test_graph_builder.py` (append)

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/engine/test_graph_builder.py`:

```python
def test_conditional_edges_compile_for_user_approval() -> None:
    from langgraph.checkpoint.memory import MemorySaver

    from src.engine.graph_builder import build_graph
    from src.engine.workflow import Workflow

    wf = Workflow.model_validate(
        {
            "id": "w1",
            "name": "user-approval test",
            "nodes": [
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                {
                    "id": "ua",
                    "type": "user-approval",
                    "position": {"x": 100, "y": 0},
                    "data": {"label": "UA", "prompt": "Please approve"},
                },
                {"id": "a", "type": "end", "position": {"x": 200, "y": 0}, "data": {"label": "A"}},
                {"id": "b", "type": "end", "position": {"x": 200, "y": 100}, "data": {"label": "B"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "ua"},
                {"id": "e2", "source": "ua", "target": "a", "branch": "approved"},
                {"id": "e3", "source": "ua", "target": "b", "branch": "rejected"},
            ],
        }
    )
    compiled = build_graph(wf, MemorySaver())
    assert compiled is not None


def test_user_approval_missing_approved_branch_raises() -> None:
    from langgraph.checkpoint.memory import MemorySaver

    from src.engine.graph_builder import WorkflowValidationError, build_graph
    from src.engine.workflow import Workflow

    wf = Workflow.model_validate(
        {
            "id": "w1",
            "name": "bad user-approval",
            "nodes": [
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                {
                    "id": "ua",
                    "type": "user-approval",
                    "position": {"x": 100, "y": 0},
                    "data": {"label": "UA", "prompt": "Please approve"},
                },
                {"id": "b", "type": "end", "position": {"x": 200, "y": 0}, "data": {"label": "B"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "ua"},
                # Only rejected branch — missing approved
                {"id": "e2", "source": "ua", "target": "b", "branch": "rejected"},
            ],
        }
    )
    with pytest.raises(WorkflowValidationError, match="approved"):
        build_graph(wf, MemorySaver())


def test_user_approval_wrong_branch_name_raises() -> None:
    from langgraph.checkpoint.memory import MemorySaver

    from src.engine.graph_builder import WorkflowValidationError, build_graph
    from src.engine.workflow import Workflow

    wf = Workflow.model_validate(
        {
            "id": "w1",
            "name": "bad branch name",
            "nodes": [
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                {
                    "id": "ua",
                    "type": "user-approval",
                    "position": {"x": 100, "y": 0},
                    "data": {"label": "UA", "prompt": "Approve?"},
                },
                {"id": "a", "type": "end", "position": {"x": 200, "y": 0}, "data": {"label": "A"}},
                {"id": "b", "type": "end", "position": {"x": 200, "y": 100}, "data": {"label": "B"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "ua"},
                {"id": "e2", "source": "ua", "target": "a", "branch": "yes"},  # wrong
                {"id": "e3", "source": "ua", "target": "b", "branch": "rejected"},
            ],
        }
    )
    with pytest.raises(WorkflowValidationError, match="yes"):
        build_graph(wf, MemorySaver())
```

- [ ] **Step 2: Verify fail**

Run the focused tests; they should fail at the "user-approval Phase 5 NotImplementedError" check currently in graph_builder.py:

```bash
.venv/Scripts/python -m pytest tests/unit/engine/test_graph_builder.py::test_conditional_edges_compile_for_user_approval -v
```

- [ ] **Step 3: Modify `src/engine/graph_builder.py`**

Read the file. Find the section that handles `{"if-else", "while"}` in the conditional-edges pass. It currently has a `user-approval` → NotImplementedError branch.

**(a) Add router factory** after `_route_while`:

```python
def _route_user_approval(
    node: UserApprovalNode,
) -> Callable[[WorkflowStateDict], str]:
    """Router for user-approval: reads _approval_<node_id> from variables."""

    def _router(state: WorkflowStateDict) -> str:
        variables = state.get("variables") or {}
        decision = variables.get(f"_approval_{node.id}")
        return "approved" if decision == "approved" else "rejected"

    return _router
```

**(b) Add `UserApprovalNode` to the workflow imports** at the top:
```python
from src.engine.workflow import IfElseNode, UserApprovalNode, WhileNode, WorkflowEdge, WorkflowNode
```

**(c) Update the conditional-edges pass.** Find the existing block that handles `if-else` and `while`:

```python
    for node in workflow.nodes:
        if node.type == "if-else":
            assert isinstance(node, IfElseNode)
            mapping = _branch_mapping(node, list(workflow.edges), {"true", "false"})
            builder.add_conditional_edges(...)
        elif node.type == "while":
            assert isinstance(node, WhileNode)
            mapping = _branch_mapping(node, list(workflow.edges), {"body", "exit"})
            builder.add_conditional_edges(...)
```

Add the user-approval branch:

```python
        elif node.type == "user-approval":
            assert isinstance(node, UserApprovalNode)
            mapping = _branch_mapping(node, list(workflow.edges), {"approved", "rejected"})
            builder.add_conditional_edges(  # pyright: ignore[reportUnknownMemberType]
                node.id, _route_user_approval(node), mapping
            )
```

**(d) Remove the NotImplementedError for user-approval.** Find:
```python
        if source_node.type == "user-approval":
            raise NotImplementedError(
                "Conditional edges from node type 'user-approval' land in Phase 5."
            )
```
Replace with:
```python
        if source_node.type in {"if-else", "while", "user-approval"}:
            continue  # handled by conditional-edges pass below
```
(Or expand the existing `{"if-else", "while"}` check to include `user-approval`; either works.)

**(e) Update branch-validation in `validate_workflow_shape`.** Find the `_conditional_types = {"if-else", "while"}` set and expand to include user-approval:
```python
    _conditional_types = {"if-else", "while", "user-approval"}
```

- [ ] **Step 4: Run + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/engine/test_graph_builder.py -v
.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```
Expected: 3 new tests pass; the previous unshipped-sentinel test for `user-approval`/`Phase 5` migrates — see note below.

**Important: sentinel test needs re-migration.** `test_build_graph_rejects_unshipped_executor_type` currently uses `user-approval` + `Phase 5` as the sentinel. Now that user-approval ships, this test needs to move to a still-unshipped type. Look at `_PHASE_FOR_TYPE` in `src/executors/base.py` to pick one. Candidates: `guardrails` (Phase 6), `note` (Phase 6 visual), `vector-db` (Phase 6), `gamma-ai` (Phase 6), `arcade` (Phase 6).

Use `guardrails` and `Phase 6`. Update the test:
```python
from src.engine.workflow import GuardrailsNode

def test_build_graph_rejects_unshipped_executor_type() -> None:
    wf = _mk(
        nodes=[
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {"id": "h", "type": "guardrails", "position": {"x": 0, "y": 0}, "data": {"label": "H"}},
            {"id": "e", "type": "end", "position": {"x": 0, "y": 0}, "data": {"label": "E"}},
        ],
        edges=[
            {"id": "e1", "source": "s", "target": "h"},
            {"id": "e2", "source": "h", "target": "e"},
        ],
    )
    with pytest.raises(NotImplementedError, match="Phase 6"):
        build_graph(wf, MemorySaver())
```

Also update `tests/unit/executors/test_registry.py::test_unshipped_type_raises_with_phase_hint` — similar swap (check its current shape in the file; apply the same `user-approval` → `guardrails`, `Phase 5` → `Phase 6` rename).

Also `tests/unit/engine/test_langgraph_executor.py::test_run_marks_failed_on_exception` — same migration.

```bash
git add src/engine/graph_builder.py tests/unit/engine/test_graph_builder.py \
        tests/unit/executors/test_registry.py tests/unit/engine/test_langgraph_executor.py
git commit -m "feat(engine): emit add_conditional_edges for user-approval

Phase 4b's pattern extended to user-approval source nodes: branches
are {'approved', 'rejected'}, router reads _approval_<node_id> from
state.variables (written by UserApprovalExecutor in Task 2).

Removes the Phase-5 NotImplementedError block.  validate_workflow_shape
now requires branch labels on user-approval edges (per the existing
rule for conditional sources).

Sentinel tests migrate: user-approval/Phase 5 → guardrails/Phase 6
(guardrails is the next unshipped type, Phase 6).

See Phase 5a spec §7, ADR-0013 (branch labels), ADR-0016 (interrupt).

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: `LangGraphExecutor` — catch `GraphInterrupt` + add `resume()` method

**Files:**
- Modify: `src/engine/langgraph_executor.py`
- Modify: `tests/unit/engine/test_langgraph_executor.py` (append)

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/engine/test_langgraph_executor.py`:

```python
async def test_run_marks_waiting_approval_on_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the compiled graph raises GraphInterrupt, the execution is marked
    waiting_approval with _pending_approval_node + _pending_approval_prompt
    in variables."""
    from types import SimpleNamespace
    from typing import Any
    from unittest.mock import AsyncMock, MagicMock

    from langgraph.errors import GraphInterrupt

    from src.engine.langgraph_executor import LangGraphExecutor

    # Stub db
    db = MagicMock()
    db.workflowexecution = MagicMock()
    db.workflowexecution.find_unique = AsyncMock(
        return_value=SimpleNamespace(
            id="e1",
            workflowId="w1",
            userId="dev",
            threadId="t1",
            input=None,
        )
    )
    db.workflow = MagicMock()
    db.workflow.find_unique = AsyncMock(
        return_value=SimpleNamespace(
            id="w1",
            name="test",
            nodes=[
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                {"id": "ua", "type": "user-approval", "position": {"x": 0, "y": 0}, "data": {"label": "UA", "prompt": "Approve?"}},
                {"id": "a", "type": "end", "position": {"x": 0, "y": 0}, "data": {"label": "A"}},
                {"id": "b", "type": "end", "position": {"x": 0, "y": 0}, "data": {"label": "B"}},
            ],
            edges=[
                {"id": "e1", "source": "s", "target": "ua"},
                {"id": "e2", "source": "ua", "target": "a", "branch": "approved"},
                {"id": "e3", "source": "ua", "target": "b", "branch": "rejected"},
            ],
        )
    )
    update_calls: list[dict[str, Any]] = []

    async def _update(*, where: Any, data: Any) -> Any:
        update_calls.append({"where": where, "data": data})
        return None

    db.workflowexecution.update = _update

    checkpointer = MagicMock()

    # Patch compiled.ainvoke to raise GraphInterrupt
    from src.engine import langgraph_executor as lge_mod

    class _FakeCompiled:
        async def ainvoke(self, *args: Any, **kwargs: Any) -> Any:
            raise GraphInterrupt({"node_id": "ua", "prompt": "Approve?"})

    def _fake_build_graph(*args: Any, **kwargs: Any) -> Any:
        return _FakeCompiled()

    monkeypatch.setattr(lge_mod, "build_graph", _fake_build_graph)

    orchestrator = LangGraphExecutor(db, checkpointer)
    await orchestrator.run("e1")

    # Inspect the last update call
    assert any(
        call["data"].get("status") == "waiting_approval" for call in update_calls
    ), f"Expected waiting_approval update; got: {update_calls}"


async def test_resume_approved_continues_to_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """resume() calls compiled.ainvoke(Command(resume=...)) and marks completed."""
    from types import SimpleNamespace
    from typing import Any
    from unittest.mock import AsyncMock, MagicMock

    from src.engine.langgraph_executor import LangGraphExecutor

    db = MagicMock()
    db.workflowexecution = MagicMock()
    db.workflowexecution.find_unique = AsyncMock(
        return_value=SimpleNamespace(
            id="e1", workflowId="w1", userId="dev", threadId="t1", input=None,
        )
    )
    db.workflow = MagicMock()
    db.workflow.find_unique = AsyncMock(
        return_value=SimpleNamespace(
            id="w1",
            name="t",
            nodes=[
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                {"id": "e", "type": "end", "position": {"x": 0, "y": 0}, "data": {"label": "E"}},
            ],
            edges=[{"id": "e1", "source": "s", "target": "e"}],
        )
    )
    update_calls: list[dict[str, Any]] = []

    async def _update(*, where: Any, data: Any) -> Any:
        update_calls.append(data)
        return None

    db.workflowexecution.update = _update

    from src.engine import langgraph_executor as lge_mod

    class _FakeCompiled:
        async def ainvoke(self, state: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
            return {"variables": {"lastOutput": "done"}, "node_results": {}}

    monkeypatch.setattr(lge_mod, "build_graph", lambda *a, **kw: _FakeCompiled())  # pyright: ignore[reportUnknownLambdaType]

    orchestrator = LangGraphExecutor(db, MagicMock())
    await orchestrator.resume("e1", "approved")

    assert any(c.get("status") == "completed" for c in update_calls), update_calls
```

- [ ] **Step 2: Verify fail**

```bash
.venv/Scripts/python -m pytest tests/unit/engine/test_langgraph_executor.py -v
```

- [ ] **Step 3: Modify `src/engine/langgraph_executor.py`**

Read the file. Imports to add:

```python
from langgraph.errors import GraphInterrupt
from langgraph.types import Command
```

The existing `run(execution_id)` method wraps `compiled.ainvoke(...)` in try/except. Add a new specific handler for `GraphInterrupt`:

```python
    async def run(self, execution_id: str) -> None:
        execution = await self._load_execution(execution_id)
        if execution is None:
            logger.error("Execution %s not found at run time", execution_id)
            return

        try:
            compiled, state, config = await self._prepare(execution)
            try:
                final_state = await compiled.ainvoke(state, config=config)
            except GraphInterrupt as interrupt_exc:
                await self._mark_waiting_approval(execution_id, interrupt_exc)
                return
            await self._mark_completed(execution_id, final_state)
        except Exception as exc:
            logger.exception("Execution %s failed", execution_id)
            await self._mark_failed(execution_id, exc)
```

Refactor the existing inline DB-update logic into `_prepare`, `_mark_waiting_approval`, `_mark_completed`, `_mark_failed` helpers. If you prefer inline, that's fine too — the key requirement is the `GraphInterrupt` catch + `waiting_approval` update.

Add the new `resume()` method:

```python
    async def resume(self, execution_id: str, decision: str) -> None:
        """Resume a paused execution with the given decision.

        Called by /executions/{id}/resume via FastAPI BackgroundTasks.
        Feeds `decision` (e.g. "approved" / "rejected") into the paused
        graph via LangGraph's Command(resume=...).
        """
        execution = await self._load_execution(execution_id)
        if execution is None:
            return

        try:
            compiled, _state, config = await self._prepare(execution)
            try:
                final_state = await compiled.ainvoke(Command(resume=decision), config=config)
            except GraphInterrupt as interrupt_exc:
                # Chained pause — another user-approval node hit
                await self._mark_waiting_approval(execution_id, interrupt_exc)
                return
            await self._mark_completed(execution_id, final_state)
        except Exception as exc:
            logger.exception("Resume of execution %s failed", execution_id)
            await self._mark_failed(execution_id, exc)
```

Implement the helpers:

```python
    async def _prepare(self, execution: Any) -> tuple[Any, Any, dict[str, Any]]:
        """Build compiled graph + initial state + config for run() / resume()."""
        workflow_row = await self.db.workflow.find_unique(where={"id": execution.workflowId})  # pyright: ignore[reportAttributeAccessIssue]
        if workflow_row is None:
            raise RuntimeError(f"Workflow {execution.workflowId!r} not found")

        workflow = Workflow.model_validate({
            "id": workflow_row.id,
            "name": workflow_row.name,
            "nodes": workflow_row.nodes,
            "edges": workflow_row.edges,
        })
        compiled = build_graph(workflow, self.checkpointer)

        state = initial_state(execution.input if execution.input is not None else "")
        if execution.userId:
            state["user_id"] = execution.userId

        # Phase 7a LangSmith threading
        settings = get_settings()
        ls = (
            LangSmithConfig(
                tracing_v2=settings.langchain_tracing_v2,
                project=settings.langchain_project,
                endpoint=settings.langchain_endpoint,
                api_key=settings.langchain_api_key,
            )
            if settings.langchain_tracing_v2
            else None
        )
        set_current_langsmith(ls)
        set_current_db(self.db)

        config: dict[str, Any] = {"configurable": {"thread_id": execution.threadId}}
        return compiled, state, config


    async def _mark_waiting_approval(
        self, execution_id: str, interrupt_exc: GraphInterrupt
    ) -> None:
        """Persist the pause state."""
        info_raw = getattr(interrupt_exc, "value", None)
        # LangGraph sometimes wraps the interrupt value as a list
        if isinstance(info_raw, list) and info_raw:
            info: dict[str, Any] = info_raw[0] if isinstance(info_raw[0], dict) else {}
        elif isinstance(info_raw, dict):
            info = info_raw
        else:
            info = {}

        pending_variables: dict[str, Any] = {
            "_pending_approval_node": info.get("node_id"),
            "_pending_approval_prompt": info.get("prompt"),
        }
        await self.db.workflowexecution.update(  # pyright: ignore[reportAttributeAccessIssue]
            where={"id": execution_id},
            data={
                "status": "waiting_approval",
                "variables": Json(pending_variables),
            },
        )


    async def _mark_completed(self, execution_id: str, final_state: Any) -> None:
        final_vars: dict[str, Any] = (final_state or {}).get("variables") or {}
        await self.db.workflowexecution.update(  # pyright: ignore[reportAttributeAccessIssue]
            where={"id": execution_id},
            data={
                "status": "completed",
                "output": Json(final_vars.get("finalOutput")),
                "variables": Json(final_vars),
                "nodeResults": Json(final_state.get("node_results") or {} if final_state else {}),
                "completedAt": datetime.now(UTC),
            },
        )


    async def _mark_failed(self, execution_id: str, exc: Exception) -> None:
        await self.db.workflowexecution.update(  # pyright: ignore[reportAttributeAccessIssue]
            where={"id": execution_id},
            data={
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "completedAt": datetime.now(UTC),
            },
        )


    async def _load_execution(self, execution_id: str) -> Any | None:
        return await self.db.workflowexecution.find_unique(where={"id": execution_id})  # pyright: ignore[reportAttributeAccessIssue]
```

Adjust as needed — these helpers may already exist under different names in Phase 1/7a; rename for consistency rather than duplicating.

- [ ] **Step 4: Run + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/engine/test_langgraph_executor.py -v
.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```
Expected: 2 new tests pass + all existing tests green; overall +2 (~372).

```bash
git add src/engine/langgraph_executor.py tests/unit/engine/test_langgraph_executor.py
git commit -m "feat(engine): LangGraphExecutor.resume + GraphInterrupt handling

run() now catches GraphInterrupt distinctly (instead of treating as
generic exception → failed).  On interrupt, persists:
  - status = 'waiting_approval'
  - variables._pending_approval_node + _pending_approval_prompt

New method: resume(execution_id, decision).  Called by /executions/
{id}/resume via BackgroundTasks.  Feeds decision via Command(resume=...)
to compiled.ainvoke; LangGraph loads the checkpoint (PrismaCheckpointSaver)
and continues from the paused node.  Chained pause (another user-approval
hit during resume) → mark waiting_approval again.

Factored out _prepare / _mark_waiting_approval / _mark_completed /
_mark_failed / _load_execution helpers.  run() and resume() now share
the prep + DB-update logic.

See Phase 5a spec §8, ADR-0016.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: REST endpoint — `POST /executions/{id}/resume`

**Files:**
- Modify: `src/api/executions.py`
- Create: `tests/unit/api/test_executions_resume.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/api/test_executions_resume.py`:

```python
"""Tests for POST /executions/{id}/resume (Phase 5a)."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.main import create_app


def _execution_row(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": "e1",
        "workflowId": "w1",
        "userId": "dev",
        "status": "waiting_approval",
        "threadId": "t1",
        "nodeResults": {},
        "variables": {
            "_pending_approval_node": "ua",
            "_pending_approval_prompt": "Approve?",
        },
        "input": None,
        "output": None,
        "error": None,
        "startedAt": "2026-04-21T00:00:00Z",
        "completedAt": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _client_with_execution(
    monkeypatch: pytest.MonkeyPatch,
    execution: Any | None,
) -> tuple[TestClient, MagicMock]:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()
    db = MagicMock()
    db.workflowexecution = MagicMock()
    db.workflowexecution.find_unique = AsyncMock(return_value=execution)
    db.workflowexecution.update = AsyncMock(return_value=execution)
    db.approval = MagicMock()
    db.approval.create = AsyncMock()
    app.state.db = db
    app.state.checkpointer = MagicMock()
    return TestClient(app), db


def test_resume_approved_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client_with_execution(monkeypatch, _execution_row())
    resp = client.post(
        "/executions/e1/resume",
        json={"decision": "approved", "note": "lgtm"},
    )
    assert resp.status_code == 200, resp.text
    db.approval.create.assert_awaited_once()
    call = db.approval.create.await_args
    created = call.kwargs["data"]
    assert created["decision"] == "approved"
    assert created["note"] == "lgtm"
    assert created["nodeId"] == "ua"
    assert created["executionId"] == "e1"
    assert created["approverUserId"] == "dev"  # dev-mode fallback


def test_resume_rejected_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client_with_execution(monkeypatch, _execution_row())
    resp = client.post(
        "/executions/e1/resume",
        json={"decision": "rejected"},
    )
    assert resp.status_code == 200
    call = db.approval.create.await_args
    created = call.kwargs["data"]
    assert created["decision"] == "rejected"
    assert created["note"] is None


def test_resume_execution_not_found_404(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client_with_execution(monkeypatch, None)
    resp = client.post(
        "/executions/ghost/resume",
        json={"decision": "approved"},
    )
    assert resp.status_code == 404


def test_resume_wrong_status_409(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client_with_execution(
        monkeypatch, _execution_row(status="completed")
    )
    resp = client.post(
        "/executions/e1/resume",
        json={"decision": "approved"},
    )
    assert resp.status_code == 409


def test_resume_missing_pending_node_500(monkeypatch: pytest.MonkeyPatch) -> None:
    """Execution is waiting_approval but variables don't have _pending_approval_node."""
    client, _ = _client_with_execution(
        monkeypatch, _execution_row(variables={})
    )
    resp = client.post(
        "/executions/e1/resume",
        json={"decision": "approved"},
    )
    assert resp.status_code == 500


def test_resume_invalid_decision_422(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client_with_execution(monkeypatch, _execution_row())
    resp = client.post(
        "/executions/e1/resume",
        json={"decision": "maybe"},
    )
    assert resp.status_code == 422


def test_resume_marks_execution_running_before_scheduling_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The endpoint flips status to 'running' BEFORE the background task.
    This prevents polling from seeing stale 'waiting_approval' state."""
    client, db = _client_with_execution(monkeypatch, _execution_row())
    resp = client.post("/executions/e1/resume", json={"decision": "approved"})
    assert resp.status_code == 200
    # At least one update call should flip status to 'running'
    # (update is AsyncMock with return_value=execution, so check the call args)
    assert db.workflowexecution.update.await_count >= 1
    # The first update flips status to 'running'
    first_update = db.workflowexecution.update.await_args_list[0]
    assert first_update.kwargs["data"]["status"] == "running"
```

- [ ] **Step 2: Verify fail**

```bash
.venv/Scripts/python -m pytest tests/unit/api/test_executions_resume.py -v
```

- [ ] **Step 3: Modify `src/api/executions.py`**

Read the file. At the top, add imports if missing:

```python
from enum import Enum

from fastapi import BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from src.engine.langgraph_executor import LangGraphExecutor
from src.security.auth import get_current_user_id
from src.storage.db import get_checkpointer
```

Add the enum + request model near existing Pydantic models:

```python
class ResumeDecision(str, Enum):
    approved = "approved"
    rejected = "rejected"


class ResumeRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    decision: ResumeDecision
    note: str | None = None
```

Add the endpoint handler (after existing handlers):

```python
@router.post("/executions/{execution_id}/resume", response_model=ExecutionRead)
async def resume_execution(
    execution_id: str,
    payload: ResumeRequest,
    background: BackgroundTasks,
    user_id: str = Depends(get_current_user_id),
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    checkpointer: Any = Depends(get_checkpointer),
) -> ExecutionRead:
    execution = await db.workflowexecution.find_unique(where={"id": execution_id})  # pyright: ignore[reportAttributeAccessIssue]
    if execution is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"execution {execution_id!r} not found",
        )
    if execution.status != "waiting_approval":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"execution is {execution.status!r}, not 'waiting_approval'",
        )

    pending_node_id = (execution.variables or {}).get("_pending_approval_node")
    if pending_node_id is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="execution is waiting_approval but has no _pending_approval_node",
        )

    # Audit trail
    await db.approval.create(  # pyright: ignore[reportAttributeAccessIssue]
        data={
            "executionId": execution_id,
            "nodeId": pending_node_id,
            "approverUserId": user_id,
            "decision": payload.decision.value,
            "note": payload.note,
        }
    )

    # Flip status to 'running' BEFORE scheduling the background task,
    # so polling sees consistent state while the background work runs.
    await db.workflowexecution.update(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": execution_id},
        data={"status": "running"},
    )

    # Schedule the resume work
    orchestrator = LangGraphExecutor(db, checkpointer)
    background.add_task(orchestrator.resume, execution_id, payload.decision.value)

    # Return the updated execution (with status='running')
    updated = await db.workflowexecution.find_unique(where={"id": execution_id})  # pyright: ignore[reportAttributeAccessIssue]
    if updated is None:
        # Race with delete — treat as 404
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"execution {execution_id!r} not found after update",
        )
    return _to_execution_read(updated)
```

The existing `_to_execution_read` helper in executions.py already handles conversion from Prisma row → `ExecutionRead`; reuse it. If the helper doesn't exist (perhaps the endpoint uses inline `ExecutionRead.model_validate(...)`), use that pattern.

- [ ] **Step 4: Run + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/api/test_executions_resume.py -v
.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```
Expected: 7 new tests pass; overall +7 (~379).

```bash
git add src/api/executions.py tests/unit/api/test_executions_resume.py
git commit -m "feat(api): POST /executions/{id}/resume

Accepts {decision: 'approved'|'rejected', note?: str}.
  - 404 on unknown execution_id
  - 409 on execution.status != 'waiting_approval'
  - 422 (Pydantic) on invalid decision value
  - 500 on missing _pending_approval_node (invariant violation)
  - 200 happy path: writes Approval row, flips status to 'running',
    schedules LangGraphExecutor.resume via BackgroundTasks, returns
    updated execution

user_id comes from Depends(get_current_user_id) — Phase 7a auth;
any authenticated user may resume in 5a (RBAC is Phase 7b+).

Status flip to 'running' happens BEFORE the BackgroundTask, so polling
sees consistent state.

See Phase 5a spec §9, ADR-0016.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Pass-through — verify no surprises in the pipeline

**Sanity check task.** No production code changes. Read through the stack once:

- [ ] **Step 1: Verify `UserApprovalNode` is in the `WorkflowNode` discriminated union** (should be from Phase 1 ADR-0002). Read `src/engine/workflow.py` — confirm `UserApprovalNode` is in the union.

- [ ] **Step 2: Verify `PrismaCheckpointSaver` doesn't need changes.** LangGraph's `interrupt()` writes a checkpoint via the standard `put` method — Phase 1's saver implements it. No changes.

- [ ] **Step 3: Verify `get_checkpointer` dependency exists in `src/storage/db.py`.** Phase 1 defined it. Confirm it's importable.

- [ ] **Step 4: Run full suite**

```bash
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```
Expected: all green.

- [ ] **Step 5: No commit** (nothing changed). Skip to Task 7.

---

## Task 7: Integration test — approved path

**File:**
- Create: `tests/integration/test_user_approval_approved.py`

- [ ] **Step 1: Write**

Create `tests/integration/test_user_approval_approved.py`:

```python
"""Integration — Start → UserApproval → End (approved path).

Real Neon.  Register workflow → start execution → poll until
waiting_approval → POST /resume {approved} → poll until completed.
Assert Approval row exists with decision=approved.
"""

import asyncio
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def _poll_until_status(
    client: AsyncClient,
    execution_id: str,
    target: set[str],
    timeout: float = 30.0,
) -> dict[str, object]:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        resp = await client.get(f"/executions/{execution_id}")
        body: dict[str, object] = resp.json()
        if body["status"] in target:
            return body
        await asyncio.sleep(0.2)
    raise AssertionError(
        f"Execution {execution_id} did not reach {target} within {timeout}s"
    )


async def test_user_approval_approved_path(client: AsyncClient, app: FastAPI) -> None:
    db: Any = app.state.db

    wf = await client.post(
        "/workflows",
        json={
            "name": "Phase 5a user-approval (approved path)",
            "nodes": [
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                {
                    "id": "ua",
                    "type": "user-approval",
                    "position": {"x": 100, "y": 0},
                    "data": {"label": "UA", "prompt": "Please approve"},
                },
                {
                    "id": "ok",
                    "type": "set-state",
                    "position": {"x": 200, "y": 0},
                    "data": {"label": "ok", "stateKey": "result", "stateValue": "approved"},
                },
                {
                    "id": "no",
                    "type": "set-state",
                    "position": {"x": 200, "y": 100},
                    "data": {"label": "no", "stateKey": "result", "stateValue": "rejected"},
                },
                {"id": "e", "type": "end", "position": {"x": 300, "y": 0}, "data": {"label": "E"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "ua"},
                {"id": "e2", "source": "ua", "target": "ok", "branch": "approved"},
                {"id": "e3", "source": "ua", "target": "no", "branch": "rejected"},
                {"id": "e4", "source": "ok", "target": "e"},
                {"id": "e5", "source": "no", "target": "e"},
            ],
        },
    )
    assert wf.status_code == 201, wf.text
    start = await client.post(
        "/executions", json={"workflowId": wf.json()["id"], "input": ""}
    )
    execution_id = start.json()["id"]

    # Poll until pause
    paused = await _poll_until_status(
        client, execution_id, {"waiting_approval", "failed", "completed"}
    )
    assert paused["status"] == "waiting_approval", f"Got: {paused}"

    variables = paused.get("variables") or {}
    assert isinstance(variables, dict)
    assert variables.get("_pending_approval_node") == "ua"

    # Submit approval
    resume = await client.post(
        f"/executions/{execution_id}/resume",
        json={"decision": "approved", "note": "looks good"},
    )
    assert resume.status_code == 200, resume.text

    # Poll until completion
    done = await _poll_until_status(client, execution_id, {"completed", "failed"})
    assert done["status"] == "completed", f"Got: {done}"

    final_vars = done.get("variables") or {}
    assert isinstance(final_vars, dict)
    assert final_vars.get("result") == "approved"

    # Approval row exists
    approvals = await db.approval.find_many(where={"executionId": execution_id})
    assert len(approvals) == 1
    assert approvals[0].decision == "approved"
    assert approvals[0].note == "looks good"
    assert approvals[0].nodeId == "ua"
```

- [ ] **Step 2: Quality gates + commit**

```bash
.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
.venv/Scripts/python -m pytest tests/integration/test_user_approval_approved.py --collect-only -q
```
Expected: 1 test collected.

```bash
git add tests/integration/test_user_approval_approved.py
git commit -m "test(integration): user-approval approved path

Real Neon.  Register Start → UserApproval → [ok | no] → End workflow,
start execution, poll until waiting_approval (verifies
interrupt+persist), submit /resume {approved}, poll until completed
(verifies resume+Command(resume=...)), assert:
  - variables.result == 'approved' (approved branch traversed)
  - exactly one Approval row with decision='approved' + note='looks good'

See Phase 5a spec §12.2.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Integration test — rejected path

**File:**
- Create: `tests/integration/test_user_approval_rejected.py`

- [ ] **Step 1: Write**

Create `tests/integration/test_user_approval_rejected.py`. Same shape as Task 7's test but submits `{decision: "rejected"}` and asserts `variables.result == "rejected"`.

```python
"""Integration — Start → UserApproval → End (rejected path)."""

import asyncio
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def _poll_until_status(
    client: AsyncClient,
    execution_id: str,
    target: set[str],
    timeout: float = 30.0,
) -> dict[str, object]:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        resp = await client.get(f"/executions/{execution_id}")
        body: dict[str, object] = resp.json()
        if body["status"] in target:
            return body
        await asyncio.sleep(0.2)
    raise AssertionError(
        f"Execution {execution_id} did not reach {target} within {timeout}s"
    )


async def test_user_approval_rejected_path(client: AsyncClient, app: FastAPI) -> None:
    db: Any = app.state.db

    wf = await client.post(
        "/workflows",
        json={
            "name": "Phase 5a user-approval (rejected path)",
            "nodes": [
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                {
                    "id": "ua",
                    "type": "user-approval",
                    "position": {"x": 100, "y": 0},
                    "data": {"label": "UA", "prompt": "Please approve"},
                },
                {
                    "id": "ok",
                    "type": "set-state",
                    "position": {"x": 200, "y": 0},
                    "data": {"label": "ok", "stateKey": "result", "stateValue": "approved"},
                },
                {
                    "id": "no",
                    "type": "set-state",
                    "position": {"x": 200, "y": 100},
                    "data": {"label": "no", "stateKey": "result", "stateValue": "rejected"},
                },
                {"id": "e", "type": "end", "position": {"x": 300, "y": 0}, "data": {"label": "E"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "ua"},
                {"id": "e2", "source": "ua", "target": "ok", "branch": "approved"},
                {"id": "e3", "source": "ua", "target": "no", "branch": "rejected"},
                {"id": "e4", "source": "ok", "target": "e"},
                {"id": "e5", "source": "no", "target": "e"},
            ],
        },
    )
    assert wf.status_code == 201, wf.text
    start = await client.post(
        "/executions", json={"workflowId": wf.json()["id"], "input": ""}
    )
    execution_id = start.json()["id"]

    paused = await _poll_until_status(
        client, execution_id, {"waiting_approval", "failed", "completed"}
    )
    assert paused["status"] == "waiting_approval"

    resume = await client.post(
        f"/executions/{execution_id}/resume",
        json={"decision": "rejected", "note": "needs revisions"},
    )
    assert resume.status_code == 200

    done = await _poll_until_status(client, execution_id, {"completed", "failed"})
    assert done["status"] == "completed", f"Got: {done}"

    final_vars = done.get("variables") or {}
    assert isinstance(final_vars, dict)
    assert final_vars.get("result") == "rejected"

    approvals = await db.approval.find_many(where={"executionId": execution_id})
    assert len(approvals) == 1
    assert approvals[0].decision == "rejected"
    assert approvals[0].note == "needs revisions"
```

- [ ] **Step 2: Commit**

```bash
git add tests/integration/test_user_approval_rejected.py
git commit -m "test(integration): user-approval rejected path

Same workflow shape as the approved test; submits /resume {rejected}.
Verifies:
  - variables.result == 'rejected' (rejected branch traversed)
  - Approval row has decision='rejected' + note='needs revisions'
  - Execution completes normally (status='completed') — the rejected
    branch is a normal flow, not a failure

See Phase 5a spec §12.2.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Reserved — real-Neon integration fix-up

If the integration suite reveals bugs (same pattern as Phases 3a/3b/4a/4b/7a), fix in a dedicated commit titled `"fix(phase-5a): <N> bugs caught by real-API integration testing"`. If no bugs, skip.

---

## Task 10: Phase-exit — CHANGELOG + CLAUDE.md + ADR-0016 backfill

**Authorized:** `CHANGELOG.md`, `CLAUDE.md`, `docs/design/decisions.md`.

- [ ] **Step 1: Verify exit checklist**

```bash
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m ruff format --check src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
grep -rn "NotImplementedError" src/ | grep -i "user-approval"
```
Last grep should return nothing.

Run the integration suite (controller, not subagent):
```bash
.venv/Scripts/python -c "
from dotenv import load_dotenv
import os, subprocess, sys
load_dotenv('.env', override=True)
os.environ['TEST_DATABASE_URL'] = os.environ.get('DATABASE_URL', '')
r = subprocess.run(
    [sys.executable, '-m', 'pytest', '-v', '--tb=short', '--no-cov',
     '-m', 'integration',
     'tests/integration/test_user_approval_approved.py',
     'tests/integration/test_user_approval_rejected.py'],
    cwd='.'
)
sys.exit(r.returncode)
"
```
Both pass → proceed. Any fail → back to Task 9.

- [ ] **Step 2: Update CHANGELOG.md**

Insert above the Phase 7a section:

```markdown
### Phase 5a — User-approval + interrupt/resume (2026-04-21)

#### Added
- [Phase 5a design spec](docs/superpowers/specs/2026-04-21-phase-5a-user-approval-design.md) + ADR-0016.
- Prisma `Approval` table + `ApprovalDecision` enum (per-user-approval-decision audit row; cascade-deletes with execution).
- `src/executors/user_approval.py` — `UserApprovalExecutor` calls LangGraph's `interrupt()`; records `_approval_<node_id>` on resume; `UserApprovalNodeError` for invalid decisions.
- `src/engine/graph_builder.py` — extends Phase 4b's conditional-edges pass with `user-approval` routing (branches `{approved, rejected}`); `_route_user_approval` reads `_approval_<node_id>` from variables.
- `src/engine/langgraph_executor.py` — catches `GraphInterrupt` and marks `waiting_approval` + persists `_pending_approval_node` / `_pending_approval_prompt`; new `resume(execution_id, decision)` method calls `compiled.ainvoke(Command(resume=decision))` via LangGraph's Command primitive.
- `POST /executions/{id}/resume` — new endpoint accepts `{decision, note?}`; 404 / 409 / 422 / 500 error paths per spec.
- Integration tests: approved path + rejected path (real Neon); assert `Approval` row contents and final variable state.

#### Changed
- Status transitions: `running → waiting_approval → running → completed|failed`. No new top-level status; rejected branches complete normally.
- Sentinel tests migrate: `user-approval`/`Phase 5` → `guardrails`/`Phase 6` across three test files (graph_builder, langgraph_executor, executors/test_registry).
- `LangGraphExecutor` refactored: `_prepare` / `_mark_*` helpers factor out the common prep + DB-update logic between `run()` and `resume()`.

#### Deliberate design choices (see ADR-0016)
- Checkpoint + resume (LangGraph `interrupt()`), not blocking request or re-run-from-scratch. Pins no API workers; preserves side-effect correctness.
- `Approval` as dedicated Prisma table, not inline JSON on `WorkflowExecution.variables`. Enables audit trail + future multi-approver.
- Status flip to `running` happens BEFORE the BackgroundTask, so polling clients never see stale `waiting_approval` while the resume is actually in flight.
- Rejected branch is ordinary graph routing — no special "rejected" top-level status. The decision itself lives on the Approval table.
- Any authenticated user may resume in 5a (dev-mode fallback returns `'dev'` per ADR-0015). RBAC is Phase 7b+.

### Phase 7a — Deployment-mode toggle + auth middleware (2026-04-21)
```

- [ ] **Step 3: Update CLAUDE.md phase table**

Change:
```markdown
| 7a — Deployment-mode toggle + auth middleware | ✅ Complete | standalone vs embedded; User table; dev-mode fallback (ADR-0015); brought forward from Phase 7 |
| 5 — User-approval + SSE streaming | ⏭ Next | LangGraph `interrupt()` — now has authenticated user context thanks to 7a |
```
To:
```markdown
| 7a — Deployment-mode toggle + auth middleware | ✅ Complete | standalone vs embedded; User table; dev-mode fallback (ADR-0015); brought forward from Phase 7 |
| 5a — User-approval + interrupt/resume | ✅ Complete | interrupt() pause; /executions/{id}/resume; Approval table; both branches verified against real Neon |
| 5b — SSE streaming | ⏭ Next | real-time execution events (GET /executions/{id}/events) |
```

- [ ] **Step 4: Backfill ADR-0016 `Implemented by`**

Replace `**Implemented by.** Phase 5a (commits TBD).` with the actual range.

```bash
git log --oneline 7c5a51a..HEAD  # starts just after Phase 7a exit
```

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md CLAUDE.md docs/design/decisions.md
git commit -m "docs(phase-5a): mark Phase 5a complete

User-approval + interrupt/resume shipped.  UserApprovalExecutor calls
LangGraph's interrupt(); LangGraphExecutor.run catches GraphInterrupt
and marks waiting_approval; /executions/{id}/resume writes Approval row
+ BackgroundTask-resumes via compiled.ainvoke(Command(resume=...)).

Real-Neon integration: approved path + rejected path both green.
Approval table stores every decision with approver user id + optional
note.

Phase 5b (SSE streaming) is next — adds real-time UX on top of the
same execution model.

ADR-0016 Implemented by backfilled.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Spec coverage self-review

| Spec section | Tasks |
|---|---|
| §5 Approval table | Task 1 |
| §6 UserApprovalExecutor | Task 2 |
| §7 graph_builder routing | Task 3 |
| §8 LangGraphExecutor.resume | Task 4 |
| §9 /executions/{id}/resume | Task 5 |
| §12.1 unit tests | Tasks 2, 3, 4, 5 |
| §12.2 integration | Tasks 7, 8 |
| §13 phase-exit | Task 10 |

No placeholder steps. Type consistency:
- `decision: str` (passed to `interrupt()`, stored in Approval, keyed off `_approval_<node_id>` variable) — same shape across Tasks 2, 3, 4, 5.
- `Approval` table columns match usage in Task 5's endpoint + Task 7/8's assertions.
- `_pending_approval_node` + `_pending_approval_prompt` key names consistent between Task 4 (writes) and Task 5 (reads).

---

## Execution handoff

Plan saved. Controller proceeds to `superpowers:subagent-driven-development` for Tasks 1–10. Same cadence as Phases 1–7a. Task 10 runs integration suite against real Neon before phase-exit.
