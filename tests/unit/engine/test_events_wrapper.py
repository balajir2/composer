"""Tests for the event-emitting executor wrapper."""

from typing import Any
from unittest.mock import AsyncMock

import pytest
from langgraph.errors import GraphBubbleUp

from src.engine.events import ExecutionEvent
from src.engine.events_wrapper import wrap_executor_with_events
from src.engine.execution_control import ExecutionStopped
from src.engine.state import initial_state
from src.engine.workflow import Workflow
from tests.unit.db_fakes import (
    FakeSessionmaker,
    RecordingSessionmaker,
    assert_no_network_inside_session,
)


class _FakeExecutor:
    async def arun(self, state: Any) -> dict[str, Any]:
        return {"variables": {"k": "v"}, "node_results": {}}


class _RaisingExecutor:
    async def arun(self, state: Any) -> dict[str, Any]:
        raise RuntimeError("boom")


class _InterruptingExecutor:
    """Simulates a user-approval node calling langgraph.types.interrupt()."""

    async def arun(self, state: Any) -> dict[str, Any]:
        from langgraph.errors import GraphInterrupt
        from langgraph.types import Interrupt

        raise GraphInterrupt((Interrupt(value={"node_id": "approval-1", "prompt": "Approve?"}),))


class _FakeNodeData:
    label = "label"
    node_name = None


class _FakeNode:
    def __init__(self, node_id: str = "n", node_type: str = "http") -> None:
        self.id = node_id
        self.type = node_type
        self.data = _FakeNodeData()


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
    """The wrapper calls notify_execution_event(execution_id, seq=seq) after
    every append — patch it to a no-op so unit tests never attempt a real
    asyncpg connection."""
    mock = AsyncMock()
    monkeypatch.setattr("src.engine.events_wrapper.notify_execution_event", mock)
    return mock


async def test_wrapper_emits_start_and_complete_on_success() -> None:
    from src.engine.context import set_current_event_bus, set_current_execution_id

    store = _FakeEventStore()
    set_current_execution_id("e1")
    set_current_event_bus(store)  # pyright: ignore[reportArgumentType]

    try:
        arun = wrap_executor_with_events(_FakeExecutor(), _FakeNode("n1", "http"))  # pyright: ignore[reportArgumentType]
        result = await arun(initial_state())
        assert result["variables"]["k"] == "v"
    finally:
        set_current_execution_id(None)
        set_current_event_bus(None)

    events = store.events
    types = [e.type for e in events]
    assert types == ["node_started", "node_completed"]
    for e in events:
        assert e.payload["nodeId"] == "n1"
        assert e.payload["nodeType"] == "http"
    # node_completed carries the executor's output so the UI can display it.
    completed = next(e for e in events if e.type == "node_completed")
    # _FakeExecutor doesn't write node_results, so _extract_output falls back
    # to variables.lastOutput — unset here → None.
    assert "output" in completed.payload


async def test_wrapper_adds_timing_to_node_results_and_completed_event() -> None:
    """P1-5: every node's persisted node_results entry (and the
    node_completed event payload) carries startedAt/completedAt/durationMs
    so the execution trace shows how long each node took — added once,
    universally, in this wrapper rather than per-executor, since every
    node type already flows through it."""
    from src.engine.context import set_current_event_bus, set_current_execution_id

    store = _FakeEventStore()
    set_current_execution_id("e1")
    set_current_event_bus(store)  # pyright: ignore[reportArgumentType]

    try:
        arun = wrap_executor_with_events(_FakeExecutor(), _FakeNode("n1", "http"))  # pyright: ignore[reportArgumentType]
        result = await arun(initial_state())
    finally:
        set_current_execution_id(None)
        set_current_event_bus(None)

    node_rec = result["node_results"]["n1"]
    assert "startedAt" in node_rec
    assert "completedAt" in node_rec
    assert isinstance(node_rec["durationMs"], int)
    assert node_rec["durationMs"] >= 0

    completed = next(e for e in store.events if e.type == "node_completed")
    assert "durationMs" in completed.payload
    assert completed.payload["durationMs"] >= 0


async def test_wrapper_adds_duration_to_node_failed_event() -> None:
    """A failed node's duration matters too — helps distinguish an
    instant validation error from a call that hung for 30s before
    erroring (P1-5)."""
    from src.engine.context import set_current_event_bus, set_current_execution_id

    store = _FakeEventStore()
    set_current_execution_id("e1")
    set_current_event_bus(store)  # pyright: ignore[reportArgumentType]

    try:
        arun = wrap_executor_with_events(_RaisingExecutor(), _FakeNode("n1", "http"))  # pyright: ignore[reportArgumentType]
        with pytest.raises(RuntimeError, match="boom"):
            await arun(initial_state())
    finally:
        set_current_execution_id(None)
        set_current_event_bus(None)

    failed = next(e for e in store.events if e.type == "node_failed")
    assert "durationMs" in failed.payload
    assert failed.payload["durationMs"] >= 0


async def test_wrapper_emits_start_but_not_complete_on_exception() -> None:
    from src.engine.context import set_current_event_bus, set_current_execution_id

    store = _FakeEventStore()
    set_current_execution_id("e1")
    set_current_event_bus(store)  # pyright: ignore[reportArgumentType]

    try:
        arun = wrap_executor_with_events(_RaisingExecutor(), _FakeNode("n1", "http"))  # pyright: ignore[reportArgumentType]
        with pytest.raises(RuntimeError, match="boom"):
            await arun(initial_state())
    finally:
        set_current_execution_id(None)
        set_current_event_bus(None)

    # The wrapper now emits node_failed on exception so the UI can surface
    # the actual error without the user having to dig through backend logs.
    types = [e.type for e in store.events]
    assert types == ["node_started", "node_failed"]
    failed = store.events[1]
    assert failed.payload["nodeId"] == "n1"
    assert "boom" in failed.payload["error"]


async def test_wrapper_does_not_emit_node_failed_on_graph_interrupt() -> None:
    """Regression: a user-approval node's interrupt() is LangGraph's own
    pause mechanism, not a failure. The wrapper must let GraphInterrupt
    (and any other GraphBubbleUp subclass) propagate unmolested — without
    emitting node_failed — so LangGraphExecutor.run's post-ainvoke
    snapshot.next check is what surfaces the pause, not a misreported
    node crash."""
    from langgraph.errors import GraphInterrupt

    from src.engine.context import set_current_event_bus, set_current_execution_id

    store = _FakeEventStore()
    set_current_execution_id("e1")
    set_current_event_bus(store)  # pyright: ignore[reportArgumentType]

    try:
        node = _FakeNode("approval-1", "user-approval")
        arun = wrap_executor_with_events(_InterruptingExecutor(), node)  # pyright: ignore[reportArgumentType]
        with pytest.raises(GraphInterrupt):
            await arun(initial_state())
    finally:
        set_current_execution_id(None)
        set_current_event_bus(None)

    types = [e.type for e in store.events]
    assert types == ["node_started"]
    assert not any(e.type == "node_failed" for e in store.events)


async def test_wrapper_noop_when_context_unset() -> None:
    """If execution_id/event_bus ContextVars are unset (e.g., in a unit test
    that isn't driving through LangGraphExecutor), the wrapper must still
    call through to the underlying executor without errors."""
    from src.engine.context import set_current_event_bus, set_current_execution_id

    set_current_execution_id(None)
    set_current_event_bus(None)

    arun = wrap_executor_with_events(_FakeExecutor(), _FakeNode("n1", "http"))  # pyright: ignore[reportArgumentType]
    result = await arun(initial_state())
    assert result["variables"]["k"] == "v"


def _patch_statuses(monkeypatch: pytest.MonkeyPatch, statuses: list[str | None]) -> list[str]:
    """Make ``executions_repo.get_status`` answer from ``statuses``; returns the asked ids."""
    asked: list[str] = []
    remaining = list(statuses)

    async def _get_status(_session: Any, execution_id: str) -> str | None:
        asked.append(execution_id)
        return remaining.pop(0)

    monkeypatch.setattr("src.db.repos.executions.get_status", _get_status)
    return asked


@pytest.mark.parametrize(
    "status", ["canceled", "completed", "failed", "waiting_approval", "queued", None]
)
async def test_wrapper_stops_before_start_when_execution_is_not_running(
    status: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.engine.context import (
        set_current_event_bus,
        set_current_execution_id,
        set_current_sessionmaker,
    )

    asked = _patch_statuses(monkeypatch, [status])
    sm = FakeSessionmaker()
    executor = _FakeExecutor()
    executor.arun = AsyncMock(wraps=executor.arun)
    store = _FakeEventStore()
    set_current_sessionmaker(sm)  # pyright: ignore[reportArgumentType]
    set_current_execution_id("e1")
    set_current_event_bus(store)  # pyright: ignore[reportArgumentType]

    try:
        arun = wrap_executor_with_events(executor, _FakeNode())  # pyright: ignore[reportArgumentType]
        with pytest.raises(ExecutionStopped) as stopped:
            await arun(initial_state())
    finally:
        set_current_sessionmaker(None)
        set_current_execution_id(None)
        set_current_event_bus(None)

    assert isinstance(stopped.value, GraphBubbleUp)
    assert stopped.value.execution_id == "e1"
    assert stopped.value.status == (status if status is not None else "missing")
    executor.arun.assert_not_awaited()
    assert store.events == []
    assert asked == ["e1"]


async def test_wrapper_stops_after_executor_returns_when_execution_was_canceled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.engine.context import (
        set_current_event_bus,
        set_current_execution_id,
        set_current_sessionmaker,
    )

    asked = _patch_statuses(monkeypatch, ["running", "canceled"])
    sm = FakeSessionmaker()
    executor = _FakeExecutor()
    executor.arun = AsyncMock(wraps=executor.arun)
    store = _FakeEventStore()
    set_current_sessionmaker(sm)  # pyright: ignore[reportArgumentType]
    set_current_execution_id("e1")
    set_current_event_bus(store)  # pyright: ignore[reportArgumentType]

    try:
        arun = wrap_executor_with_events(executor, _FakeNode())  # pyright: ignore[reportArgumentType]
        with pytest.raises(ExecutionStopped) as stopped:
            await arun(initial_state())
    finally:
        set_current_sessionmaker(None)
        set_current_execution_id(None)
        set_current_event_bus(None)

    assert isinstance(stopped.value, GraphBubbleUp)
    assert stopped.value.execution_id == "e1"
    assert stopped.value.status == "canceled"
    executor.arun.assert_awaited_once()
    assert [event.type for event in store.events] == ["node_started"]
    assert asked == ["e1", "e1"]


async def test_stop_check_is_a_short_read_that_closes_before_the_node_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ordering: session open -> read -> close, then the node executes, then a second read."""
    from src.engine.context import (
        set_current_event_bus,
        set_current_execution_id,
        set_current_sessionmaker,
    )

    events: list[str] = []
    _patch_statuses(monkeypatch, ["running", "running"])
    sm = RecordingSessionmaker(events)

    class _Node:
        async def arun(self, state: Any) -> dict[str, Any]:
            events.append("net:node")
            return {"variables": {}, "node_results": {}}

    set_current_sessionmaker(sm)  # pyright: ignore[reportArgumentType]
    set_current_execution_id("e1")
    set_current_event_bus(None)
    try:
        arun = wrap_executor_with_events(_Node(), _FakeNode())  # pyright: ignore[reportArgumentType]
        await arun(initial_state())
    finally:
        set_current_sessionmaker(None)
        set_current_execution_id(None)

    assert events == [
        "session:enter",
        "session:exit",
        "net:node",
        "session:enter",
        "session:exit",
    ]
    assert_no_network_inside_session(events)


async def test_wrapper_persists_events_and_notifies() -> None:
    from unittest.mock import AsyncMock, patch

    from src.engine.context import set_current_event_bus, set_current_execution_id

    store = AsyncMock()
    store.append = AsyncMock(return_value=1)
    set_current_execution_id("e1")
    set_current_event_bus(store)

    try:
        with patch("src.engine.events_wrapper.notify_execution_event", new=AsyncMock()) as notify:
            arun = wrap_executor_with_events(_FakeExecutor(), _FakeNode("n1", "http"))  # pyright: ignore[reportArgumentType]
            await arun(initial_state())
            assert store.append.await_count == 2  # node_started + node_completed
            notify.assert_awaited()
    finally:
        set_current_execution_id(None)
        set_current_event_bus(None)


async def test_wrapper_node_completed_survives_event_store_failure() -> None:
    """P1-4 regression: a telemetry-path failure (event append or NOTIFY)
    must not abort the node. By the time node_completed would be emitted,
    the executor has already produced its real, possibly expensive output
    -- a broken event store must not discard that and raise past the
    caller. _persist_and_notify's internal try/except is what protects
    this."""
    from src.engine.context import set_current_event_bus, set_current_execution_id

    class _RaisingEventStore:
        async def append(self, event: ExecutionEvent) -> int:
            raise RuntimeError("simulated Postgres event-log outage")

    set_current_execution_id("e1")
    set_current_event_bus(_RaisingEventStore())  # pyright: ignore[reportArgumentType]

    try:
        arun = wrap_executor_with_events(_FakeExecutor(), _FakeNode("n1", "http"))  # pyright: ignore[reportArgumentType]
        # Must not raise despite every append() call failing (node_started
        # and node_completed both hit the broken store).
        result = await arun(initial_state())
    finally:
        set_current_execution_id(None)
        set_current_event_bus(None)

    # The executor's actual output is still returned successfully.
    assert result["variables"]["k"] == "v"


async def test_build_graph_wraps_executors() -> None:
    """Integration: build_graph applies the wrapper and emits events through
    a real compiled graph."""
    from langgraph.checkpoint.memory import MemorySaver

    from src.engine.context import set_current_event_bus, set_current_execution_id
    from src.engine.graph_builder import build_graph

    store = _FakeEventStore()
    set_current_execution_id("e1")
    set_current_event_bus(store)  # pyright: ignore[reportArgumentType]

    wf = Workflow.model_validate(
        {
            "id": "w",
            "name": "t",
            "nodes": [
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                {"id": "e", "type": "end", "position": {"x": 1, "y": 0}, "data": {"label": "E"}},
            ],
            "edges": [{"id": "e1", "source": "s", "target": "e"}],
        }
    )
    compiled = build_graph(wf, MemorySaver())
    try:
        await compiled.ainvoke(  # pyright: ignore[reportUnknownMemberType]
            initial_state(), config={"configurable": {"thread_id": "t1"}}
        )
    finally:
        set_current_execution_id(None)
        set_current_event_bus(None)

    types_by_node: dict[str, list[str]] = {}
    for ev in store.events:
        types_by_node.setdefault(ev.payload["nodeId"], []).append(ev.type)

    assert types_by_node["s"] == ["node_started", "node_completed"]
    assert types_by_node["e"] == ["node_started", "node_completed"]
