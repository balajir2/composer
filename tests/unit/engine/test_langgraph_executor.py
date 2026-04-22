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

from src.engine.langgraph_executor import LangGraphExecutor


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


async def test_start_execution_creates_row_with_running_status() -> None:
    db = MagicMock()
    db.workflowexecution = MagicMock()
    db.workflowexecution.create = AsyncMock(
        return_value=SimpleNamespace(
            id="ex1",
            workflowId="wf1",
            status="running",
            threadId="t1",
        )
    )
    executor = LangGraphExecutor(db=db, checkpointer=MemorySaver())
    row = await executor.start_execution(workflow_id="wf1", input={"msg": "hi"}, user_id="dev")
    assert row.id == "ex1"
    assert row.status == "running"
    db.workflowexecution.create.assert_awaited_once()
    assert db.workflowexecution.create.await_args is not None
    create_kwargs = db.workflowexecution.create.await_args.kwargs["data"]
    assert create_kwargs["workflowId"] == "wf1"
    assert create_kwargs["userId"] == "dev"
    assert create_kwargs["status"] == "running"
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
    """run() emits status-change(running) before ainvoke and status-change(completed)
    + close() after."""
    from types import SimpleNamespace
    from typing import Any as _Any
    from unittest.mock import AsyncMock, MagicMock

    from src.engine import langgraph_executor as lge_mod
    from src.engine.events import ExecutionEventBus
    from src.engine.langgraph_executor import LangGraphExecutor

    bus = ExecutionEventBus()
    queue = await bus.subscribe("e1")

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

    orch = LangGraphExecutor(db, MagicMock(), event_bus=bus)
    await orch.run("e1")

    events: list[_Any] = []
    import asyncio as _aio

    while True:
        try:
            ev = await _aio.wait_for(queue.get(), timeout=0.1)
        except TimeoutError:
            break
        if ev is None:
            break
        events.append(ev)

    types = [e.type for e in events]
    statuses = [e.payload.get("status") for e in events if e.type == "status-change"]
    assert "status-change" in types
    assert "running" in statuses
    assert "completed" in statuses


async def test_run_emits_approval_pending_on_pause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace
    from typing import Any as _Any
    from typing import ClassVar
    from unittest.mock import AsyncMock, MagicMock

    from src.engine import langgraph_executor as lge_mod
    from src.engine.events import ExecutionEventBus
    from src.engine.langgraph_executor import LangGraphExecutor

    bus = ExecutionEventBus()
    queue = await bus.subscribe("e1")

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

    orch = LangGraphExecutor(db, MagicMock(), event_bus=bus)
    await orch.run("e1")

    events: list[_Any] = []
    import asyncio as _aio

    while True:
        try:
            ev = await _aio.wait_for(queue.get(), timeout=0.1)
        except TimeoutError:
            break
        if ev is None:
            break
        events.append(ev)

    types = [e.type for e in events]
    assert "approval-pending" in types
    pending = next(e for e in events if e.type == "approval-pending")
    assert pending.payload["node_id"] == "ua"
    assert pending.payload["prompt"] == "Approve?"
    waiting = [
        e
        for e in events
        if e.type == "status-change" and e.payload.get("status") == "waiting_approval"
    ]
    assert waiting


async def test_run_emits_failed_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace
    from typing import Any as _Any
    from unittest.mock import AsyncMock, MagicMock

    from src.engine.events import ExecutionEventBus
    from src.engine.langgraph_executor import LangGraphExecutor

    bus = ExecutionEventBus()
    queue = await bus.subscribe("e1")

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

    orch = LangGraphExecutor(db, MagicMock(), event_bus=bus)
    await orch.run("e1")

    events: list[_Any] = []
    import asyncio as _aio

    while True:
        try:
            ev = await _aio.wait_for(queue.get(), timeout=0.1)
        except TimeoutError:
            break
        if ev is None:
            break
        events.append(ev)

    statuses = [e.payload.get("status") for e in events if e.type == "status-change"]
    assert "failed" in statuses
