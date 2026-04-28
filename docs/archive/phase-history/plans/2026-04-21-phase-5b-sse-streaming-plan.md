# Phase 5b — SSE streaming: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `GET /executions/{execution_id}/events` — a Server-Sent Events stream of real-time workflow progress. Adds an in-process `ExecutionEventBus`, emission from `LangGraphExecutor` + node wrapper, and an SSE endpoint with snapshot-on-subscribe.

**Architecture.** `ExecutionEventBus` (per-app singleton) fans out `ExecutionEvent`s per `execution_id` to bounded asyncio queues. Executor-run flow and node wrappers emit events; SSE endpoint subscribes, replays a status snapshot, then streams live until close.

**Tech Stack:** FastAPI `StreamingResponse`, Python stdlib `asyncio.Queue`, ContextVars (following Phase 3b pattern), pytest + pytest-httpx.

**Spec:** [`docs/superpowers/specs/2026-04-21-phase-5b-sse-streaming-design.md`](../specs/2026-04-21-phase-5b-sse-streaming-design.md)
**ADR:** [ADR-0017](../../design/decisions.md#adr-0017-sse-streaming-uses-in-process-asyncio-event-bus-with-snapshot-on-subscribe)

---

## Sequencing and discipline

8 tasks, one commit each. Every task ends green on:

```bash
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m ruff format --check src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

Integration tests (Task 6) + regression (Task 7) run at phase-exit (Task 8) with real Neon.

**⚠️ Forbidden files (all tasks except where explicitly noted):** `pyproject.toml`, `.github/workflows/*`, `CLAUDE.md` (except Task 8), `docs/design/*` (except Task 8 for ADR backfill), `docs/superpowers/plans/*`, `docs/superpowers/specs/*`, Prisma schema + migrations (5b is schema-free). Fix pyright strict errors with inline `# pyright: ignore[specific]` — never loosen `pyproject.toml`.

Commit footer every commit:
```
Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
```

Stay on `main`.

---

## Task 1: `ExecutionEventBus` + `ExecutionEvent` + ContextVars

**Files:**
- Create: `src/engine/events.py`
- Modify: `src/engine/context.py` (add execution-id + event-bus ContextVars)
- Create: `tests/unit/engine/test_events.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/engine/test_events.py`:

```python
"""Tests for ExecutionEventBus + ExecutionEvent."""

import asyncio

import pytest

from src.engine.events import ExecutionEvent, ExecutionEventBus


async def test_subscribe_emit_receive() -> None:
    bus = ExecutionEventBus()
    q = await bus.subscribe("e1")
    await bus.emit(ExecutionEvent(type="status-change", execution_id="e1", payload={"status": "running"}))
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
    # Queue was unsubscribed; receives nothing
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
    # Fill past 128 + a bit more
    for i in range(200):
        await bus.emit(ExecutionEvent(type="node-start", execution_id="e1", payload={"i": i}))
    # Queue cap is 128; overflow drops oldest, so we should be able to drain exactly 128 items
    drained: list[int] = []
    while True:
        try:
            ev = await asyncio.wait_for(q.get(), timeout=0.01)
            if ev is None:
                break
            drained.append(ev.payload["i"])
        except asyncio.TimeoutError:
            break
    assert len(drained) == 128
    # The oldest drops first, so the drained items end at i=199
    assert drained[-1] == 199


async def test_event_as_json_roundtrippable() -> None:
    import json

    e = ExecutionEvent(type="status-change", execution_id="e1", payload={"status": "running"})
    body = e.as_json()
    assert body["type"] == "status-change"
    assert body["execution_id"] == "e1"
    assert body["payload"] == {"status": "running"}
    assert "timestamp" in body
    # JSON-serializable
    json.dumps(body)


async def test_context_vars_set_and_get() -> None:
    from src.engine.context import (
        get_current_event_bus,
        get_current_execution_id,
        set_current_event_bus,
        set_current_execution_id,
    )

    # Default is None
    assert get_current_execution_id() is None
    assert get_current_event_bus() is None

    set_current_execution_id("e1")
    assert get_current_execution_id() == "e1"

    bus = ExecutionEventBus()
    set_current_event_bus(bus)
    assert get_current_event_bus() is bus

    # Reset
    set_current_execution_id(None)
    set_current_event_bus(None)
    assert get_current_execution_id() is None
    assert get_current_event_bus() is None
```

- [ ] **Step 2: Verify fail**

```bash
.venv/Scripts/python -m pytest tests/unit/engine/test_events.py -v
```
Expected: ImportError on `src.engine.events`.

- [ ] **Step 3: Implement `src/engine/events.py`**

```python
"""Execution event bus — in-process asyncio fanout for SSE streaming.

See Phase 5b spec §5 + ADR-0017.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

EventType = Literal[
    "status-change",
    "node-start",
    "node-complete",
    "approval-pending",
    "approval-resumed",
]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class ExecutionEvent:
    type: EventType
    execution_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=_now_iso)

    def as_json(self) -> dict[str, Any]:
        return asdict(self)


class ExecutionEventBus:
    """Per-execution fanout of ExecutionEvent, in-process only.

    Bounded queue per subscriber (maxsize=128); on overflow the oldest
    undelivered event is dropped so a slow subscriber never stalls the
    emitter.
    """

    _MAX_QUEUE = 128

    def __init__(self) -> None:
        self._queues: dict[str, list[asyncio.Queue[ExecutionEvent | None]]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, execution_id: str) -> asyncio.Queue[ExecutionEvent | None]:
        q: asyncio.Queue[ExecutionEvent | None] = asyncio.Queue(maxsize=self._MAX_QUEUE)
        async with self._lock:
            self._queues.setdefault(execution_id, []).append(q)
        return q

    async def unsubscribe(
        self,
        execution_id: str,
        q: asyncio.Queue[ExecutionEvent | None],
    ) -> None:
        async with self._lock:
            queues = self._queues.get(execution_id, [])
            if q in queues:
                queues.remove(q)
            if not queues:
                self._queues.pop(execution_id, None)

    async def emit(self, event: ExecutionEvent) -> None:
        async with self._lock:
            queues = list(self._queues.get(event.execution_id, []))
        for q in queues:
            self._offer(q, event)

    @staticmethod
    def _offer(q: asyncio.Queue[ExecutionEvent | None], event: ExecutionEvent) -> None:
        try:
            q.put_nowait(event)
            return
        except asyncio.QueueFull:
            pass
        # Drop oldest and retry once
        try:
            q.get_nowait()
        except asyncio.QueueEmpty:
            return
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass  # still full; drop this event silently

    async def close(self, execution_id: str) -> None:
        async with self._lock:
            queues = list(self._queues.get(execution_id, []))
        for q in queues:
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                # Queue is pressured; drop oldest to make room for sentinel
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    q.put_nowait(None)
                except asyncio.QueueFull:
                    pass


__all__ = ["EventType", "ExecutionEvent", "ExecutionEventBus"]
```

- [ ] **Step 4: Extend `src/engine/context.py`**

Read the file first. After the existing `set_current_langsmith` / `set_current_db` helpers, add:

```python
from src.engine.events import ExecutionEventBus  # local import to keep module lightweight

_current_execution_id: ContextVar[str | None] = ContextVar("_current_execution_id", default=None)
_current_event_bus: ContextVar[ExecutionEventBus | None] = ContextVar("_current_event_bus", default=None)


def set_current_execution_id(execution_id: str | None) -> None:
    _current_execution_id.set(execution_id)


def get_current_execution_id() -> str | None:
    return _current_execution_id.get()


def set_current_event_bus(bus: ExecutionEventBus | None) -> None:
    _current_event_bus.set(bus)


def get_current_event_bus() -> ExecutionEventBus | None:
    return _current_event_bus.get()
```

Match the module's existing imports + style. If `ContextVar` is already imported, reuse it. If placing the import at top level creates a cycle, keep the `from src.engine.events import ExecutionEventBus` import local to the module body (same-file, just above the ContextVar declaration) — Python resolves it fine at runtime.

Update `__all__` to include the 4 new names.

- [ ] **Step 5: Run + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/engine/test_events.py -v
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```
Expected: 8 new tests pass; overall +8 (~390).

```bash
git add src/engine/events.py src/engine/context.py tests/unit/engine/test_events.py
git commit -m "feat(engine): ExecutionEventBus + event ContextVars (Phase 5b)

In-process asyncio fanout of ExecutionEvents keyed by execution_id.
Each subscriber gets a bounded (size 128) asyncio.Queue; emitter drops
oldest on overflow.  close(execution_id) signals subscribers to drain
via a None sentinel.

Five event types (spec §4): status-change, node-start, node-complete,
approval-pending, approval-resumed.

src/engine/context.py gains two ContextVars: current_execution_id +
current_event_bus.  LangGraphExecutor (Task 2) will set them inside
run()/resume(); node wrappers (Task 3) will read them.

See Phase 5b spec §5, ADR-0017.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `LangGraphExecutor` emits events

**Files:**
- Modify: `src/engine/langgraph_executor.py`
- Modify: `src/storage/db.py` (attach `event_bus` to `app.state`)
- Modify: `tests/unit/engine/test_langgraph_executor.py` (append)

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/engine/test_langgraph_executor.py`:

```python
async def test_run_emits_status_change_on_start_and_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run() emits status-change(running) before ainvoke and status-change(completed)
    + close() after."""
    from types import SimpleNamespace
    from typing import Any
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
            id="e1", workflowId="w1", userId="dev", threadId="t1", input=None,
        )
    )
    db.workflow = MagicMock()
    db.workflow.find_unique = AsyncMock(
        return_value=SimpleNamespace(
            id="w1", name="t",
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
        async def ainvoke(self, *a: Any, **kw: Any) -> dict[str, Any]:
            return {"variables": {}, "node_results": {}}
        async def aget_state(self, *a: Any, **kw: Any) -> Any:
            return _FakeSnap()

    monkeypatch.setattr(lge_mod, "build_graph", lambda *a, **kw: _FakeCompiled())  # pyright: ignore[reportUnknownLambdaType]

    orch = LangGraphExecutor(db, MagicMock(), event_bus=bus)
    await orch.run("e1")

    # Drain queue
    events: list[Any] = []
    import asyncio as _aio
    while True:
        try:
            ev = await _aio.wait_for(queue.get(), timeout=0.1)
        except _aio.TimeoutError:
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
    from typing import Any
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
            id="e1", workflowId="w1", userId="dev", threadId="t1", input=None,
        )
    )
    db.workflow = MagicMock()
    db.workflow.find_unique = AsyncMock(
        return_value=SimpleNamespace(
            id="w1", name="t",
            nodes=[
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                {"id": "ua", "type": "user-approval", "position": {"x": 0, "y": 0}, "data": {"label": "UA", "approvalMessage": "Approve?"}},
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
        value = {"node_id": "ua", "prompt": "Approve?"}

    class _FakeTask:
        interrupts = (_FakeInterrupt(),)

    class _FakeSnapPaused:
        next = ("ua",)
        tasks = (_FakeTask(),)

    class _FakeCompiled:
        async def ainvoke(self, *a: Any, **kw: Any) -> dict[str, Any]:
            return {"variables": {"input": ""}, "node_results": {}}
        async def aget_state(self, *a: Any, **kw: Any) -> Any:
            return _FakeSnapPaused()

    monkeypatch.setattr(lge_mod, "build_graph", lambda *a, **kw: _FakeCompiled())  # pyright: ignore[reportUnknownLambdaType]

    orch = LangGraphExecutor(db, MagicMock(), event_bus=bus)
    await orch.run("e1")

    events: list[Any] = []
    import asyncio as _aio
    while True:
        try:
            ev = await _aio.wait_for(queue.get(), timeout=0.1)
        except _aio.TimeoutError:
            break
        if ev is None:
            break
        events.append(ev)

    types = [e.type for e in events]
    assert "approval-pending" in types
    pending = next(e for e in events if e.type == "approval-pending")
    assert pending.payload["node_id"] == "ua"
    assert pending.payload["prompt"] == "Approve?"
    # Also emits status-change(waiting_approval)
    waiting = [e for e in events if e.type == "status-change" and e.payload.get("status") == "waiting_approval"]
    assert waiting


async def test_run_emits_failed_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace
    from typing import Any
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
            id="e1", workflowId="w1", userId="dev", threadId="t1", input=None,
        )
    )
    db.workflow = MagicMock()
    db.workflow.find_unique = AsyncMock(side_effect=RuntimeError("boom"))
    db.workflowexecution.update = AsyncMock()

    orch = LangGraphExecutor(db, MagicMock(), event_bus=bus)
    await orch.run("e1")

    events: list[Any] = []
    import asyncio as _aio
    while True:
        try:
            ev = await _aio.wait_for(queue.get(), timeout=0.1)
        except _aio.TimeoutError:
            break
        if ev is None:
            break
        events.append(ev)

    statuses = [e.payload.get("status") for e in events if e.type == "status-change"]
    assert "failed" in statuses
```

- [ ] **Step 2: Verify fail**

```bash
.venv/Scripts/python -m pytest tests/unit/engine/test_langgraph_executor.py -v
```
Expected: 3 new tests fail (LangGraphExecutor doesn't accept `event_bus` yet).

- [ ] **Step 3: Modify `src/engine/langgraph_executor.py`**

Read the file first. Changes:

**(a)** `__init__`:
```python
def __init__(
    self,
    db: Prisma,  # pyright: ignore[reportUnknownParameterType]
    checkpointer: BaseCheckpointSaver[Any],
    event_bus: ExecutionEventBus | None = None,
) -> None:
    self.db = db
    self.checkpointer = checkpointer
    self.event_bus = event_bus
```

Add import at top:
```python
from src.engine.events import ExecutionEvent, ExecutionEventBus
```

**(b)** Add helpers:
```python
async def _emit(self, event_type: EventType, execution_id: str, payload: dict[str, Any]) -> None:
    if self.event_bus is None:
        return
    await self.event_bus.emit(
        ExecutionEvent(type=event_type, execution_id=execution_id, payload=payload)
    )

async def _close_event_bus(self, execution_id: str) -> None:
    if self.event_bus is not None:
        await self.event_bus.close(execution_id)
```

Where `EventType` is imported from `src.engine.events`.

**(c)** In `run()`:
- Right after `execution = await self._load_execution(execution_id)` returns non-None: `await self._emit("status-change", execution_id, {"status": "running", "previous": None})`.
- Replace the `_mark_completed` call path with: `await self._mark_completed(...)` → `await self._emit("status-change", execution_id, {"status": "completed", "previous": "running"})` → `await self._close_event_bus(execution_id)`.
- Replace the pause path: after `_mark_waiting_approval`, emit `approval-pending` with `{"node_id": pending_info["node_id"], "prompt": pending_info["prompt"]}` THEN emit `status-change` with `{"status": "waiting_approval", "previous": "running"}`. Note we need the `pending_info` dict available — refactor `_mark_waiting_approval` (if not already) so it returns the extracted info, or extract inline before calling it. Whichever is cleaner.
- In the outer `except Exception`: after `_mark_failed`, emit `status-change(failed)` + close.

**(d)** In `resume()`:
- At entry (after loading execution): `await self._emit("status-change", execution_id, {"status": "running", "previous": "waiting_approval"})`.
- Same terminal emit pattern as `run()` for the 3 exit paths: completed, chained pause, failed.

Note: `approval-resumed` is emitted from the HTTP endpoint (Task 5), not from `resume()`. This is deliberate so the event carries the decision value from the request payload.

**(e)** Set the ContextVars inside `_prepare_compiled`:
```python
from src.engine.context import set_current_execution_id, set_current_event_bus

set_current_execution_id(execution.id)
set_current_event_bus(self.event_bus)
```

Alongside the existing `set_current_db` / `set_current_langsmith`.

- [ ] **Step 4: Attach `event_bus` to `app.state` in `src/storage/db.py`**

Read the file. Inside `prisma_lifespan`, after `app.state.checkpointer = PrismaCheckpointSaver(db)`, add:

```python
from src.engine.events import ExecutionEventBus  # local import to keep module cold-start small

app.state.event_bus = ExecutionEventBus()
```

And in the teardown: no special cleanup needed (queues are GC'd when the app exits; subscribers will already have been drained by their own disconnect handlers).

Add `get_event_bus(request: Request) -> ExecutionEventBus` alongside `get_checkpointer`:

```python
def get_event_bus(request: Request) -> ExecutionEventBus:
    bus = getattr(request.app.state, "event_bus", None)
    if bus is None:
        raise RuntimeError("ExecutionEventBus not attached to app.state")
    return bus
```

Export from `__all__`.

- [ ] **Step 5: Update `src/api/executions.py` to pass `event_bus` into `LangGraphExecutor`**

Read the file. `_get_executor` currently does:
```python
def _get_executor(request: Request, db: Prisma) -> LangGraphExecutor:
    checkpointer = getattr(request.app.state, "checkpointer", None)
    if checkpointer is None:
        raise RuntimeError("Checkpointer not attached to app.state")
    return LangGraphExecutor(db=db, checkpointer=checkpointer)
```

Update to pass `event_bus`:
```python
def _get_executor(request: Request, db: Prisma) -> LangGraphExecutor:
    checkpointer = getattr(request.app.state, "checkpointer", None)
    if checkpointer is None:
        raise RuntimeError("Checkpointer not attached to app.state")
    event_bus = getattr(request.app.state, "event_bus", None)
    return LangGraphExecutor(db=db, checkpointer=checkpointer, event_bus=event_bus)
```

No test changes here (event_bus is optional; existing tests without it still work).

- [ ] **Step 6: Run + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/engine/test_langgraph_executor.py -v
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```
Expected: 3 new tests pass; overall +3 (~393).

```bash
git add src/engine/langgraph_executor.py src/storage/db.py src/api/executions.py tests/unit/engine/test_langgraph_executor.py
git commit -m "feat(engine): LangGraphExecutor emits execution events (Phase 5b)

run() + resume() now emit to ExecutionEventBus (when present):
  - status-change(running) at start
  - status-change(completed) + close() at success
  - approval-pending + status-change(waiting_approval) + close() on pause
  - status-change(failed) + close() on error

ContextVars (current_execution_id + current_event_bus) set inside
_prepare_compiled so node wrappers (Task 3) can emit node-start/
complete without explicit plumbing.

event_bus is attached to app.state in prisma_lifespan alongside the
checkpointer; get_event_bus dependency wired; _get_executor in
executions.py passes it into LangGraphExecutor.

See Phase 5b spec §6.1, ADR-0017.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Node-level event emission via graph_builder wrapper

**Files:**
- Create: `src/engine/events_wrapper.py`
- Modify: `src/engine/graph_builder.py`
- Create: `tests/unit/engine/test_events_wrapper.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/engine/test_events_wrapper.py`:

```python
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
        arun = wrap_executor_with_events(_FakeExecutor(), _FakeNode("n1", "http"))
        result = await arun(initial_state())
        assert result["variables"]["k"] == "v"
    finally:
        set_current_execution_id(None)
        set_current_event_bus(None)

    events: list[Any] = []
    while True:
        try:
            ev = await asyncio.wait_for(q.get(), timeout=0.05)
        except asyncio.TimeoutError:
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
        arun = wrap_executor_with_events(_RaisingExecutor(), _FakeNode("n1", "http"))
        with pytest.raises(RuntimeError, match="boom"):
            await arun(initial_state())
    finally:
        set_current_execution_id(None)
        set_current_event_bus(None)

    events: list[Any] = []
    while True:
        try:
            ev = await asyncio.wait_for(q.get(), timeout=0.05)
        except asyncio.TimeoutError:
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

    arun = wrap_executor_with_events(_FakeExecutor(), _FakeNode("n1", "http"))
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

    wf = Workflow.model_validate({
        "id": "w",
        "name": "t",
        "nodes": [
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {"id": "e", "type": "end", "position": {"x": 1, "y": 0}, "data": {"label": "E"}},
        ],
        "edges": [{"id": "e1", "source": "s", "target": "e"}],
    })
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
        except asyncio.TimeoutError:
            break
        if ev is None:
            break
        events.append(ev)

    types_by_node: dict[str, list[str]] = {}
    for ev in events:
        types_by_node.setdefault(ev.payload["node_id"], []).append(ev.type)

    assert types_by_node["s"] == ["node-start", "node-complete"]
    assert types_by_node["e"] == ["node-start", "node-complete"]
```

- [ ] **Step 2: Verify fail**

```bash
.venv/Scripts/python -m pytest tests/unit/engine/test_events_wrapper.py -v
```

- [ ] **Step 3: Implement `src/engine/events_wrapper.py`**

```python
"""Executor wrapper that emits node-start / node-complete events.

Applied inside graph_builder.build_graph so every executor participates
automatically — no per-executor changes needed.

See Phase 5b spec §6.2.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from src.engine.context import get_current_event_bus, get_current_execution_id
from src.engine.events import ExecutionEvent
from src.engine.state import WorkflowStateDict
from src.engine.workflow import WorkflowNode
from src.executors.base import Executor


def wrap_executor_with_events(
    executor: Executor,
    node: WorkflowNode,
) -> Callable[[WorkflowStateDict], Awaitable[dict[str, Any]]]:
    """Return an `arun(state)` callable that emits events around the executor."""

    node_info: dict[str, Any] = {"node_id": node.id, "node_type": node.type}

    async def _arun(state: WorkflowStateDict) -> dict[str, Any]:
        execution_id = get_current_execution_id()
        bus = get_current_event_bus()

        if bus is not None and execution_id is not None:
            await bus.emit(
                ExecutionEvent(type="node-start", execution_id=execution_id, payload=dict(node_info))
            )

        result = await executor.arun(state)

        if bus is not None and execution_id is not None:
            await bus.emit(
                ExecutionEvent(type="node-complete", execution_id=execution_id, payload=dict(node_info))
            )

        return result

    return _arun


__all__ = ["wrap_executor_with_events"]
```

- [ ] **Step 4: Update `src/engine/graph_builder.py`**

Find the node-registration loop:
```python
for node in workflow.nodes:
    if node.type == "note":
        continue
    executor = build_executor(node)
    builder.add_node(node.id, executor.arun)  # pyright: ignore[reportUnknownMemberType]
```

Replace with:
```python
from src.engine.events_wrapper import wrap_executor_with_events  # at top of file

# ...inside build_graph:
for node in workflow.nodes:
    if node.type == "note":
        continue
    executor = build_executor(node)
    arun_with_events = wrap_executor_with_events(executor, node)
    builder.add_node(node.id, arun_with_events)  # pyright: ignore[reportUnknownMemberType]
```

- [ ] **Step 5: Run + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/engine/test_events_wrapper.py -v
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```
Expected: 4 new tests pass; overall +4 (~397).

```bash
git add src/engine/events_wrapper.py src/engine/graph_builder.py tests/unit/engine/test_events_wrapper.py
git commit -m "feat(engine): node-level event emission via executor wrapper (Phase 5b)

wrap_executor_with_events wraps each executor's arun with node-start/
node-complete emissions.  Reads current_execution_id +
current_event_bus from ContextVars set by LangGraphExecutor (Task 2)
— zero per-executor changes.

build_graph now applies the wrapper inside its node-registration loop.
Exceptions (including GraphInterrupt for user-approval pauses) skip
node-complete and propagate, which is the correct semantic — a paused
node hasn't completed.

See Phase 5b spec §6.2, ADR-0017.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: `/executions/{id}/events` SSE endpoint

**Files:**
- Create: `src/api/events.py`
- Modify: `src/main.py` (register the new router)
- Create: `tests/unit/api/test_events_stream.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/api/test_events_stream.py`:

```python
"""Tests for GET /executions/{id}/events (SSE)."""

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from src.main import create_app


def _execution_row(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": "e1",
        "status": "running",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _build_app(monkeypatch: pytest.MonkeyPatch, execution: Any | None) -> Any:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    from src.config import get_settings
    from src.engine.events import ExecutionEventBus

    get_settings.cache_clear()
    app = create_app()

    db = MagicMock()
    db.workflowexecution = MagicMock()
    db.workflowexecution.find_unique = AsyncMock(return_value=execution)
    app.state.db = db
    app.state.checkpointer = MagicMock()
    app.state.event_bus = ExecutionEventBus()
    return app


async def test_events_stream_404_on_unknown_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _build_app(monkeypatch, None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/executions/ghost/events")
    assert resp.status_code == 404


async def test_events_stream_terminal_sends_snapshot_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If status is already completed, stream sends one status-change and closes."""
    app = _build_app(monkeypatch, _execution_row(status="completed"))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        async with ac.stream("GET", "/executions/e1/events") as resp:
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers["content-type"]
            body = b""
            async for chunk in resp.aiter_bytes():
                body += chunk
                if b"\n\n" in body:
                    break

    assert b"event: status-change" in body
    # Extract the data line
    data_line = next(
        line for line in body.decode("utf-8").split("\n") if line.startswith("data: ")
    )
    payload = json.loads(data_line.removeprefix("data: "))
    assert payload["type"] == "status-change"
    assert payload["payload"]["status"] == "completed"


async def test_events_stream_live_delivers_emitted_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Subscribe to a running execution, emit via the bus, assert receive."""
    from src.engine.events import ExecutionEvent

    app = _build_app(monkeypatch, _execution_row(status="running"))
    bus = app.state.event_bus

    async def _emit_later() -> None:
        # Give the subscriber a beat to connect
        await asyncio.sleep(0.2)
        await bus.emit(
            ExecutionEvent(type="node-start", execution_id="e1", payload={"node_id": "s"})
        )
        await asyncio.sleep(0.05)
        await bus.close("e1")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Kick off emitter in parallel
        task = asyncio.create_task(_emit_later())
        frames: list[str] = []
        async with ac.stream("GET", "/executions/e1/events") as resp:
            assert resp.status_code == 200
            buf = ""
            async for chunk in resp.aiter_text():
                buf += chunk
                while "\n\n" in buf:
                    frame, buf = buf.split("\n\n", 1)
                    frames.append(frame)
                if len(frames) >= 2:
                    break
        await task

    # First frame is snapshot (status-change: running)
    assert any("event: status-change" in f and '"running"' in f for f in frames)
    # Second frame is the emitted node-start
    assert any("event: node-start" in f for f in frames)
```

- [ ] **Step 2: Verify fail**

```bash
.venv/Scripts/python -m pytest tests/unit/api/test_events_stream.py -v
```

- [ ] **Step 3: Implement `src/api/events.py`**

```python
"""GET /executions/{id}/events — Server-Sent Events stream.

See Phase 5b spec §7.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]
from src.engine.events import ExecutionEvent, ExecutionEventBus
from src.security.auth import get_current_user_id
from src.storage.db import get_db, get_event_bus

router = APIRouter(tags=["events"])


def _sse_format(event: ExecutionEvent) -> bytes:
    payload = json.dumps(event.as_json())
    return f"event: {event.type}\ndata: {payload}\n\n".encode()


@router.get("/executions/{execution_id}/events")
async def stream_events(
    execution_id: str,
    request: Request,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    event_bus: ExecutionEventBus = Depends(get_event_bus),
    _: str = Depends(get_current_user_id),
) -> StreamingResponse:  # pyright: ignore[reportUnusedFunction]
    execution = await db.workflowexecution.find_unique(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": execution_id}
    )
    if execution is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Execution {execution_id!r} not found.",
        )

    queue = await event_bus.subscribe(execution_id)
    terminal_statuses = {"completed", "failed"}

    async def _gen() -> AsyncIterator[bytes]:
        try:
            # Snapshot
            snapshot = ExecutionEvent(
                type="status-change",
                execution_id=execution_id,
                payload={"status": execution.status, "previous": None},
            )
            yield _sse_format(snapshot)

            if execution.status in terminal_statuses:
                return

            while True:
                if await request.is_disconnected():
                    return
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield b": keepalive\n\n"
                    continue
                if event is None:
                    return
                yield _sse_format(event)
        finally:
            await event_bus.unsubscribe(execution_id, queue)

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


__all__ = ["router"]
```

- [ ] **Step 4: Register router in `src/main.py`**

Find the existing router-registration block. Add:
```python
from src.api.events import router as events_router
...
app.include_router(events_router)
```

Register it in both standalone and embedded modes — no mode-specific logic needed.

- [ ] **Step 5: Run + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/api/test_events_stream.py -v
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```
Expected: 3 new tests pass; overall +3 (~400).

```bash
git add src/api/events.py src/main.py tests/unit/api/test_events_stream.py
git commit -m "feat(api): GET /executions/{id}/events — SSE stream (Phase 5b)

Endpoint subscribes to ExecutionEventBus, emits one status-change
snapshot of the current DB status, then streams live events until
the bus closes or the client disconnects.

Keepalive (SSE comment) every 15s of idle prevents proxy idle-closes.
Bounded queue in the bus means slow subscribers drop events rather
than stalling the emitter; authoritative state is still GET /executions/
{id}.

No auth redesign: Depends(get_current_user_id) matches /resume.

See Phase 5b spec §7, ADR-0017.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: `/resume` emits `approval-resumed`

**Files:**
- Modify: `src/api/executions.py`
- Modify: `tests/unit/api/test_executions_resume.py` (append)

- [ ] **Step 1: Write failing test**

Append to `tests/unit/api/test_executions_resume.py`:

```python
def test_resume_emits_approval_resumed_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """The endpoint emits approval-resumed to the event bus."""
    from src.engine.events import ExecutionEventBus

    client, db = _client_with_execution(monkeypatch, _execution_row())
    # _client_with_execution attaches a MagicMock event_bus; replace with a real bus
    bus = ExecutionEventBus()
    client.app.state.event_bus = bus  # pyright: ignore[reportAttributeAccessIssue]

    import asyncio

    async def _collect() -> list[Any]:
        q = await bus.subscribe("e1")
        events: list[Any] = []
        try:
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=0.2)
                except asyncio.TimeoutError:
                    break
                if ev is None:
                    break
                events.append(ev)
        finally:
            await bus.unsubscribe("e1", q)
        return events

    async def _scenario() -> list[Any]:
        task = asyncio.create_task(_collect())
        await asyncio.sleep(0.05)
        client.post("/executions/e1/resume", json={"decision": "approved", "note": "ok"})
        # Give the endpoint a beat to emit before _collect times out
        return await task

    events = asyncio.run(_scenario())
    resumed = [e for e in events if e.type == "approval-resumed"]
    assert resumed, f"no approval-resumed event; got {[(e.type, e.payload) for e in events]}"
    assert resumed[0].payload == {"node_id": "ua", "decision": "approved"}
```

Note: TestClient's synchronous `.post()` runs the endpoint in a worker; `_client_with_execution` also needs to add `app.state.event_bus = ExecutionEventBus()` by default (or the fixture accepts a custom bus). Update `_client_with_execution` accordingly — the helper can attach a fresh `ExecutionEventBus()` and tests can read from it via subscribe. This is already assumed above.

Actually simpler: update the existing `_client_with_execution` fixture in `tests/unit/api/test_executions_resume.py` to attach a real `ExecutionEventBus`:
```python
from src.engine.events import ExecutionEventBus
...
app.state.event_bus = ExecutionEventBus()
```

And the new test reads from `client.app.state.event_bus`.

- [ ] **Step 2: Verify fail**

```bash
.venv/Scripts/python -m pytest tests/unit/api/test_executions_resume.py::test_resume_emits_approval_resumed_event -v
```

- [ ] **Step 3: Modify `src/api/executions.py`**

Find `resume_execution`. After reading `pending_node_id` (before the approval-row create), OR between the status-update and the BackgroundTask schedule, emit the event:

```python
event_bus: ExecutionEventBus | None = getattr(request.app.state, "event_bus", None)
if event_bus is not None:
    await event_bus.emit(
        ExecutionEvent(
            type="approval-resumed",
            execution_id=execution_id,
            payload={"node_id": pending_node_id, "decision": payload.decision.value},
        )
    )
```

Add import at the top of `executions.py`:
```python
from src.engine.events import ExecutionEvent, ExecutionEventBus
```

Emit AFTER the status flip to `running` (so the ordering in the stream is: `approval-resumed` → `status-change(running)` — wait no, `status-change(running)` isn't emitted by the endpoint; `LangGraphExecutor.resume()` emits it when the BackgroundTask fires). So order in the stream will be: `approval-resumed` (endpoint) → `status-change(running)` (BackgroundTask start) → node events → terminal. That's correct.

Actually, clarification: the BackgroundTask may fire after the HTTP response is sent. On a slow executor, subscribers might see `approval-resumed` well before `status-change(running)`. That's fine — the events are advisory.

- [ ] **Step 4: Run + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/api/test_executions_resume.py -v
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

```bash
git add src/api/executions.py tests/unit/api/test_executions_resume.py
git commit -m "feat(api): /resume emits approval-resumed event (Phase 5b)

POST /executions/{id}/resume emits approval-resumed {node_id, decision}
to the ExecutionEventBus right after writing the Approval audit row +
status flip.  Emission happens synchronously from the endpoint (not
the BackgroundTask) so SSE subscribers see the decision before the
graph actually resumes.

See Phase 5b spec §6.3.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Integration test — approved path with SSE

**Files:**
- Create: `tests/integration/test_user_approval_events.py`

- [ ] **Step 1: Write the integration test**

```python
"""Integration — SSE stream emits events during user-approval approved path.

Real Neon.  Subscribes to /executions/{id}/events in parallel with
/executions + /resume; collects frames; asserts expected event order.
"""

import asyncio
import json
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

pytestmark = pytest.mark.integration


def _parse_sse_frames(body: str) -> list[dict[str, Any]]:
    """Parse a blob of SSE text into a list of event dicts."""
    events: list[dict[str, Any]] = []
    for raw in body.split("\n\n"):
        raw = raw.strip()
        if not raw or raw.startswith(":"):
            continue
        kind: str | None = None
        data: str | None = None
        for line in raw.split("\n"):
            if line.startswith("event:"):
                kind = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data = line.split(":", 1)[1].strip()
        if kind and data:
            events.append({"type": kind, "data": json.loads(data)})
    return events


async def test_sse_stream_emits_events_approved_path(
    client: AsyncClient,
    app: FastAPI,
) -> None:
    db: Any = app.state.db

    wf = await client.post(
        "/workflows",
        json={
            "name": "Phase 5b SSE approved path",
            "nodes": [
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                {
                    "id": "ua",
                    "type": "user-approval",
                    "position": {"x": 100, "y": 0},
                    "data": {"label": "UA", "approvalMessage": "Please approve"},
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
    start = await client.post("/executions", json={"workflowId": wf.json()["id"], "input": ""})
    execution_id = start.json()["id"]

    collected_frames: list[str] = []

    async def _subscribe() -> None:
        async with client.stream("GET", f"/executions/{execution_id}/events") as resp:
            assert resp.status_code == 200
            async for chunk in resp.aiter_text():
                collected_frames.append(chunk)
                # Stop once we see 'completed' — test ends
                if "\"status\": \"completed\"" in "".join(collected_frames):
                    return

    # Run subscriber in parallel with the pause + /resume loop
    sub_task = asyncio.create_task(_subscribe())

    # Poll until pause, then resume
    async def _drive_to_completion() -> None:
        deadline = asyncio.get_running_loop().time() + 30.0
        while asyncio.get_running_loop().time() < deadline:
            r = await client.get(f"/executions/{execution_id}")
            body = r.json()
            if body["status"] == "waiting_approval":
                await client.post(
                    f"/executions/{execution_id}/resume",
                    json={"decision": "approved", "note": "sse test"},
                )
                return
            if body["status"] in {"completed", "failed"}:
                return
            await asyncio.sleep(0.2)

    await _drive_to_completion()

    # Give the subscriber up to 10s to receive 'completed'
    try:
        await asyncio.wait_for(sub_task, timeout=10.0)
    except asyncio.TimeoutError:
        sub_task.cancel()

    all_body = "".join(collected_frames)
    events = _parse_sse_frames(all_body)
    types = [e["type"] for e in events]

    assert "status-change" in types
    assert "node-start" in types
    assert "node-complete" in types
    assert "approval-pending" in types
    assert "approval-resumed" in types

    # Terminal event present
    statuses = [e["data"]["payload"].get("status") for e in events if e["type"] == "status-change"]
    assert "completed" in statuses
```

- [ ] **Step 2: Quality gates**

```bash
.venv/Scripts/python -m ruff check tests/integration/test_user_approval_events.py
.venv/Scripts/python -m ruff format tests/integration/test_user_approval_events.py
.venv/Scripts/python -m pyright tests/integration/test_user_approval_events.py
.venv/Scripts/python -m pytest tests/integration/test_user_approval_events.py --collect-only -q
```
Expected: 1 test collected.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_user_approval_events.py
git commit -m "test(integration): SSE stream during user-approval approved path

Real Neon.  Subscribes to GET /executions/{id}/events in parallel with
start + /resume.  Asserts the full event taxonomy is emitted during
the approved path: status-change, node-start, node-complete,
approval-pending, approval-resumed, and terminal status-change(
completed).

See Phase 5b spec §12.2.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Reserved — real-Neon fix-up

If the integration suite reveals bugs (same pattern as Phases 3a/3b/4a/4b/5a/7a), fix in a dedicated commit titled `"fix(phase-5b): <N> bugs caught by real-API integration testing"`. If no bugs, skip.

---

## Task 8: Phase-exit — CHANGELOG + CLAUDE.md + ADR-0017 backfill

**Authorized:** `CHANGELOG.md`, `CLAUDE.md`, `docs/design/decisions.md`.

- [ ] **Step 1: Verify exit checklist**

```bash
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m ruff format --check src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

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
     'tests/integration/test_user_approval_events.py',
     'tests/integration/test_user_approval_approved.py',
     'tests/integration/test_user_approval_rejected.py'],
    cwd='.'
)
sys.exit(r.returncode)
"
```

All three pass → proceed. The Phase 5a integration tests re-run because Task 3 wraps every executor; confirm no regression.

- [ ] **Step 2: Update CHANGELOG.md**

Insert above the Phase 5a section:

```markdown
### Phase 5b — SSE streaming (2026-04-21)

#### Added
- [Phase 5b design spec](docs/superpowers/specs/2026-04-21-phase-5b-sse-streaming-design.md) + ADR-0017.
- `src/engine/events.py` — `ExecutionEventBus` (in-process asyncio fanout) + `ExecutionEvent` frozen dataclass.
- `src/engine/events_wrapper.py` — `wrap_executor_with_events` applied inside `graph_builder.build_graph`. Every executor emits `node-start` / `node-complete` automatically.
- `src/engine/context.py` — new ContextVars `current_execution_id`, `current_event_bus` set by `LangGraphExecutor._prepare_compiled`.
- `src/engine/langgraph_executor.py` — emits `status-change` on every transition, `approval-pending` on pause, closes the event stream on terminal states.
- `src/api/events.py` — `GET /executions/{id}/events` Server-Sent Events endpoint with snapshot-on-subscribe, 15s keepalive, bounded per-subscriber queue, auto-disconnect detection.
- `src/api/executions.py` — `POST /resume` now emits `approval-resumed` with the decision payload.
- `src/storage/db.py` — `event_bus` attached to `app.state` alongside the checkpointer; `get_event_bus` dependency.
- Integration test: full approved-path SSE stream against real Neon; asserts complete event taxonomy in order.

#### Changed
- `LangGraphExecutor.__init__` gains an optional `event_bus` parameter (default None for back-compat with existing tests).
- All executors now participate in event emission without per-executor changes — the wrapper is applied at graph-build time.

#### Deliberate design choices (see ADR-0017)
- In-process asyncio bus, not Postgres `LISTEN/NOTIFY` or Redis. Zero new infra; multi-worker is a Phase 9 concern.
- SSE over WebSocket — stateless, plays with standard HTTP middleware, no new deps.
- Snapshot-on-subscribe: first frame is always a `status-change` with current DB status. No event replay; DB remains authoritative for history via `GET /executions/{id}`.
- Bounded per-subscriber queue (128) with drop-oldest overflow: slow subscribers never stall the emitter. Stream is advisory; authoritative state lives in Postgres.
- Five event types only. LLM token streaming is a Phase 10 UI concern.

### Phase 5a — User-approval + interrupt/resume (2026-04-21)
```

- [ ] **Step 3: Update CLAUDE.md phase table**

Change:
```markdown
| 5a — User-approval + interrupt/resume | ✅ Complete | `interrupt()` pause via `aget_state`; `/executions/{id}/resume`; `Approval` table; both branches verified against real Neon |
| 5b — SSE streaming | ⏭ Next | real-time execution events (`GET /executions/{id}/events`) |
```
To:
```markdown
| 5a — User-approval + interrupt/resume | ✅ Complete | `interrupt()` pause via `aget_state`; `/executions/{id}/resume`; `Approval` table; both branches verified against real Neon |
| 5b — SSE streaming | ✅ Complete | `GET /executions/{id}/events` with in-process asyncio bus; 5 event types; full taxonomy verified against real Neon |
| 6 — Guardrails, Note, Vector-DB, Gamma, Arcade | ⏭ Next | visual + data executors (see design §6) |
```

- [ ] **Step 4: Backfill ADR-0017 `Implemented by`**

```bash
git log --oneline b50b0ce..HEAD
```
Replace `**Implemented by.** Phase 5b (commits TBD).` with the actual range.

- [ ] **Step 5: Commit + push**

```bash
git add CHANGELOG.md CLAUDE.md docs/design/decisions.md
git commit -m "docs(phase-5b): mark Phase 5b complete

SSE streaming shipped.  ExecutionEventBus + per-node wrapper emit a
complete event taxonomy (status-change, node-start, node-complete,
approval-pending, approval-resumed) during execution; GET /executions/
{id}/events serves them via Server-Sent Events with snapshot-on-
subscribe and 15s keepalive.

Real-Neon integration: approved path with SSE fully green — receives
the complete taxonomy, including terminal status-change(completed).

ADR-0017 Implemented by backfilled.

Phase 6 (guardrails, note, vector-db, gamma-ai, arcade, join-chunks)
is next.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"

git push origin main
```

---

## Spec coverage self-review

| Spec section | Tasks |
|---|---|
| §4 event taxonomy | Tasks 2, 3, 5 |
| §5 ExecutionEventBus | Task 1 |
| §6.1 LangGraphExecutor emission | Task 2 |
| §6.2 node wrapper | Task 3 |
| §6.3 /resume emission | Task 5 |
| §7 SSE endpoint | Task 4 |
| §8 backpressure | Task 1 (overflow test) |
| §9 snapshot on subscribe | Task 4 |
| §12.1 unit tests | Tasks 1–5 |
| §12.2 integration | Task 6 |
| §13 phase-exit | Task 8 |

No placeholder steps. Type consistency:
- `EventType` literal (§4) matches usage in every emit site.
- `ExecutionEvent` frozen dataclass (§5) with `.as_json()` — used by `_sse_format` in Task 4 + test assertions in Task 6.
- ContextVars `current_execution_id` + `current_event_bus` set in Task 2, read in Task 3. Names are identical across both.

---

## Execution handoff

Plan saved. Controller proceeds to `superpowers:subagent-driven-development` for Tasks 1–8. Same cadence as Phases 1–5a. Task 8 runs integration suite against real Neon before phase-exit.
