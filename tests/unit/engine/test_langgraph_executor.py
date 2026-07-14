"""Tests for the LangGraphExecutor orchestrator.

Unit-level: mocks the Prisma client and verifies the orchestrator
creates the right rows, drives the compiled graph, and persists the
right fields on completion/failure.
"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langgraph.checkpoint.memory import MemorySaver

from src.engine.events import ExecutionEvent
from src.engine.langgraph_executor import LangGraphExecutor


class _FakeEventStore:
    """In-memory stand-in for PostgresEventStore.append — no real Postgres
    round-trip, just an append-only list with an assigned sequence number,
    so these unit tests don't need a live database."""

    def __init__(self) -> None:
        self.events: list[ExecutionEvent] = []

    async def append(self, event: ExecutionEvent) -> int:
        seq = len(self.events) + 1
        self.events.append(event)
        return seq


@pytest.fixture(autouse=True)
def _patch_notify(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:  # pyright: ignore[reportUnusedFunction]
    """LangGraphExecutor._emit calls notify_execution_event(execution_id, seq=seq)
    after every append — patch it to a no-op so unit tests never attempt a
    real asyncpg connection."""
    mock = AsyncMock()
    monkeypatch.setattr("src.engine.langgraph_executor.notify_execution_event", mock)
    return mock


def _workflow_row(workflow_dict: dict[str, Any]) -> SimpleNamespace:
    """Simulate a prisma.models.Workflow row with the fields the executor reads."""
    return SimpleNamespace(
        id=workflow_dict.get("id", "wf1"),
        name=workflow_dict["name"],
        nodes=workflow_dict["nodes"],
        edges=workflow_dict["edges"],
        # workflow row also has workflowId attribute on derived types; executor
        # reads the camelCase form from prisma client (execution.workflowId)
    )


def _start_to_end_workflow_dict() -> dict[str, Any]:
    return {
        "id": "wf1",
        "name": "Smoke",
        "nodes": [
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {"id": "e", "type": "end", "position": {"x": 100, "y": 0}, "data": {"label": "E"}},
        ],
        "edges": [{"id": "edge1", "source": "s", "target": "e"}],
    }


async def test_start_execution_creates_row_with_queued_status() -> None:
    """P1-2: rows start life `queued`, not `running` — the row only
    becomes `running` once POST /internal/claim-and-run actually claims
    it (src/api/internal.py), since the caller now enqueues a Cloud Task
    rather than running the execution inline."""
    db = MagicMock()
    db.workflowexecution = MagicMock()
    db.workflowexecution.create = AsyncMock(
        return_value=SimpleNamespace(
            id="ex1",
            workflowId="wf1",
            status="queued",
            threadId="t1",
        )
    )
    executor = LangGraphExecutor(db=db, checkpointer=MemorySaver())
    row = await executor.start_execution(workflow_id="wf1", input={"msg": "hi"}, user_id="dev")
    assert row.id == "ex1"
    assert row.status == "queued"
    db.workflowexecution.create.assert_awaited_once()
    assert db.workflowexecution.create.await_args is not None
    create_kwargs = db.workflowexecution.create.await_args.kwargs["data"]
    assert create_kwargs["workflowId"] == "wf1"
    assert create_kwargs["userId"] == "dev"
    assert create_kwargs["status"] == "queued"
    assert isinstance(create_kwargs["threadId"], str) and len(create_kwargs["threadId"]) > 0


async def test_run_completes_start_to_end() -> None:
    wf_dict = _start_to_end_workflow_dict()
    db = MagicMock()
    db.workflow = MagicMock()
    db.workflow.find_unique = AsyncMock(return_value=_workflow_row(wf_dict))
    db.workflowexecution = MagicMock()
    db.workflowexecution.find_unique = AsyncMock(
        return_value=SimpleNamespace(
            id="ex1",
            workflowId="wf1",
            threadId="t1",
            input="hello",
            userId=None,  # Phase 7a: nullable; executor checks before setting state
        )
    )
    db.workflowexecution.update = AsyncMock()

    executor = LangGraphExecutor(db=db, checkpointer=MemorySaver())
    await executor.run("ex1")

    db.workflowexecution.update.assert_awaited_once()
    assert db.workflowexecution.update.await_args is not None
    update_kwargs = db.workflowexecution.update.await_args.kwargs["data"]
    assert update_kwargs["status"] == "completed"
    # output/variables/nodeResults are wrapped in prisma.Json(...) before being
    # passed to Prisma — unwrap via the .data attribute for assertion.
    assert update_kwargs["output"].data == "hello"  # start sets lastOutput to parsed input
    assert "completedAt" in update_kwargs


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


async def test_run_marks_failed_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    # Simulate a graph-build failure by patching build_graph to raise.
    # The vector-db executor now ships in Phase 6e; this test covers the general
    # "run() catches any exception and marks the execution failed" contract.
    from src.engine import langgraph_executor as lge_mod

    def _boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("simulated build failure")

    monkeypatch.setattr(lge_mod, "build_graph", _boom)

    wf_dict = _start_to_end_workflow_dict()
    db = MagicMock()
    db.workflow = MagicMock()
    db.workflow.find_unique = AsyncMock(return_value=_workflow_row(wf_dict))
    db.workflowexecution = MagicMock()
    db.workflowexecution.find_unique = AsyncMock(
        return_value=SimpleNamespace(
            id="ex1",
            workflowId="wf1",
            threadId="t1",
            input="",
            userId=None,  # Phase 7a: nullable; executor checks before setting state
        )
    )
    db.workflowexecution.update = AsyncMock()

    executor = LangGraphExecutor(db=db, checkpointer=MemorySaver())
    await executor.run("ex1")

    db.workflowexecution.update.assert_awaited_once()
    assert db.workflowexecution.update.await_args is not None
    update_kwargs = db.workflowexecution.update.await_args.kwargs["data"]
    assert update_kwargs["status"] == "failed"
    assert "simulated build failure" in update_kwargs["error"]


async def test_run_marks_waiting_approval_on_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the graph pauses at a user-approval node, the execution is marked
    waiting_approval with _pending_approval_node + _pending_approval_prompt
    in variables.

    Production LangGraph >= 0.2.x does NOT propagate GraphInterrupt out of
    ainvoke — the Pregel runtime catches it internally, persists a checkpoint,
    and returns cleanly.  The executor detects the pause via aget_state():
    snapshot.next is a non-empty tuple when the graph is paused.
    """
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
                {
                    "id": "ua",
                    "type": "user-approval",
                    "position": {"x": 0, "y": 0},
                    "data": {"label": "UA", "approvalMessage": "Approve?"},
                },
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

    from src.engine import langgraph_executor as lge_mod

    # Stub snapshot: snapshot.next is non-empty → paused
    _fake_interrupt = SimpleNamespace(value={"node_id": "ua", "prompt": "Approve?"})
    _fake_task = SimpleNamespace(interrupts=(_fake_interrupt,))
    _fake_snapshot = SimpleNamespace(next=("ua",), tasks=(_fake_task,))

    class _FakeCompiled:
        async def ainvoke(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            # Production LangGraph returns state dict, not raises GraphInterrupt
            return {"variables": {"input": "", "lastOutput": ""}, "node_results": {"s": {}}}

        async def aget_state(self, *args: Any, **kwargs: Any) -> Any:
            return _fake_snapshot

    monkeypatch.setattr(lge_mod, "build_graph", lambda *a, **kw: _FakeCompiled())  # pyright: ignore[reportUnknownLambdaType]

    orchestrator = LangGraphExecutor(db, MagicMock())
    await orchestrator.run("e1")

    assert any(call["data"].get("status") == "waiting_approval" for call in update_calls), (
        f"Expected waiting_approval update; got: {update_calls}"
    )
    # Verify the pending markers are present in variables
    waiting_call = next(
        call for call in update_calls if call["data"].get("status") == "waiting_approval"
    )
    saved_vars: dict[str, Any] = waiting_call["data"]["variables"].data
    assert saved_vars.get("_pending_approval_node") == "ua", f"Got variables: {saved_vars}"
    assert saved_vars.get("_pending_approval_prompt") == "Approve?", f"Got variables: {saved_vars}"


async def test_resume_approved_continues_to_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """resume() calls compiled.ainvoke(Command(resume=...)) and marks completed."""
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

    # Stub snapshot: snapshot.next is empty → completed (no chained pause)
    _completed_snapshot = SimpleNamespace(next=(), tasks=())

    class _FakeCompiled:
        async def ainvoke(self, state: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
            return {"variables": {"lastOutput": "done"}, "node_results": {}}

        async def aget_state(self, *args: Any, **kwargs: Any) -> Any:
            return _completed_snapshot

    monkeypatch.setattr(lge_mod, "build_graph", lambda *a, **kw: _FakeCompiled())  # pyright: ignore[reportUnknownLambdaType]

    orchestrator = LangGraphExecutor(db, MagicMock())
    await orchestrator.resume("e1", "approved")

    assert any(c.get("status") == "completed" for c in update_calls), update_calls


async def test_run_emits_status_change_on_start_and_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run() emits workflow_started(running) before ainvoke and workflow_completed(completed)
    after."""
    from types import SimpleNamespace
    from typing import Any as _Any
    from unittest.mock import AsyncMock, MagicMock

    from src.engine import langgraph_executor as lge_mod
    from src.engine.langgraph_executor import LangGraphExecutor

    store = _FakeEventStore()

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
            name="t",
            nodes=[
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                {"id": "e", "type": "end", "position": {"x": 0, "y": 0}, "data": {"label": "E"}},
            ],
            edges=[{"id": "e1", "source": "s", "target": "e"}],
        )
    )
    db.workflowexecution.update = AsyncMock()

    class _FakeSnap:
        next = ()
        tasks = ()

    class _FakeCompiled:
        async def ainvoke(self, *a: _Any, **kw: _Any) -> dict[str, _Any]:
            return {"variables": {}, "node_results": {}}

        async def aget_state(self, *a: _Any, **kw: _Any) -> _Any:
            return _FakeSnap()

    monkeypatch.setattr(lge_mod, "build_graph", lambda *a, **kw: _FakeCompiled())  # pyright: ignore[reportUnknownLambdaType]

    orch = LangGraphExecutor(db, MagicMock(), event_bus=store)  # pyright: ignore[reportArgumentType]
    await orch.run("e1")

    types = [e.type for e in store.events]
    assert "workflow_started" in types
    assert "workflow_completed" in types
    started_statuses = [
        e.payload.get("status") for e in store.events if e.type == "workflow_started"
    ]
    completed_statuses = [
        e.payload.get("status") for e in store.events if e.type == "workflow_completed"
    ]
    assert "running" in started_statuses
    assert "completed" in completed_statuses


async def test_run_emits_approval_pending_on_pause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace
    from typing import Any as _Any
    from typing import ClassVar
    from unittest.mock import AsyncMock, MagicMock

    from src.engine import langgraph_executor as lge_mod
    from src.engine.langgraph_executor import LangGraphExecutor

    store = _FakeEventStore()

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
            name="t",
            nodes=[
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                {
                    "id": "ua",
                    "type": "user-approval",
                    "position": {"x": 0, "y": 0},
                    "data": {"label": "UA", "approvalMessage": "Approve?"},
                },
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
    db.workflowexecution.update = AsyncMock()

    class _FakeInterrupt:
        value: ClassVar[dict[str, str]] = {"node_id": "ua", "prompt": "Approve?"}

    class _FakeTask:
        interrupts: ClassVar[tuple[_FakeInterrupt, ...]] = (_FakeInterrupt(),)

    class _FakeSnapPaused:
        next: ClassVar[tuple[str, ...]] = ("ua",)
        tasks: ClassVar[tuple[_FakeTask, ...]] = (_FakeTask(),)

    class _FakeCompiled:
        async def ainvoke(self, *a: _Any, **kw: _Any) -> dict[str, _Any]:
            return {"variables": {"input": ""}, "node_results": {}}

        async def aget_state(self, *a: _Any, **kw: _Any) -> _Any:
            return _FakeSnapPaused()

    monkeypatch.setattr(lge_mod, "build_graph", lambda *a, **kw: _FakeCompiled())  # pyright: ignore[reportUnknownLambdaType]

    orch = LangGraphExecutor(db, MagicMock(), event_bus=store)  # pyright: ignore[reportArgumentType]
    await orch.run("e1")

    types = [e.type for e in store.events]
    assert "approval_required" in types
    pending = next(e for e in store.events if e.type == "approval_required")
    assert pending.payload["node_id"] == "ua"
    assert pending.payload["prompt"] == "Approve?"
    assert pending.payload.get("status") == "waiting_approval"


async def test_run_emits_failed_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from src.engine.langgraph_executor import LangGraphExecutor

    store = _FakeEventStore()

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
    db.workflow.find_unique = AsyncMock(side_effect=RuntimeError("boom"))
    db.workflowexecution.update = AsyncMock()

    orch = LangGraphExecutor(db, MagicMock(), event_bus=store)  # pyright: ignore[reportArgumentType]
    await orch.run("e1")

    statuses = [e.payload.get("status") for e in store.events if e.type == "workflow_completed"]
    assert "failed" in statuses


async def test_run_sends_approval_email_when_approver_email_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from typing import ClassVar
    from unittest.mock import AsyncMock

    from src.engine import langgraph_executor as lge_mod

    send_mock = AsyncMock()
    monkeypatch.setattr(lge_mod, "send_approval_email", send_mock)

    db = MagicMock()
    db.workflowexecution = MagicMock()
    db.workflowexecution.find_unique = AsyncMock(
        return_value=SimpleNamespace(
            id="e1", workflowId="w1", userId="dev", threadId="t1", input=None
        )
    )
    db.workflow = MagicMock()
    db.workflow.find_unique = AsyncMock(
        return_value=SimpleNamespace(
            id="w1",
            name="t",
            nodes=[
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                {
                    "id": "ua",
                    "type": "user-approval",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": "UA",
                        "approvalMessage": "Approve?",
                        "approverEmail": "reviewer@example.com",
                    },
                },
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
    db.workflowexecution.update = AsyncMock()

    class _FakeInterrupt:
        value: ClassVar[dict[str, str]] = {
            "node_id": "ua",
            "prompt": "Approve?",
            "approver_email": "reviewer@example.com",
            "approver_cc": "",
            "attachment_path": "/tmp/composer-attachments/brd.pdf",
        }

    class _FakeTask:
        interrupts: ClassVar[tuple[_FakeInterrupt, ...]] = (_FakeInterrupt(),)

    class _FakeSnapPaused:
        next: ClassVar[tuple[str, ...]] = ("ua",)
        tasks: ClassVar[tuple[_FakeTask, ...]] = (_FakeTask(),)

    class _FakeCompiled:
        async def ainvoke(self, *a: Any, **kw: Any) -> dict[str, Any]:
            return {"variables": {"input": ""}, "node_results": {}}

        async def aget_state(self, *a: Any, **kw: Any) -> Any:
            return _FakeSnapPaused()

    monkeypatch.setattr(lge_mod, "build_graph", lambda *a, **kw: _FakeCompiled())  # pyright: ignore[reportUnknownLambdaType]

    orch = LangGraphExecutor(db, MagicMock())
    await orch.run("e1")

    send_mock.assert_awaited_once()
    assert send_mock.await_args is not None
    call_kwargs = send_mock.await_args.kwargs
    assert call_kwargs["approver_email"] == "reviewer@example.com"
    assert call_kwargs["node_id"] == "ua"
    assert call_kwargs["prompt"] == "Approve?"
    assert call_kwargs["attachment_path"] == "/tmp/composer-attachments/brd.pdf"


async def test_run_pending_since_matches_persisted_and_emailed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pending_since timestamp stamped into variables._pending_approval_since
    must be the exact same value threaded into send_approval_email's
    pending_since kwarg -- run() computes it once and shares it between the
    two calls (see LangGraphExecutor._mark_waiting_approval's docstring); a
    regression that recomputes the timestamp separately for each call would
    silently break the approval-email pause-instance guard."""
    from typing import ClassVar

    from src.engine import langgraph_executor as lge_mod

    send_mock = AsyncMock()
    monkeypatch.setattr(lge_mod, "send_approval_email", send_mock)

    db = MagicMock()
    db.workflowexecution = MagicMock()
    db.workflowexecution.find_unique = AsyncMock(
        return_value=SimpleNamespace(
            id="e1", workflowId="w1", userId="dev", threadId="t1", input=None
        )
    )
    db.workflow = MagicMock()
    db.workflow.find_unique = AsyncMock(
        return_value=SimpleNamespace(
            id="w1",
            name="t",
            nodes=[
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                {
                    "id": "ua",
                    "type": "user-approval",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": "UA",
                        "approvalMessage": "Approve?",
                        "approverEmail": "reviewer@example.com",
                    },
                },
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

    class _FakeInterrupt:
        value: ClassVar[dict[str, str]] = {
            "node_id": "ua",
            "prompt": "Approve?",
            "approver_email": "reviewer@example.com",
            "approver_cc": "",
        }

    class _FakeTask:
        interrupts: ClassVar[tuple[_FakeInterrupt, ...]] = (_FakeInterrupt(),)

    class _FakeSnapPaused:
        next: ClassVar[tuple[str, ...]] = ("ua",)
        tasks: ClassVar[tuple[_FakeTask, ...]] = (_FakeTask(),)

    class _FakeCompiled:
        async def ainvoke(self, *a: Any, **kw: Any) -> dict[str, Any]:
            return {"variables": {"input": ""}, "node_results": {}}

        async def aget_state(self, *a: Any, **kw: Any) -> Any:
            return _FakeSnapPaused()

    monkeypatch.setattr(lge_mod, "build_graph", lambda *a, **kw: _FakeCompiled())  # pyright: ignore[reportUnknownLambdaType]

    orch = LangGraphExecutor(db, MagicMock())
    await orch.run("e1")

    send_mock.assert_awaited_once()
    assert send_mock.await_args is not None
    emailed_pending_since = send_mock.await_args.kwargs["pending_since"]

    waiting_call = next(
        call for call in update_calls if call["data"].get("status") == "waiting_approval"
    )
    saved_vars: dict[str, Any] = waiting_call["data"]["variables"].data
    persisted_pending_since = saved_vars.get("_pending_approval_since")

    assert persisted_pending_since is not None
    assert persisted_pending_since == emailed_pending_since


async def test_resume_chained_pause_stamps_consistent_pending_since(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """resume()'s chained-pause branch is hit when a `while`-loop re-enters
    the same user-approval node after an earlier resume -- e.g. resuming an
    execution feeds Command(resume=...) back into the graph, which
    immediately hits ANOTHER interrupt rather than completing. This branch
    duplicates run()'s pending_since-compute + _mark_waiting_approval +
    send_approval_email sequence and previously had zero test coverage of
    its own; verify it upholds the same cross-consistency invariant."""
    from typing import ClassVar

    from src.engine import langgraph_executor as lge_mod

    send_mock = AsyncMock()
    monkeypatch.setattr(lge_mod, "send_approval_email", send_mock)

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
            name="t",
            nodes=[
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                {
                    "id": "ua",
                    "type": "user-approval",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": "UA",
                        "approvalMessage": "Approve again?",
                        "approverEmail": "reviewer@example.com",
                    },
                },
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

    # Simulates a `while`-loop re-entering the same user-approval node: after
    # resume's ainvoke(Command(resume=...)), the graph immediately hits
    # ANOTHER interrupt rather than completing -- snapshot.next is non-empty
    # again, taking resume() down its own (separately-implemented)
    # pause-handling branch rather than the completion branch.
    class _FakeInterrupt:
        value: ClassVar[dict[str, str]] = {
            "node_id": "ua",
            "prompt": "Approve again?",
            "approver_email": "reviewer@example.com",
            "approver_cc": "",
        }

    class _FakeTask:
        interrupts: ClassVar[tuple[_FakeInterrupt, ...]] = (_FakeInterrupt(),)

    class _FakeSnapPaused:
        next: ClassVar[tuple[str, ...]] = ("ua",)
        tasks: ClassVar[tuple[_FakeTask, ...]] = (_FakeTask(),)

    class _FakeCompiled:
        async def ainvoke(self, *a: Any, **kw: Any) -> dict[str, Any]:
            return {"variables": {"lastOutput": "loop-iteration"}, "node_results": {}}

        async def aget_state(self, *a: Any, **kw: Any) -> Any:
            return _FakeSnapPaused()

    monkeypatch.setattr(lge_mod, "build_graph", lambda *a, **kw: _FakeCompiled())  # pyright: ignore[reportUnknownLambdaType]

    orchestrator = LangGraphExecutor(db, MagicMock())
    await orchestrator.resume("e1", "approved")

    send_mock.assert_awaited_once()
    assert send_mock.await_args is not None
    call_kwargs = send_mock.await_args.kwargs
    assert call_kwargs["approver_email"] == "reviewer@example.com"
    assert call_kwargs["node_id"] == "ua"
    assert call_kwargs["prompt"] == "Approve again?"
    emailed_pending_since = call_kwargs["pending_since"]

    waiting_call = next(
        call for call in update_calls if call["data"].get("status") == "waiting_approval"
    )
    saved_vars: dict[str, Any] = waiting_call["data"]["variables"].data
    persisted_pending_since = saved_vars.get("_pending_approval_since")

    assert persisted_pending_since is not None
    assert persisted_pending_since == emailed_pending_since


async def test_run_completed_status_survives_emit_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1-4 regression: a telemetry-path failure (event append or NOTIFY)
    must not corrupt the already-persisted execution outcome. Before the
    fix, `_emit` raising after `_mark_completed` had already written
    status='completed' would propagate to run()'s outer except, which
    unconditionally calls `_mark_failed` -- overwriting a correct
    'completed' status with 'failed' purely because of a broken event
    log/NOTIFY connection."""
    from src.engine import langgraph_executor as lge_mod
    from src.engine.langgraph_executor import LangGraphExecutor

    class _RaisingEventStore:
        async def append(self, event: ExecutionEvent) -> int:
            raise RuntimeError("simulated Postgres event-log outage")

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

    class _FakeSnap:
        next = ()
        tasks = ()

    class _FakeCompiled:
        async def ainvoke(self, *a: Any, **kw: Any) -> dict[str, Any]:
            return {"variables": {"lastOutput": "done"}, "node_results": {}}

        async def aget_state(self, *a: Any, **kw: Any) -> Any:
            return _FakeSnap()

    monkeypatch.setattr(lge_mod, "build_graph", lambda *a, **kw: _FakeCompiled())  # pyright: ignore[reportUnknownLambdaType]

    orch = LangGraphExecutor(db, MagicMock(), event_bus=_RaisingEventStore())  # pyright: ignore[reportArgumentType]
    await orch.run("e1")

    # Exactly one DB update -- _mark_completed. If the bug were present, a
    # second update (from _mark_failed in the outer except) would follow,
    # flipping status to 'failed'.
    assert len(update_calls) == 1, update_calls
    assert update_calls[0]["status"] == "completed"
    assert not any(c.get("status") == "failed" for c in update_calls)


async def test_resume_waiting_approval_status_survives_emit_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same guard as above, but for resume()'s waiting_approval path -- an
    approver may already have a valid emailed token pointing at
    status='waiting_approval'; a broken event emit must not flip that to
    'failed' and strand the token."""
    from typing import ClassVar

    from src.engine import langgraph_executor as lge_mod

    send_mock = AsyncMock()
    monkeypatch.setattr(lge_mod, "send_approval_email", send_mock)

    class _RaisingEventStore:
        async def append(self, event: ExecutionEvent) -> int:
            raise RuntimeError("simulated Postgres event-log outage")

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
            name="t",
            nodes=[
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                {
                    "id": "ua",
                    "type": "user-approval",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": "UA",
                        "approvalMessage": "Approve again?",
                        "approverEmail": "reviewer@example.com",
                    },
                },
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
        update_calls.append(data)
        return None

    db.workflowexecution.update = _update

    class _FakeInterrupt:
        value: ClassVar[dict[str, str]] = {
            "node_id": "ua",
            "prompt": "Approve again?",
            "approver_email": "reviewer@example.com",
            "approver_cc": "",
        }

    class _FakeTask:
        interrupts: ClassVar[tuple[_FakeInterrupt, ...]] = (_FakeInterrupt(),)

    class _FakeSnapPaused:
        next: ClassVar[tuple[str, ...]] = ("ua",)
        tasks: ClassVar[tuple[_FakeTask, ...]] = (_FakeTask(),)

    class _FakeCompiled:
        async def ainvoke(self, *a: Any, **kw: Any) -> dict[str, Any]:
            return {"variables": {"lastOutput": "loop-iteration"}, "node_results": {}}

        async def aget_state(self, *a: Any, **kw: Any) -> Any:
            return _FakeSnapPaused()

    monkeypatch.setattr(lge_mod, "build_graph", lambda *a, **kw: _FakeCompiled())  # pyright: ignore[reportUnknownLambdaType]

    orchestrator = LangGraphExecutor(db, MagicMock(), event_bus=_RaisingEventStore())  # pyright: ignore[reportArgumentType]
    await orchestrator.resume("e1", "approved")

    # send_approval_email must still fire -- the approver gets a valid token --
    # and the persisted status must remain waiting_approval, not be flipped to
    # failed by the broken event store.
    send_mock.assert_awaited_once()
    assert len(update_calls) == 1, update_calls
    assert update_calls[0]["status"] == "waiting_approval"
    assert not any(c.get("status") == "failed" for c in update_calls)


async def test_mark_waiting_approval_stamps_pending_since() -> None:
    db = MagicMock()
    db.workflowexecution = MagicMock()
    update_calls: list[dict[str, Any]] = []

    async def _update(*, where: Any, data: Any) -> Any:
        update_calls.append({"where": where, "data": data})
        return None

    db.workflowexecution.update = _update

    orch = LangGraphExecutor(db, MagicMock())
    pending_since = "2026-07-11T10:00:00+00:00"
    await orch._mark_waiting_approval(  # pyright: ignore[reportPrivateUsage]
        "e1", {"node_id": "ua", "prompt": "Approve?"}, {}, pending_since
    )

    saved_vars: dict[str, Any] = update_calls[0]["data"]["variables"].data
    since = saved_vars.get("_pending_approval_since")
    assert since == pending_since
    from datetime import datetime

    datetime.fromisoformat(since)  # raises if not a valid ISO-8601 string
