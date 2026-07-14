"""Tests for the event-emitting executor wrapper."""

from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.engine.events import ExecutionEvent
from src.engine.events_wrapper import wrap_executor_with_events
from src.engine.state import initial_state
from src.engine.workflow import Workflow


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
        await compiled.ainvoke(initial_state(), config={"configurable": {"thread_id": "t1"}})
    finally:
        set_current_execution_id(None)
        set_current_event_bus(None)

    types_by_node: dict[str, list[str]] = {}
    for ev in store.events:
        types_by_node.setdefault(ev.payload["nodeId"], []).append(ev.type)

    assert types_by_node["s"] == ["node_started", "node_completed"]
    assert types_by_node["e"] == ["node_started", "node_completed"]
