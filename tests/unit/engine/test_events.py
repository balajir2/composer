"""Tests for ExecutionEventBus + ExecutionEvent."""

import asyncio

import pytest

from src.engine.events import ExecutionEvent, ExecutionEventBus


async def test_subscribe_emit_receive() -> None:
    bus = ExecutionEventBus()
    q = await bus.subscribe("e1")
    await bus.emit(
        ExecutionEvent(type="status-change", execution_id="e1", payload={"status": "running"})
    )
    got = await asyncio.wait_for(q.get(), timeout=1.0)
    assert got is not None
    assert got.type == "status-change"
    assert got.execution_id == "e1"
    assert got.payload == {"status": "running"}


async def test_multi_subscriber_fanout() -> None:
    bus = ExecutionEventBus()
    q1 = await bus.subscribe("e1")
    q2 = await bus.subscribe("e1")
    await bus.emit(ExecutionEvent(type="node-start", execution_id="e1", payload={"node_id": "n"}))
    g1 = await asyncio.wait_for(q1.get(), timeout=1.0)
    g2 = await asyncio.wait_for(q2.get(), timeout=1.0)
    assert g1 is not None and g2 is not None
    assert g1.type == g2.type == "node-start"


async def test_emit_ignores_unrelated_execution() -> None:
    bus = ExecutionEventBus()
    q = await bus.subscribe("e1")
    await bus.emit(ExecutionEvent(type="status-change", execution_id="e2", payload={}))
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(q.get(), timeout=0.05)


async def test_unsubscribe_removes_queue() -> None:
    bus = ExecutionEventBus()
    q = await bus.subscribe("e1")
    await bus.unsubscribe("e1", q)
    await bus.emit(ExecutionEvent(type="status-change", execution_id="e1", payload={}))
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(q.get(), timeout=0.05)


async def test_close_sends_none_sentinel() -> None:
    bus = ExecutionEventBus()
    q = await bus.subscribe("e1")
    await bus.close("e1")
    got = await asyncio.wait_for(q.get(), timeout=1.0)
    assert got is None


async def test_queue_overflow_drops_oldest() -> None:
    bus = ExecutionEventBus()
    q = await bus.subscribe("e1")
    for i in range(200):
        await bus.emit(ExecutionEvent(type="node-start", execution_id="e1", payload={"i": i}))
    drained: list[int] = []
    while True:
        try:
            ev = await asyncio.wait_for(q.get(), timeout=0.01)
            if ev is None:
                break
            drained.append(ev.payload["i"])
        except TimeoutError:
            break
    assert len(drained) == 128
    assert drained[-1] == 199


async def test_event_as_json_roundtrippable() -> None:
    import json

    e = ExecutionEvent(type="status-change", execution_id="e1", payload={"status": "running"})
    body = e.as_json()
    assert body["type"] == "status-change"
    assert body["execution_id"] == "e1"
    assert body["payload"] == {"status": "running"}
    assert "timestamp" in body
    json.dumps(body)


async def test_context_vars_set_and_get() -> None:
    from src.engine.context import (
        get_current_event_bus,
        get_current_execution_id,
        set_current_event_bus,
        set_current_execution_id,
    )

    assert get_current_execution_id() is None
    assert get_current_event_bus() is None

    set_current_execution_id("e1")
    assert get_current_execution_id() == "e1"

    bus = ExecutionEventBus()
    set_current_event_bus(bus)
    assert get_current_event_bus() is bus

    set_current_execution_id(None)
    set_current_event_bus(None)
    assert get_current_execution_id() is None
    assert get_current_event_bus() is None
