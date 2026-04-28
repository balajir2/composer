# Phase 5b — SSE streaming: Design

**Status.** Approved 2026-04-21.
**Supersedes.** n/a — new scope.
**Related.** Phase 5a (user-approval + interrupt/resume), ADR-0016, ADR-0017 (this phase).

---

## 1. Goal

Ship `GET /executions/{execution_id}/events` — a Server-Sent Events stream delivering real-time workflow progress to clients so polling `/executions/{id}` is no longer the primary UX path. Same execution model as Phase 5a; this is additive.

## 2. Non-goals

- **No LLM token streaming.** Per-token streaming of LLM responses is a Phase 10 frontend concern; 5b emits coarse-grained events only. If agents stream to stdout/LangSmith, that is unaffected.
- **No cross-worker fanout.** Single-process asyncio queue. Multi-worker deployments with >1 uvicorn worker will only deliver events to subscribers connected to the same worker as the executor. This is acceptable for the current (dev + single-worker prod) target. Phase 9 (WebSocket cutover) revisits.
- **No persistent event log.** Events live only in memory until delivered. A client that subscribes AFTER the execution ends sees only the initial snapshot (see §9). Replay of historical events is a Phase 7 feature if we need it.
- **No new frontend.** This spec only ships the server side. Phase 10 consumes it.
- **No auth redesign.** Reuses `Depends(get_current_user_id)` from Phase 7a; any authenticated user can subscribe to any execution in 5b. RBAC lands in Phase 7b+ (same cadence as 5a's `/resume`).

## 3. Architecture overview

```
┌────────────────────────────────────────────────────────────────────┐
│ FastAPI app (single process)                                       │
│                                                                    │
│  ┌─────────────────────┐        ┌──────────────────────────────┐   │
│  │ LangGraphExecutor   │        │ ExecutionEventBus (app.state)│   │
│  │  - run()            │ emit() │   {exec_id → list[Queue]}    │   │
│  │  - resume()         ├───────>│   subscribe(exec_id) → Queue │   │
│  └─────────────────────┘        └─────────────┬────────────────┘   │
│          │                                    │                    │
│          │ wraps each executor                │                    │
│          v                                    │                    │
│  ┌─────────────────────┐                      │                    │
│  │ EventEmittingWrapper│                      │                    │
│  │ node-start/complete │                      │                    │
│  └─────────────────────┘                      │                    │
│                                               v                    │
│                                  ┌──────────────────────────────┐  │
│                                  │ GET /executions/{id}/events  │  │
│                                  │   StreamingResponse          │  │
│                                  │   text/event-stream          │  │
│                                  │   snapshot + live + keepalive│  │
│                                  └──────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────┘
```

**Key objects:**

- `ExecutionEventBus` — app-scoped singleton attached to `app.state.event_bus` in the existing lifespan. Maps `execution_id → list[asyncio.Queue[ExecutionEvent | None]]`. Thread-unsafe (asyncio only, single event loop).
- `ExecutionEvent` — a tagged dataclass with `type`, `execution_id`, `timestamp`, and a typed `payload`.
- `EventEmittingWrapper` — a thin executor wrapper in `src/engine/events_wrapper.py` that `node-start`s before `arun`, then `node-complete`s (or the exception propagates). Applied inside `graph_builder.build_graph` when registering nodes.

**ContextVar for execution_id:** `src/engine/context.py` adds `set_current_execution_id(execution_id)` / `get_current_execution_id()`. `LangGraphExecutor._prepare_compiled` sets it before `ainvoke`, clears after. Emitters read from the ContextVar; no explicit plumbing through state.

## 4. Event taxonomy (MVP)

Exactly five event types:

| `type`              | When emitted                                      | Payload                                                |
|---------------------|---------------------------------------------------|--------------------------------------------------------|
| `status-change`     | Execution status transition                       | `{status: str, previous: str \| null}`                 |
| `node-start`        | Before executor's `arun`                          | `{node_id: str, node_type: str}`                       |
| `node-complete`     | After executor's `arun` returns successfully      | `{node_id: str, node_type: str}`                       |
| `approval-pending`  | Graph paused (Phase 5a detection)                 | `{node_id: str, prompt: str}`                          |
| `approval-resumed`  | `/executions/{id}/resume` accepts                 | `{node_id: str, decision: 'approved' \| 'rejected'}`   |

All events share a common envelope:

```json
{
  "type": "<one of the five>",
  "execution_id": "cmo...",
  "timestamp": "2026-04-21T10:30:45.123Z",
  "payload": { ... }
}
```

Rejected design: emitting one `status-change` with `status='waiting_approval'` plus a separate `approval-pending` would double-signal the pause. We emit **only** `approval-pending` on pause; the status change is implicit. Same on resume — `approval-resumed` only; the subsequent `status-change` to `running` is emitted as a normal transition.

Actually no — clarification: we DO emit `status-change` AND the approval-specific event, because the approval event is richer. Consumers that only care about coarse state can listen to `status-change` alone; consumers that want the prompt/decision listen to the approval events. Both are emitted.

**No `node-error`.** If an executor raises, the execution transitions to `failed` → emits `status-change` with `status: 'failed'`. The executor's exception message lives on `WorkflowExecution.error`, fetchable via the existing `GET /executions/{id}`.

## 5. `ExecutionEventBus`

**File:** `src/engine/events.py` (new).

```python
from __future__ import annotations
import asyncio
from dataclasses import dataclass, field, asdict
from datetime import UTC, datetime
from typing import Any, Literal

EventType = Literal[
    "status-change",
    "node-start",
    "node-complete",
    "approval-pending",
    "approval-resumed",
]

@dataclass(frozen=True)
class ExecutionEvent:
    type: EventType
    execution_id: str
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    payload: dict[str, Any] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        return asdict(self)


class ExecutionEventBus:
    """Per-execution fanout of ExecutionEvent, in-process only.

    Bounded queue per subscriber (maxsize=128); on overflow the oldest
    undelivered event is dropped so a slow client never stalls the emitter.
    """

    _MAX_QUEUE = 128

    def __init__(self) -> None:
        self._queues: dict[str, list[asyncio.Queue[ExecutionEvent | None]]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, execution_id: str) -> asyncio.Queue[ExecutionEvent | None]:
        """Attach a new subscriber; returns a fresh Queue."""
        q: asyncio.Queue[ExecutionEvent | None] = asyncio.Queue(maxsize=self._MAX_QUEUE)
        async with self._lock:
            self._queues.setdefault(execution_id, []).append(q)
        return q

    async def unsubscribe(self, execution_id: str, q: asyncio.Queue[Any]) -> None:
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
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Drop oldest and retry once
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    pass  # still full; drop this event

    async def close(self, execution_id: str) -> None:
        """Signal all subscribers that no more events will arrive."""
        async with self._lock:
            queues = list(self._queues.get(execution_id, []))
        for q in queues:
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                pass
```

The bus is attached to `app.state.event_bus` in `src/storage/db.py:prisma_lifespan` alongside the checkpointer.

`close(execution_id)` is called by `LangGraphExecutor` after emitting the terminal `status-change` so that subscribers can cleanly drain and disconnect (None sentinel).

## 6. Emit points

Events are emitted from three places:

### 6.1 `LangGraphExecutor.run` / `resume`

- Before `ainvoke`: emit `status-change` with the current status (`running`). On resume, the same.
- After `_mark_completed`: emit `status-change` with `completed`.
- After pause detection: emit `approval-pending` + `status-change` with `waiting_approval`.
- After `_mark_failed`: emit `status-change` with `failed`.
- After emitting any terminal event: call `event_bus.close(execution_id)`.

The bus reference is stored on `LangGraphExecutor.__init__` (new parameter, wired from `app.state.event_bus`).

### 6.2 Node wrapping in `graph_builder`

`build_graph` currently does:
```python
executor = build_executor(node)
builder.add_node(node.id, executor.arun)
```

Replace with:
```python
executor = build_executor(node)
wrapped = _event_emitting_wrapper(executor, node)
builder.add_node(node.id, wrapped)
```

Where `_event_emitting_wrapper` is defined in `src/engine/events_wrapper.py`:

```python
async def _arun_with_events(state: WorkflowStateDict) -> dict[str, Any]:
    execution_id = get_current_execution_id()
    bus = get_current_event_bus()
    node_info = {"node_id": node.id, "node_type": node.type}

    if bus and execution_id:
        await bus.emit(ExecutionEvent(
            type="node-start", execution_id=execution_id, payload=node_info,
        ))

    result = await executor.arun(state)  # may raise; may call interrupt()

    if bus and execution_id:
        await bus.emit(ExecutionEvent(
            type="node-complete", execution_id=execution_id, payload=node_info,
        ))
    return result
```

**Interrupt interaction:** when `interrupt()` raises `GraphInterrupt` inside the wrapped `arun`, the `node-complete` emit is skipped (correct — the node hasn't completed, it's paused). The subsequent `approval-pending` emit from `LangGraphExecutor.run` covers the pause semantics.

### 6.3 `/executions/{id}/resume` endpoint

Before scheduling the BackgroundTask, emit:
```python
await event_bus.emit(ExecutionEvent(
    type="approval-resumed",
    execution_id=execution_id,
    payload={"node_id": pending_node_id, "decision": payload.decision.value},
))
```

## 7. SSE endpoint — `GET /executions/{execution_id}/events`

**File:** `src/api/events.py` (new).

**Shape:**
```python
@router.get("/executions/{execution_id}/events")
async def stream_events(
    execution_id: str,
    request: Request,
    db: Prisma = Depends(get_db),
    _: str = Depends(get_current_user_id),
) -> StreamingResponse:
    execution = await db.workflowexecution.find_unique(where={"id": execution_id})
    if execution is None:
        raise HTTPException(404, f"Execution {execution_id!r} not found.")

    event_bus: ExecutionEventBus = request.app.state.event_bus
    queue = await event_bus.subscribe(execution_id)

    async def _gen() -> AsyncIterator[bytes]:
        try:
            # Snapshot first — emit current status
            snapshot = ExecutionEvent(
                type="status-change",
                execution_id=execution_id,
                payload={"status": execution.status, "previous": None},
            )
            yield _sse_format(snapshot)

            # If already terminal, we're done
            if execution.status in {"completed", "failed"}:
                return

            # Live loop
            while True:
                if await request.is_disconnected():
                    return
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield b": keepalive\n\n"  # SSE comment — valid heartbeat
                    continue
                if event is None:
                    return  # close sentinel
                yield _sse_format(event)
        finally:
            await event_bus.unsubscribe(execution_id, queue)

    return StreamingResponse(_gen(), media_type="text/event-stream")


def _sse_format(event: ExecutionEvent) -> bytes:
    """Encode as SSE frame: `event:` + `data:` + blank line."""
    data_json = json.dumps(event.as_json())
    return f"event: {event.type}\ndata: {data_json}\n\n".encode("utf-8")
```

**Key properties:**

- **Snapshot-on-subscribe.** First frame is always a `status-change` with the current DB status. No replay of past node-start/complete events.
- **Terminal short-circuit.** If status is already `completed` or `failed`, send the snapshot and close. No queue wait.
- **Disconnect check every 15s (via keepalive timeout).** Prevents zombie subscribers when a client disappears without closing.
- **Keepalive.** SSE comment line `: keepalive\n\n` every 15s of idle. Proxies/load balancers often idle-close connections after 30-60s.
- **Bounded by event queue.** Emitter never blocks (§5).

**Auth.** `Depends(get_current_user_id)` — any authenticated user. No execution-ownership check in 5b (consistent with `/resume`).

## 8. Disconnect + backpressure

- **Subscriber slow:** Queue is bounded at 128; overflow drops the oldest undelivered event. An important event can be dropped — consumers must treat the stream as advisory, and the authoritative state is still `GET /executions/{id}`. Documented in the CHANGELOG.
- **Subscriber dead (network drop, client crash):** Detected at next keepalive interval (≤15s). `request.is_disconnected()` returns True, generator exits, `finally` runs `unsubscribe`.
- **Emitter crash:** The executor raising clears the ContextVar; the bus still routes any pending events. Terminal `status-change(failed)` + `close()` fire from the `except` arm of `run()`/`resume()`.

## 9. Replay on subscribe

**Only one event is replayed: a synthetic `status-change` with the current DB status and `previous: null`.** This lets a client that connects mid-execution get its bearings without a replay mechanism.

No node-start/complete replay. If a client wants full history, poll `GET /executions/{id}` — its `nodeResults` has everything that has finished.

## 10. Auth

Same as `/resume`: `Depends(get_current_user_id)`. Any authenticated user. Dev-mode fallback (ADR-0015) returns `'dev'`. RBAC is Phase 7b+.

## 11. Error model

| Condition                                       | HTTP | Response                                       |
|-------------------------------------------------|------|------------------------------------------------|
| Missing / invalid JWT (production)              | 401  | `{detail: "Not authenticated"}`                |
| Execution id not found                          | 404  | `{detail: "Execution ... not found."}`         |
| Any other startup error                         | 500  | Default FastAPI                                |

Once the stream is established, no HTTP error is possible — the stream just ends. A failed execution is signalled via a `status-change(failed)` event before close.

## 12. Test plan

### 12.1 Unit

1. `ExecutionEventBus.subscribe + emit + unsubscribe` — basic fanout to one subscriber.
2. `ExecutionEventBus` multi-subscriber fanout — emit once, both subscribers see it.
3. `ExecutionEventBus` queue overflow — emit 200 events, no exception, some dropped.
4. `ExecutionEventBus.close` — subscriber receives `None` sentinel.
5. `EventEmittingWrapper` emits `node-start` + `node-complete` around arun (happy path).
6. `EventEmittingWrapper` emits `node-start` but NOT `node-complete` when arun raises. (`GraphInterrupt` propagates; other exceptions propagate too.)
7. `LangGraphExecutor.run` emits `status-change(running)` at start + `status-change(completed)` at end + `close()`.
8. `LangGraphExecutor.run` emits `approval-pending` + `status-change(waiting_approval)` + `close()` on pause.
9. `LangGraphExecutor.run` emits `status-change(failed)` + `close()` on error.
10. `LangGraphExecutor.resume` emits `approval-resumed` + `status-change(running)` + terminal.
11. `POST /executions/{id}/resume` emits `approval-resumed` (the endpoint's own emit, not run()'s).
12. `GET /executions/{id}/events` — 404 when execution_id unknown.
13. `GET /executions/{id}/events` — terminal execution replays snapshot + closes immediately.
14. `GET /executions/{id}/events` — live execution: subscribe, simulate an emit, receive the frame.

### 12.2 Integration (real Neon)

1. **Approved path with SSE.** Register the user-approval workflow from Phase 5a. Subscribe to SSE. Start execution. Assert received events include, in order:
   - `status-change(running)`
   - `node-start(s)` → `node-complete(s)`
   - `node-start(ua)` (no `node-complete` for ua before pause)
   - `approval-pending(ua, "Please approve")`
   - `status-change(waiting_approval)`
   - `approval-resumed(ua, approved)` after `/resume`
   - `status-change(running)` (resumption)
   - `node-start(ok)` → `node-complete(ok)` → `node-start(e)` → `node-complete(e)`
   - `status-change(completed)`
   - stream ends.

2. **Late subscriber.** Start execution, let it complete, THEN subscribe. Assert receive one `status-change(completed)` frame and stream ends.

## 13. Phase-exit checklist

- [ ] All unit tests green.
- [ ] Both integration tests green against real Neon.
- [ ] Ruff + format + pyright strict clean.
- [ ] `CHANGELOG.md` updated with Phase 5b section.
- [ ] `CLAUDE.md` phase table updated: 5b → ✅, next phase is 6.
- [ ] ADR-0017 appended + Implemented-by backfilled.

## 14. Risks + future

- **Event loss under backpressure.** Bounded queue drops oldest on overflow. Acceptable because stream is advisory; DB is authoritative. If in practice we see observable drops, Phase 9 swaps to WebSocket with proper flow control or to Postgres LISTEN/NOTIFY.
- **Multi-worker deployments.** Current design is single-worker. If we scale horizontally, subscribers only see events from the worker running the executor. Phase 9 fixes with a cross-worker bus (Postgres LISTEN/NOTIFY is the most likely path).
- **No persistent log.** A client that subscribes after completion only sees the terminal status, not the intermediate node-start/complete trail. If UI wants a replayable timeline, Phase 7 or 10 can add a ring-buffer or DB-backed event log.
- **Proxy buffering.** Some load balancers buffer `text/event-stream` responses. Response is returned with `Cache-Control: no-cache` and `X-Accel-Buffering: no` as a hint; we verify with real Neon + our own TestClient.
- **LangGraph retries.** If Pregel retries a node, we'll emit `node-start` twice. Acceptable for 5b — consumers see the retry cleanly.

## 15. ADR-0017

**Title.** SSE streaming uses in-process asyncio event bus with snapshot-on-subscribe.
**Status.** Accepted.
**Date.** 2026-04-21.
**Context.** Clients currently poll `GET /executions/{id}` for progress. Three approaches for real-time:
- In-process asyncio bus (this ADR).
- Postgres LISTEN/NOTIFY (works across workers; adds a DB connection per subscriber).
- Redis pub/sub (new infra).

**Decision.** In-process asyncio bus. Matches current single-worker target; YAGNI for multi-worker until we scale. SSE (not WebSocket) because SSE is stateless, plays well with standard HTTP middleware, and needs no new dependencies; Phase 9 adds WebSocket as a separate channel.

**Consequences.**
- Zero new infrastructure.
- Multi-worker limitation — documented + acceptable for now.
- Event loss under backpressure — documented; DB is authoritative.
- No persistent event log — clients that subscribe late get only the current snapshot.

**Implemented by.** Phase 5b (commits TBD).

**Related.** ADR-0001 (PrismaCheckpointSaver — same single-process assumption), ADR-0016 (Phase 5a user-approval — emits `approval-pending` / `approval-resumed`).

## 16. Self-review

- Placeholder scan — none.
- Internal consistency — `EventEmittingWrapper` (§6.2) matches the emit points in §4 and §12. `ExecutionEventBus` API (§5) matches usage in §7 and §12.
- Scope — five event types, one endpoint, one bus, one wrapper. Single-plan territory.
- Ambiguity — `approval-pending` vs `status-change(waiting_approval)` dual-emit is explicit in §4. Keepalive interval 15s, queue bound 128, `close()` semantics all pinned.
