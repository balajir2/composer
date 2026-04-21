"""Tests for the event-emitting executor wrapper."""

import asyncio
from typing import Any

import pytest

from src.engine.events import ExecutionEventBus
from src.engine.events_wrapper import wrap_executor_with_events
from src.engine.state import initial_state
from src.engine.workflow import Workflow


class _FakeExecutor:
    async def arun(self, state: Any) -> dict[str, Any]:
        return {"variables": {"k": "v"}, "node_results": {}}


class _RaisingExecutor:
    async def arun(self, state: Any) -> dict[str, Any]:
        raise RuntimeError("boom")


class _FakeNode:
    def __init__(self, node_id: str = "n", node_type: str = "http") -> None:
        self.id = node_id
        self.type = node_type


async def test_wrapper_emits_start_and_complete_on_success() -> None:
    from src.engine.context import set_current_event_bus, set_current_execution_id

    bus = ExecutionEventBus()
    q = await bus.subscribe("e1")
    set_current_execution_id("e1")
    set_current_event_bus(bus)

    try:
        arun = wrap_executor_with_events(_FakeExecutor(), _FakeNode("n1", "http"))  # pyright: ignore[reportArgumentType]
        result = await arun(initial_state())
        assert result["variables"]["k"] == "v"
    finally:
        set_current_execution_id(None)
        set_current_event_bus(None)

    events: list[Any] = []
    while True:
        try:
            ev = await asyncio.wait_for(q.get(), timeout=0.05)
        except TimeoutError:
            break
        if ev is None:
            break
        events.append(ev)

    types = [e.type for e in events]
    assert types == ["node-start", "node-complete"]
    for e in events:
        assert e.payload == {"node_id": "n1", "node_type": "http"}


async def test_wrapper_emits_start_but_not_complete_on_exception() -> None:
    from src.engine.context import set_current_event_bus, set_current_execution_id

    bus = ExecutionEventBus()
    q = await bus.subscribe("e1")
    set_current_execution_id("e1")
    set_current_event_bus(bus)

    try:
        arun = wrap_executor_with_events(_RaisingExecutor(), _FakeNode("n1", "http"))  # pyright: ignore[reportArgumentType]
        with pytest.raises(RuntimeError, match="boom"):
            await arun(initial_state())
    finally:
        set_current_execution_id(None)
        set_current_event_bus(None)

    events: list[Any] = []
    while True:
        try:
            ev = await asyncio.wait_for(q.get(), timeout=0.05)
        except TimeoutError:
            break
        if ev is None:
            break
        events.append(ev)

    types = [e.type for e in events]
    assert types == ["node-start"]


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


async def test_build_graph_wraps_executors() -> None:
    """Integration: build_graph applies the wrapper and emits events through
    a real compiled graph."""
    from langgraph.checkpoint.memory import MemorySaver

    from src.engine.context import set_current_event_bus, set_current_execution_id
    from src.engine.graph_builder import build_graph

    bus = ExecutionEventBus()
    q = await bus.subscribe("e1")
    set_current_execution_id("e1")
    set_current_event_bus(bus)

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

    events: list[Any] = []
    while True:
        try:
            ev = await asyncio.wait_for(q.get(), timeout=0.05)
        except TimeoutError:
            break
        if ev is None:
            break
        events.append(ev)

    types_by_node: dict[str, list[str]] = {}
    for ev in events:
        types_by_node.setdefault(ev.payload["node_id"], []).append(ev.type)

    assert types_by_node["s"] == ["node-start", "node-complete"]
    assert types_by_node["e"] == ["node-start", "node-complete"]
