"""Tests for the LangGraphExecutor orchestrator.

Unit-level: mocks the Prisma client and verifies the orchestrator
creates the right rows, drives the compiled graph, and persists the
right fields on completion/failure.
"""

import asyncio
import inspect
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from langgraph.checkpoint.memory import MemorySaver

from src.db.repos import workflows as workflows_repo
from src.engine.events import ExecutionEvent
from src.engine.execution_control import ExecutionStopped
from src.engine.langgraph_executor import LangGraphExecutor
from tests.unit.db_fakes import (
    assert_no_network_inside_session,
)
from tests.unit.execution_fakes import FakeExecutions, make_row


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
def _resume_finds_a_checkpoint(monkeypatch: pytest.MonkeyPatch) -> None:  # pyright: ignore[reportUnusedFunction]
    """These tests drive ``resume`` with fake graphs and in-memory savers that hold no checkpoint.
    Copy-on-resume and the fail-closed probe are covered in test_resume_checkpoint_sync.py."""
    monkeypatch.setattr(
        "src.engine.langgraph_executor.bring_forward", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(LangGraphExecutor, "_has_checkpoint", AsyncMock(return_value=True))
    monkeypatch.setattr(LangGraphExecutor, "_assert_barriers_carry_over", AsyncMock())


@pytest.fixture(autouse=True)
def _patch_notify(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:  # pyright: ignore[reportUnusedFunction]
    """LangGraphExecutor._emit calls notify_execution_event(execution_id, seq=seq)
    after every append — patch it to a no-op so unit tests never attempt a
    real asyncpg connection."""
    mock = AsyncMock()
    monkeypatch.setattr("src.engine.langgraph_executor.notify_execution_event", mock)
    return mock


_workflow_get: list[AsyncMock] = []
_monkeypatch: list[pytest.MonkeyPatch] = []


def _row(**kw: Any) -> SimpleNamespace:
    return make_row(**kw)


def _install(row: Any = None, **kw: Any) -> FakeExecutions:
    """Patch the executions repo the executor reads and writes through (see execution_fakes)."""
    return FakeExecutions(_monkeypatch[0], row, **kw)


def _set_workflow_get(mock: AsyncMock) -> None:
    """Make the executor's workflow read (``workflows_repo.get``) return/raise what ``mock`` does."""
    _workflow_get[:] = [mock]


@pytest.fixture(autouse=True)
def _workflow_reads(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
    """The executor reads the workflow through its own sessionmaker (no database here)."""

    async def _get(_session: object, workflow_id: str) -> Any:
        return await _workflow_get[0](workflow_id)

    _workflow_get.clear()
    _monkeypatch[:] = [monkeypatch]
    monkeypatch.setattr(workflows_repo, "get", _get)
    yield
    _workflow_get.clear()
    _monkeypatch.clear()


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
    _fake = _install()
    executor = LangGraphExecutor(sessionmaker=_fake.sessionmaker, checkpointer=MemorySaver())
    row = await executor.start_execution(workflow_id="wf1", input={"msg": "hi"}, user_id="dev")
    assert row.id == "ex1"
    assert row.status == "queued"
    assert len(_fake.created) == 1
    created = _fake.created[0]
    assert created["workflow_id"] == "wf1"
    assert created["user_id"] == "dev"
    assert created["input"] == {"msg": "hi"}
    assert created["idempotency_key"] is None
    assert isinstance(created["thread_id"], str) and len(created["thread_id"]) > 0
    # The row is committed before start_execution returns.
    assert _fake.commits == 1


async def test_run_completes_start_to_end() -> None:
    wf_dict = _start_to_end_workflow_dict()
    _set_workflow_get(AsyncMock(return_value=_workflow_row(wf_dict)))
    _fake = _install(
        _row(status="running", id="ex1", workflow_id="wf1", thread_id="t1", input="hello")
    )

    executor = LangGraphExecutor(sessionmaker=_fake.sessionmaker, checkpointer=MemorySaver())
    await executor.run("ex1")

    assert len(_fake.updates) == 1
    update_kwargs = _fake.updates[0]
    assert update_kwargs["status"] == "completed"
    # output/variables/nodeResults are wrapped in prisma.Json(...) before being
    # passed to Prisma — unwrap via the .data attribute for assertion.
    assert update_kwargs["output"] == "hello"  # start sets lastOutput to parsed input
    assert update_kwargs["now"] is not None


async def test_workflow_is_read_in_a_short_session_closed_before_the_graph_is_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.engine import langgraph_executor as lge_mod

    events: list[str] = []

    async def _get(_session: object, _workflow_id: str) -> Any:
        events.append("read")
        return _workflow_row(_start_to_end_workflow_dict())

    def _build(*_a: Any, **_k: Any) -> None:
        events.append("net:build_graph")
        raise RuntimeError("stop after build")

    monkeypatch.setattr(workflows_repo, "get", _get)
    monkeypatch.setattr(lge_mod, "build_graph", _build)
    _fake = _install(_row(workflow_id="wf1", input=""), events=events)

    await LangGraphExecutor(sessionmaker=_fake.sessionmaker, checkpointer=MemorySaver()).run("ex1")

    assert events[:11] == [
        "session:enter",
        "db:get",
        "session:exit",
        "session:enter",
        "db:get_status",
        "session:exit",
        "session:enter",
        "read",
        "session:exit",
        "net:build_graph",
        "session:enter",
    ]
    assert_no_network_inside_session(events)


class _OrderingBus:
    """Event store that records ``net:emit`` (a Postgres insert + NOTIFY in production)."""

    def __init__(self, events: list[str]) -> None:
        self._events = events

    async def append(self, event: ExecutionEvent) -> int:
        self._events.append(f"net:emit:{event.type}")
        return 1


def _ordering_setup(
    monkeypatch: pytest.MonkeyPatch,
    *,
    outcome: str,
    status_row: Any = None,
) -> tuple[LangGraphExecutor, list[str], FakeExecutions]:
    """An executor whose sessions, graph, email and emits all append to one ``events`` list."""
    from src.engine import langgraph_executor as lge_mod

    events: list[str] = []
    fake = _install(status_row or _row(workflow_id="wf1", user_id="dev", input=None), events=events)

    async def _get(_session: object, _workflow_id: str) -> Any:
        events.append("db:workflow_get")
        return _workflow_row(_start_to_end_workflow_dict())

    monkeypatch.setattr(workflows_repo, "get", _get)

    paused = outcome == "waiting_approval"
    snapshot = SimpleNamespace(
        next=("ua",) if paused else (),
        tasks=(
            SimpleNamespace(
                interrupts=(
                    SimpleNamespace(
                        value={"node_id": "ua", "prompt": "Approve?", "approver_email": "a@x.io"}
                    ),
                )
            ),
        ),
    )

    async def _ainvoke(*_a: Any, **_k: Any) -> dict[str, Any]:
        events.append("net:graph:ainvoke")
        if outcome == "failed":
            raise RuntimeError("graph failed")
        return {"variables": {"lastOutput": "x"}, "node_results": {}}

    async def _aget_state(*_a: Any, **_k: Any) -> Any:
        events.append("net:graph:aget_state")
        return snapshot

    compiled = SimpleNamespace(ainvoke=_ainvoke, aget_state=_aget_state)

    def _build(*_a: Any, **_k: Any) -> Any:
        return compiled

    monkeypatch.setattr(lge_mod, "build_graph", _build)

    async def _email(**_k: Any) -> None:
        events.append("net:email")

    monkeypatch.setattr(lge_mod, "send_approval_email", _email)
    monkeypatch.setattr(lge_mod, "notify_execution_event", AsyncMock())
    executor = LangGraphExecutor(
        sessionmaker=fake.sessionmaker,
        checkpointer=MemorySaver(),
        event_bus=cast("Any", _OrderingBus(events)),
    )
    return executor, events, fake


@pytest.mark.parametrize(
    ("method", "outcome", "terminal"),
    [
        ("run", "completed", "db:mark_completed"),
        ("run", "waiting_approval", "db:mark_waiting_approval"),
        ("run", "failed", "db:mark_failed"),
        ("resume", "completed", "db:mark_completed"),
    ],
)
async def test_no_session_is_open_across_graph_email_or_emit(
    monkeypatch: pytest.MonkeyPatch, method: str, outcome: str, terminal: str
) -> None:
    executor, events, _fake_exec = _ordering_setup(monkeypatch, outcome=outcome)

    if method == "run":
        await executor.run("ex1")
    else:
        await executor.resume("ex1", "approved")

    assert_no_network_inside_session(events)
    # Each guarded transition is its own short session: enter, the one repo call, commit, exit.
    at = events.index(terminal)
    assert events[at - 1] == "session:enter"
    assert events[at + 1 :][:2] == ["db:commit", "session:exit"]
    # ... and it comes after the graph ran, before the follow-up email/emit.
    assert events.index("net:graph:ainvoke") < at
    if outcome == "waiting_approval":
        assert events[events.index("net:email") - 1] == "session:exit"
        assert at < events.index("net:email") < events.index("net:emit:approval_required")


async def test_completed_run_sequence_is_load_check_graph_commit_emit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, events, _fake_exec = _ordering_setup(monkeypatch, outcome="completed")

    await executor.run("ex1")

    assert events == [
        "session:enter",
        "db:get",
        "session:exit",
        "session:enter",
        "db:get_status",
        "session:exit",
        "net:emit:workflow_started",
        "session:enter",
        "db:workflow_get",
        "session:exit",
        "net:graph:ainvoke",
        "net:graph:aget_state",
        "session:enter",
        "db:mark_completed",
        "db:commit",
        "session:exit",
        "net:emit:workflow_completed",
    ]


async def test_mark_completed_preserves_falsy_final_output() -> None:
    """P0-7 regression guard: a legitimate falsy final output (0, False,
    "", [], {}) must not be discarded in favor of lastOutput."""
    _fake = _install()
    executor = LangGraphExecutor(sessionmaker=_fake.sessionmaker, checkpointer=MemorySaver())

    final_state = {
        "final_outputs": {"end-1": 0},
        "variables": {"lastOutput": "should not be used"},
    }
    await executor._mark_completed("ex1", final_state)  # pyright: ignore[reportPrivateUsage]

    update_kwargs = _fake.updates[-1]
    assert update_kwargs["output"] == 0


async def test_mark_completed_falls_back_to_last_output_when_no_end_reached() -> None:
    """No End node reached (e.g. a run that errored before completion) --
    falls back to lastOutput, matching the historical fallback behavior
    for that case."""
    _fake = _install()
    executor = LangGraphExecutor(sessionmaker=_fake.sessionmaker, checkpointer=MemorySaver())

    final_state = {"final_outputs": {}, "variables": {"lastOutput": "fallback value"}}
    await executor._mark_completed("ex1", final_state)  # pyright: ignore[reportPrivateUsage]

    update_kwargs = _fake.updates[-1]
    assert update_kwargs["output"] == "fallback value"


async def test_mark_completed_persists_dict_when_multiple_ends_fire() -> None:
    """Genuinely multiple End nodes contributing -- output is the full
    dict, keyed by End node id, not a single collapsed scalar."""
    _fake = _install()
    executor = LangGraphExecutor(sessionmaker=_fake.sessionmaker, checkpointer=MemorySaver())

    final_state = {
        "final_outputs": {"end-a": "value-a", "end-b": "value-b"},
        "variables": {"lastOutput": "should not be used"},
    }
    await executor._mark_completed("ex1", final_state)  # pyright: ignore[reportPrivateUsage]

    update_kwargs = _fake.updates[-1]
    assert update_kwargs["output"] == {"end-a": "value-a", "end-b": "value-b"}


async def test_run_marks_failed_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    # Simulate a graph-build failure by patching build_graph to raise.
    # The vector-db executor now ships in Phase 6e; this test covers the general
    # "run() catches any exception and marks the execution failed" contract.
    from src.engine import langgraph_executor as lge_mod

    def _boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("simulated build failure")

    monkeypatch.setattr(lge_mod, "build_graph", _boom)

    wf_dict = _start_to_end_workflow_dict()
    _set_workflow_get(AsyncMock(return_value=_workflow_row(wf_dict)))
    _fake = _install(_row(status="running", id="ex1", workflow_id="wf1", thread_id="t1", input=""))

    executor = LangGraphExecutor(sessionmaker=_fake.sessionmaker, checkpointer=MemorySaver())
    await executor.run("ex1")

    assert len(_fake.updates) == 1
    update_kwargs = _fake.updates[0]
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
    _fake = _install(
        _row(
            status="running",
            id="e1",
            workflow_id="w1",
            user_id="dev",
            thread_id="t1",
            input=None,
        )
    )
    _set_workflow_get(
        AsyncMock(
            return_value=SimpleNamespace(
                id="w1",
                name="test",
                nodes=[
                    {
                        "id": "s",
                        "type": "start",
                        "position": {"x": 0, "y": 0},
                        "data": {"label": "S"},
                    },
                    {
                        "id": "ua",
                        "type": "user-approval",
                        "position": {"x": 0, "y": 0},
                        "data": {"label": "UA", "approvalMessage": "Approve?"},
                    },
                    {
                        "id": "a",
                        "type": "end",
                        "position": {"x": 0, "y": 0},
                        "data": {"label": "A"},
                    },
                    {
                        "id": "b",
                        "type": "end",
                        "position": {"x": 0, "y": 0},
                        "data": {"label": "B"},
                    },
                ],
                edges=[
                    {"id": "e1", "source": "s", "target": "ua"},
                    {"id": "e2", "source": "ua", "target": "a", "branch": "approved"},
                    {"id": "e3", "source": "ua", "target": "b", "branch": "rejected"},
                ],
            )
        )
    )

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

    def _fake_build_graph(*a: Any, **kw: Any) -> _FakeCompiled:
        return _FakeCompiled()

    monkeypatch.setattr(lge_mod, "build_graph", _fake_build_graph)

    orchestrator = LangGraphExecutor(sessionmaker=_fake.sessionmaker, checkpointer=MagicMock())
    await orchestrator.run("e1")

    assert any(call.get("status") == "waiting_approval" for call in _fake.updates), (
        f"Expected waiting_approval update; got: {_fake.updates}"
    )
    # Verify the pending markers are present in variables
    waiting_call = next(call for call in _fake.updates if call.get("status") == "waiting_approval")
    saved_vars: dict[str, Any] = waiting_call["variables"]
    assert saved_vars.get("_pending_approval_node") == "ua", f"Got variables: {saved_vars}"
    assert saved_vars.get("_pending_approval_prompt") == "Approve?", f"Got variables: {saved_vars}"


async def test_resume_approved_continues_to_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """resume() calls compiled.ainvoke(Command(resume=...)) and marks completed."""
    _fake = _install(
        _row(
            status="running",
            id="e1",
            workflow_id="w1",
            user_id="dev",
            thread_id="t1",
            input=None,
        )
    )
    _set_workflow_get(
        AsyncMock(
            return_value=SimpleNamespace(
                id="w1",
                name="t",
                nodes=[
                    {
                        "id": "s",
                        "type": "start",
                        "position": {"x": 0, "y": 0},
                        "data": {"label": "S"},
                    },
                    {
                        "id": "e",
                        "type": "end",
                        "position": {"x": 0, "y": 0},
                        "data": {"label": "E"},
                    },
                ],
                edges=[{"id": "e1", "source": "s", "target": "e"}],
            )
        )
    )

    from src.engine import langgraph_executor as lge_mod

    # Stub snapshot: snapshot.next is empty → completed (no chained pause)
    _completed_snapshot = SimpleNamespace(next=(), tasks=())

    class _FakeCompiled:
        async def ainvoke(self, state: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
            return {"variables": {"lastOutput": "done"}, "node_results": {}}

        async def aget_state(self, *args: Any, **kwargs: Any) -> Any:
            return _completed_snapshot

    def _fake_build_graph(*a: Any, **kw: Any) -> _FakeCompiled:
        return _FakeCompiled()

    monkeypatch.setattr(lge_mod, "build_graph", _fake_build_graph)

    orchestrator = LangGraphExecutor(sessionmaker=_fake.sessionmaker, checkpointer=MagicMock())
    await orchestrator.resume("e1", "approved")

    assert any(c.get("status") == "completed" for c in _fake.updates), _fake.updates


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

    _fake = _install(
        _row(
            status="running",
            id="e1",
            workflow_id="w1",
            user_id="dev",
            thread_id="t1",
            input=None,
        )
    )
    _set_workflow_get(
        AsyncMock(
            return_value=SimpleNamespace(
                id="w1",
                name="t",
                nodes=[
                    {
                        "id": "s",
                        "type": "start",
                        "position": {"x": 0, "y": 0},
                        "data": {"label": "S"},
                    },
                    {
                        "id": "e",
                        "type": "end",
                        "position": {"x": 0, "y": 0},
                        "data": {"label": "E"},
                    },
                ],
                edges=[{"id": "e1", "source": "s", "target": "e"}],
            )
        )
    )

    class _FakeSnap:
        next = ()
        tasks = ()

    class _FakeCompiled:
        async def ainvoke(self, *a: _Any, **kw: _Any) -> dict[str, _Any]:
            return {"variables": {}, "node_results": {}}

        async def aget_state(self, *a: _Any, **kw: _Any) -> _Any:
            return _FakeSnap()

    def _fake_build_graph(*a: Any, **kw: Any) -> _FakeCompiled:
        return _FakeCompiled()

    monkeypatch.setattr(lge_mod, "build_graph", _fake_build_graph)

    orch = LangGraphExecutor(
        sessionmaker=_fake.sessionmaker,
        checkpointer=MagicMock(),
        event_bus=cast("Any", store),
    )
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

    _fake = _install(
        _row(
            status="running",
            id="e1",
            workflow_id="w1",
            user_id="dev",
            thread_id="t1",
            input=None,
        )
    )
    _set_workflow_get(
        AsyncMock(
            return_value=SimpleNamespace(
                id="w1",
                name="t",
                nodes=[
                    {
                        "id": "s",
                        "type": "start",
                        "position": {"x": 0, "y": 0},
                        "data": {"label": "S"},
                    },
                    {
                        "id": "ua",
                        "type": "user-approval",
                        "position": {"x": 0, "y": 0},
                        "data": {"label": "UA", "approvalMessage": "Approve?"},
                    },
                    {
                        "id": "a",
                        "type": "end",
                        "position": {"x": 0, "y": 0},
                        "data": {"label": "A"},
                    },
                    {
                        "id": "b",
                        "type": "end",
                        "position": {"x": 0, "y": 0},
                        "data": {"label": "B"},
                    },
                ],
                edges=[
                    {"id": "e1", "source": "s", "target": "ua"},
                    {"id": "e2", "source": "ua", "target": "a", "branch": "approved"},
                    {"id": "e3", "source": "ua", "target": "b", "branch": "rejected"},
                ],
            )
        )
    )

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

    def _fake_build_graph(*a: Any, **kw: Any) -> _FakeCompiled:
        return _FakeCompiled()

    monkeypatch.setattr(lge_mod, "build_graph", _fake_build_graph)

    orch = LangGraphExecutor(
        sessionmaker=_fake.sessionmaker,
        checkpointer=MagicMock(),
        event_bus=cast("Any", store),
    )
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
    from unittest.mock import AsyncMock, MagicMock

    from src.engine.langgraph_executor import LangGraphExecutor

    store = _FakeEventStore()

    _fake = _install(
        _row(
            status="running",
            id="e1",
            workflow_id="w1",
            user_id="dev",
            thread_id="t1",
            input=None,
        )
    )
    _set_workflow_get(AsyncMock(side_effect=RuntimeError("boom")))

    orch = LangGraphExecutor(
        sessionmaker=_fake.sessionmaker,
        checkpointer=MagicMock(),
        event_bus=cast("Any", store),
    )
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

    _fake = _install(
        _row(status="running", id="e1", workflow_id="w1", user_id="dev", thread_id="t1", input=None)
    )
    _set_workflow_get(
        AsyncMock(
            return_value=SimpleNamespace(
                id="w1",
                name="t",
                nodes=[
                    {
                        "id": "s",
                        "type": "start",
                        "position": {"x": 0, "y": 0},
                        "data": {"label": "S"},
                    },
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
                    {
                        "id": "a",
                        "type": "end",
                        "position": {"x": 0, "y": 0},
                        "data": {"label": "A"},
                    },
                    {
                        "id": "b",
                        "type": "end",
                        "position": {"x": 0, "y": 0},
                        "data": {"label": "B"},
                    },
                ],
                edges=[
                    {"id": "e1", "source": "s", "target": "ua"},
                    {"id": "e2", "source": "ua", "target": "a", "branch": "approved"},
                    {"id": "e3", "source": "ua", "target": "b", "branch": "rejected"},
                ],
            )
        )
    )

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

    def _fake_build_graph(*a: Any, **kw: Any) -> _FakeCompiled:
        return _FakeCompiled()

    monkeypatch.setattr(lge_mod, "build_graph", _fake_build_graph)

    orch = LangGraphExecutor(sessionmaker=_fake.sessionmaker, checkpointer=MagicMock())
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

    _fake = _install(
        _row(status="running", id="e1", workflow_id="w1", user_id="dev", thread_id="t1", input=None)
    )
    _set_workflow_get(
        AsyncMock(
            return_value=SimpleNamespace(
                id="w1",
                name="t",
                nodes=[
                    {
                        "id": "s",
                        "type": "start",
                        "position": {"x": 0, "y": 0},
                        "data": {"label": "S"},
                    },
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
                    {
                        "id": "a",
                        "type": "end",
                        "position": {"x": 0, "y": 0},
                        "data": {"label": "A"},
                    },
                    {
                        "id": "b",
                        "type": "end",
                        "position": {"x": 0, "y": 0},
                        "data": {"label": "B"},
                    },
                ],
                edges=[
                    {"id": "e1", "source": "s", "target": "ua"},
                    {"id": "e2", "source": "ua", "target": "a", "branch": "approved"},
                    {"id": "e3", "source": "ua", "target": "b", "branch": "rejected"},
                ],
            )
        )
    )

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

    def _fake_build_graph(*a: Any, **kw: Any) -> _FakeCompiled:
        return _FakeCompiled()

    monkeypatch.setattr(lge_mod, "build_graph", _fake_build_graph)

    orch = LangGraphExecutor(sessionmaker=_fake.sessionmaker, checkpointer=MagicMock())
    await orch.run("e1")

    send_mock.assert_awaited_once()
    assert send_mock.await_args is not None
    emailed_pending_since = send_mock.await_args.kwargs["pending_since"]

    waiting_call = next(call for call in _fake.updates if call.get("status") == "waiting_approval")
    saved_vars: dict[str, Any] = waiting_call["variables"]
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

    _fake = _install(
        _row(
            status="running",
            id="e1",
            workflow_id="w1",
            user_id="dev",
            thread_id="t1",
            input=None,
        )
    )
    _set_workflow_get(
        AsyncMock(
            return_value=SimpleNamespace(
                id="w1",
                name="t",
                nodes=[
                    {
                        "id": "s",
                        "type": "start",
                        "position": {"x": 0, "y": 0},
                        "data": {"label": "S"},
                    },
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
                    {
                        "id": "a",
                        "type": "end",
                        "position": {"x": 0, "y": 0},
                        "data": {"label": "A"},
                    },
                    {
                        "id": "b",
                        "type": "end",
                        "position": {"x": 0, "y": 0},
                        "data": {"label": "B"},
                    },
                ],
                edges=[
                    {"id": "e1", "source": "s", "target": "ua"},
                    {"id": "e2", "source": "ua", "target": "a", "branch": "approved"},
                    {"id": "e3", "source": "ua", "target": "b", "branch": "rejected"},
                ],
            )
        )
    )

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

    def _fake_build_graph(*a: Any, **kw: Any) -> _FakeCompiled:
        return _FakeCompiled()

    monkeypatch.setattr(lge_mod, "build_graph", _fake_build_graph)

    orchestrator = LangGraphExecutor(sessionmaker=_fake.sessionmaker, checkpointer=MagicMock())
    await orchestrator.resume("e1", "approved")

    send_mock.assert_awaited_once()
    assert send_mock.await_args is not None
    call_kwargs = send_mock.await_args.kwargs
    assert call_kwargs["approver_email"] == "reviewer@example.com"
    assert call_kwargs["node_id"] == "ua"
    assert call_kwargs["prompt"] == "Approve again?"
    emailed_pending_since = call_kwargs["pending_since"]

    waiting_call = next(call for call in _fake.updates if call.get("status") == "waiting_approval")
    saved_vars: dict[str, Any] = waiting_call["variables"]
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

    _fake = _install(
        _row(
            status="running",
            id="e1",
            workflow_id="w1",
            user_id="dev",
            thread_id="t1",
            input=None,
        )
    )
    _set_workflow_get(
        AsyncMock(
            return_value=SimpleNamespace(
                id="w1",
                name="t",
                nodes=[
                    {
                        "id": "s",
                        "type": "start",
                        "position": {"x": 0, "y": 0},
                        "data": {"label": "S"},
                    },
                    {
                        "id": "e",
                        "type": "end",
                        "position": {"x": 0, "y": 0},
                        "data": {"label": "E"},
                    },
                ],
                edges=[{"id": "e1", "source": "s", "target": "e"}],
            )
        )
    )

    class _FakeSnap:
        next = ()
        tasks = ()

    class _FakeCompiled:
        async def ainvoke(self, *a: Any, **kw: Any) -> dict[str, Any]:
            return {"variables": {"lastOutput": "done"}, "node_results": {}}

        async def aget_state(self, *a: Any, **kw: Any) -> Any:
            return _FakeSnap()

    def _fake_build_graph(*a: Any, **kw: Any) -> _FakeCompiled:
        return _FakeCompiled()

    monkeypatch.setattr(lge_mod, "build_graph", _fake_build_graph)

    orch = LangGraphExecutor(
        sessionmaker=_fake.sessionmaker,
        checkpointer=MagicMock(),
        event_bus=cast("Any", _RaisingEventStore()),
    )
    await orch.run("e1")

    # Exactly one DB update -- _mark_completed. If the bug were present, a
    # second update (from _mark_failed in the outer except) would follow,
    # flipping status to 'failed'.
    assert len(_fake.updates) == 1, _fake.updates
    assert _fake.updates[0]["status"] == "completed"
    assert not any(c.get("status") == "failed" for c in _fake.updates)


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

    _fake = _install(
        _row(
            status="running",
            id="e1",
            workflow_id="w1",
            user_id="dev",
            thread_id="t1",
            input=None,
        )
    )
    _set_workflow_get(
        AsyncMock(
            return_value=SimpleNamespace(
                id="w1",
                name="t",
                nodes=[
                    {
                        "id": "s",
                        "type": "start",
                        "position": {"x": 0, "y": 0},
                        "data": {"label": "S"},
                    },
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
                    {
                        "id": "a",
                        "type": "end",
                        "position": {"x": 0, "y": 0},
                        "data": {"label": "A"},
                    },
                    {
                        "id": "b",
                        "type": "end",
                        "position": {"x": 0, "y": 0},
                        "data": {"label": "B"},
                    },
                ],
                edges=[
                    {"id": "e1", "source": "s", "target": "ua"},
                    {"id": "e2", "source": "ua", "target": "a", "branch": "approved"},
                    {"id": "e3", "source": "ua", "target": "b", "branch": "rejected"},
                ],
            )
        )
    )

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

    def _fake_build_graph(*a: Any, **kw: Any) -> _FakeCompiled:
        return _FakeCompiled()

    monkeypatch.setattr(lge_mod, "build_graph", _fake_build_graph)

    orchestrator = LangGraphExecutor(
        sessionmaker=_fake.sessionmaker,
        checkpointer=MagicMock(),
        event_bus=cast("Any", _RaisingEventStore()),
    )
    await orchestrator.resume("e1", "approved")

    # send_approval_email must still fire -- the approver gets a valid token --
    # and the persisted status must remain waiting_approval, not be flipped to
    # failed by the broken event store.
    send_mock.assert_awaited_once()
    assert len(_fake.updates) == 1, _fake.updates
    assert _fake.updates[0]["status"] == "waiting_approval"
    assert not any(c.get("status") == "failed" for c in _fake.updates)


async def test_mark_waiting_approval_stamps_pending_since() -> None:

    _fake = _install()
    orch = LangGraphExecutor(sessionmaker=_fake.sessionmaker, checkpointer=MagicMock())
    pending_since = "2026-07-11T10:00:00+00:00"
    await orch._mark_waiting_approval(  # pyright: ignore[reportPrivateUsage]
        "e1", {"node_id": "ua", "prompt": "Approve?"}, {}, pending_since
    )

    saved_vars: dict[str, Any] = _fake.updates[0]["variables"]
    since = saved_vars.get("_pending_approval_since")
    assert since == pending_since
    from datetime import datetime

    datetime.fromisoformat(since)  # raises if not a valid ISO-8601 string


@pytest.mark.parametrize("updated,expected", [(1, True), (0, False)])
@pytest.mark.parametrize("status", ["completed", "waiting_approval", "failed"])
async def test_status_transition_compares_running_and_clears_attempt(
    status: str, updated: int, expected: bool, caplog: pytest.LogCaptureFixture
) -> None:
    _fake = _install(won=bool(updated))
    executor = LangGraphExecutor(sessionmaker=_fake.sessionmaker, checkpointer=MemorySaver())

    with caplog.at_level("INFO", logger="src.engine.langgraph_executor"):
        if status == "completed":
            transitioned = await executor._mark_completed(  # pyright: ignore[reportPrivateUsage]
                "ex1",
                {
                    "final_outputs": {"end": "result"},
                    "variables": {"lastOutput": "previous"},
                    "node_results": {"end": {"output": "result"}},
                },
                attempt_id="attempt-1",
            )
        elif status == "waiting_approval":
            transitioned = await executor._mark_waiting_approval(  # pyright: ignore[reportPrivateUsage]
                "ex1",
                {"node_id": "ua", "prompt": "Approve?"},
                {"saved": "value"},
                "pause-1",
                attempt_id="attempt-1",
            )
        else:
            transitioned = await executor._mark_failed(  # pyright: ignore[reportPrivateUsage]
                "ex1",
                RuntimeError("private error"),
                failure_code="execution_timeout",
                attempt_id="attempt-1",
            )

    assert transitioned is expected
    # One guarded repo call, committed. The guard itself (status = running) and the cleared
    # lease columns are pinned by the repo tests and the behaviour oracle.
    assert len(_fake.updates) == 1
    assert _fake.commits == 1
    data = _fake.updates[0]
    assert data["execution_id"] == "ex1"
    assert data["attempt_id"] == "attempt-1"
    assert data["status"] == status
    if status == "completed":
        assert data["output"] == "result"
        assert data["variables"] == {"lastOutput": "previous"}
        assert data["node_results"] == {"end": {"output": "result"}}
        assert data["now"] is not None
    elif status == "waiting_approval":
        assert data["variables"] == {
            "saved": "value",
            "_pending_approval_node": "ua",
            "_pending_approval_prompt": "Approve?",
            "_pending_approval_since": "pause-1",
        }
    else:
        assert data["failure_code"] == "execution_timeout"
        assert data["error"] == "RuntimeError: private error"
        assert data["now"] is not None
    if not expected:
        record = next(r for r in caplog.records if getattr(r, "transition_outcome", None) == "lost")
        assert record.levelname == "INFO"
        assert record.__dict__["execution_id"] == "ex1"
        assert record.__dict__["transition_source"] == "executor"
        assert record.__dict__["previous_status"] == "running"
        assert record.__dict__["intended_status"] == status
        assert "private error" not in record.getMessage()


@pytest.mark.parametrize("method", ["run", "resume"])
@pytest.mark.parametrize("status", ["completed", "waiting_approval", "failed"])
@pytest.mark.parametrize("updated", [0, 1])
async def test_status_transition_gates_events_and_approval_email(
    monkeypatch: pytest.MonkeyPatch, method: str, status: str, updated: int
) -> None:
    _fake = _install(_row(id="ex1", status="running"), won=bool(updated))
    store = _FakeEventStore()
    executor = LangGraphExecutor(
        sessionmaker=_fake.sessionmaker,
        checkpointer=MemorySaver(),
        event_bus=cast("Any", store),
    )
    pending_info = {"node_id": "ua", "prompt": "Approve?", "approver_email": "a@example.com"}
    snapshot = SimpleNamespace(
        next=("ua",) if status == "waiting_approval" else (),
        tasks=(SimpleNamespace(interrupts=(SimpleNamespace(value=pending_info),)),),
    )
    compiled = SimpleNamespace(
        ainvoke=AsyncMock(return_value={"variables": {"saved": "value"}}),
        aget_state=AsyncMock(return_value=snapshot),
    )
    if status == "failed":
        compiled.ainvoke.side_effect = RuntimeError("graph failed")
    monkeypatch.setattr(executor, "_prepare_compiled", AsyncMock(return_value=(compiled, {}, {})))
    send_email = AsyncMock()
    monkeypatch.setattr("src.engine.langgraph_executor.send_approval_email", send_email)

    if method == "run":
        await executor.run("ex1", attempt_id="attempt-1")
    else:
        await executor.resume("ex1", "approved", attempt_id="attempt-1")

    assert len(_fake.updates) == 1
    expected_events = [("workflow_started", "running")]
    if updated:
        event_type = "approval_required" if status == "waiting_approval" else "workflow_completed"
        expected_events.append((event_type, status))
    assert [(event.type, event.payload["status"]) for event in store.events] == expected_events
    if updated and status == "waiting_approval":
        send_email.assert_awaited_once()
        assert send_email.await_args is not None
        assert send_email.await_args.kwargs["execution_id"] == "ex1"
        assert send_email.await_args.kwargs["approver_email"] == "a@example.com"
    else:
        send_email.assert_not_awaited()
    if status == "failed":
        assert _fake.updates[0]["failure_code"] == "execution_error"


@pytest.mark.parametrize("method", ["run", "resume"])
async def test_cancellation_before_workflow_started_skips_graph_and_failure(
    monkeypatch: pytest.MonkeyPatch, method: str, caplog: pytest.LogCaptureFixture
) -> None:
    _fake = _install(_row(id="ex1", status="running"), statuses=["canceled"])
    store = _FakeEventStore()
    executor = LangGraphExecutor(
        sessionmaker=_fake.sessionmaker,
        checkpointer=MemorySaver(),
        event_bus=cast("Any", store),
    )
    prepare = AsyncMock(return_value=(MagicMock(), {}, {}))
    monkeypatch.setattr(executor, "_prepare_compiled", prepare)
    send_email = AsyncMock()
    monkeypatch.setattr("src.engine.langgraph_executor.send_approval_email", send_email)

    with caplog.at_level("INFO", logger="src.engine.langgraph_executor"):
        if method == "run":
            await executor.run("ex1")
        else:
            await executor.resume("ex1", "approved")

    prepare.assert_not_awaited()
    assert _fake.updates == []
    send_email.assert_not_awaited()
    assert store.events == []
    assert any("canceled" in record.getMessage() for record in caplog.records)


@pytest.mark.parametrize("method", ["run", "resume"])
async def test_graph_stop_does_not_mark_failed_or_emit_failure(
    monkeypatch: pytest.MonkeyPatch, method: str, caplog: pytest.LogCaptureFixture
) -> None:
    _fake = _install(_row(id="ex1", status="running"))
    store = _FakeEventStore()
    executor = LangGraphExecutor(
        sessionmaker=_fake.sessionmaker,
        checkpointer=MemorySaver(),
        event_bus=cast("Any", store),
    )
    compiled = SimpleNamespace(
        ainvoke=AsyncMock(side_effect=ExecutionStopped("ex1", "canceled")),
        aget_state=AsyncMock(),
    )
    monkeypatch.setattr(executor, "_prepare_compiled", AsyncMock(return_value=(compiled, {}, {})))
    send_email = AsyncMock()
    monkeypatch.setattr("src.engine.langgraph_executor.send_approval_email", send_email)

    with caplog.at_level("INFO", logger="src.engine.langgraph_executor"):
        if method == "run":
            await executor.run("ex1")
        else:
            await executor.resume("ex1", "approved")

    compiled.ainvoke.assert_awaited_once()
    compiled.aget_state.assert_not_awaited()
    assert _fake.updates == []
    send_email.assert_not_awaited()
    assert [event.type for event in store.events] == ["workflow_started"]
    assert any("canceled" in record.getMessage() for record in caplog.records)
    assert not any(record.levelname == "ERROR" for record in caplog.records)


async def test_real_graph_cancellation_after_node_stops_downstream_progression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    running = _row(id="ex1", status="running", workflow_id="wf1", thread_id="t1", input="hello")
    _fake = _install(running, statuses=["running", "running", "canceled"])
    _set_workflow_get(AsyncMock(return_value=_workflow_row(_start_to_end_workflow_dict())))
    store = _FakeEventStore()
    monkeypatch.setattr("src.engine.events_wrapper.notify_execution_event", AsyncMock())
    executor = LangGraphExecutor(
        sessionmaker=_fake.sessionmaker,
        checkpointer=MemorySaver(),
        event_bus=cast("Any", store),
    )

    await executor.run("ex1")

    assert [(event.type, event.payload.get("nodeId")) for event in store.events] == [
        ("workflow_started", None),
        ("node_started", "s"),
    ]
    assert _fake.updates == []


@pytest.mark.parametrize("method", ["run", "resume"])
@pytest.mark.parametrize("updated", [0, 1])
async def test_graph_timeout_persists_guarded_failure(
    monkeypatch: pytest.MonkeyPatch, method: str, updated: int
) -> None:
    """Both invocation paths time out; a lost failure transition emits no failure event."""
    _fake = _install(_row(id="ex1", status="running"), won=bool(updated))
    store = _FakeEventStore()
    executor = LangGraphExecutor(
        sessionmaker=_fake.sessionmaker,
        checkpointer=MemorySaver(),
        event_bus=cast("Any", store),
    )
    invocation_cancelled = False

    async def _blocking_invoke(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal invocation_cancelled
        try:
            await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            invocation_cancelled = True
            raise
        return {}

    compiled = SimpleNamespace(
        ainvoke=AsyncMock(side_effect=_blocking_invoke),
        aget_state=AsyncMock(return_value=SimpleNamespace(next=())),
    )
    monkeypatch.setattr(executor, "_prepare_compiled", AsyncMock(return_value=(compiled, {}, {})))
    monkeypatch.setattr(
        "src.engine.langgraph_executor.get_settings",
        lambda: SimpleNamespace(execution_max_runtime_seconds=0.001),
    )
    mark_failed = AsyncMock(wraps=executor._mark_failed)  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(executor, "_mark_failed", mark_failed)

    if method == "run":
        await executor.run("ex1", attempt_id="attempt-1")
    else:
        await executor.resume("ex1", "approved", attempt_id="attempt-1")

    mark_failed.assert_awaited_once()
    assert mark_failed.await_args is not None
    assert mark_failed.await_args.args[0] == "ex1"
    assert isinstance(mark_failed.await_args.args[1], TimeoutError)
    assert mark_failed.await_args.kwargs == {
        "failure_code": "execution_timeout",
        "attempt_id": "attempt-1",
    }
    assert invocation_cancelled
    compiled.aget_state.assert_not_awaited()
    assert len(_fake.updates) == 1
    assert _fake.updates[0]["execution_id"] == "ex1"
    assert _fake.updates[0]["status"] == "failed"
    assert _fake.updates[0]["failure_code"] == "execution_timeout"
    expected_events = [("workflow_started", "running")]
    if updated:
        expected_events.append(("workflow_completed", "failed"))
    assert [(event.type, event.payload["status"]) for event in store.events] == expected_events


@pytest.mark.parametrize("method", ["run", "resume"])
@pytest.mark.parametrize("stage", ["prepare", "provider", "checkpoint"])
async def test_dependency_timeout_is_execution_error(
    monkeypatch: pytest.MonkeyPatch, method: str, stage: str
) -> None:
    """Only Composer's elapsed runtime budget may produce execution_timeout."""
    _fake = _install(_row(id="ex1", status="running"))
    store = _FakeEventStore()
    executor = LangGraphExecutor(
        sessionmaker=_fake.sessionmaker,
        checkpointer=MemorySaver(),
        event_bus=cast("Any", store),
    )
    error = TimeoutError("dependency timed out")
    compiled = SimpleNamespace(
        ainvoke=AsyncMock(side_effect=error) if stage == "provider" else AsyncMock(return_value={}),
        aget_state=AsyncMock(side_effect=error) if stage == "checkpoint" else AsyncMock(),
    )
    prepare = (
        AsyncMock(side_effect=error)
        if stage == "prepare"
        else AsyncMock(return_value=(compiled, {}, {}))
    )
    monkeypatch.setattr(executor, "_prepare_compiled", prepare)
    if method == "run":
        await executor.run("ex1")
    else:
        await executor.resume("ex1", "approved")

    data = _fake.updates[-1]
    assert data["failure_code"] == "execution_error"
    assert data["error"] == "TimeoutError: dependency timed out"
    assert [(event.type, event.payload["status"]) for event in store.events] == [
        ("workflow_started", "running"),
        ("workflow_completed", "failed"),
    ]


def test_worker_entrypoints_accept_keyword_only_attempt_identity() -> None:
    for method in (LangGraphExecutor.run, LangGraphExecutor.resume):
        parameter = inspect.signature(method).parameters["attempt_id"]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.annotation == str | None
