"""Cloud Tasks enqueue wrapper for durable execution (P1-2).

Every execution/resume that today goes through `BackgroundTasks.add_task`
or `asyncio.create_task` (src/api/executions.py, src/api/approval_email.py,
src/api/run.py) is replaced by enqueueing a Cloud Task carrying the
execution ID. Cloud Tasks delivers an OIDC-authenticated HTTP POST to
/internal/claim-and-run (src/api/internal.py) — because that's a real
inbound request, Cloud Run keeps the instance alive for its duration,
which is the actual fix for the scale-to-zero risk this whole subsystem
exists to close (ADR-0033).

Cloud Tasks' at-least-once delivery does NOT by itself guarantee a task
runs exactly once — the claim-and-run endpoint's SELECT ... FOR UPDATE
SKIP LOCKED (already validated: scripts/poc_persistence_row_lock.py) is
what actually prevents a double-run if Cloud Tasks redelivers.

CloudTasksAsyncClient provisions a grpc_asyncio transport and resolves
ADC credentials at construction time, and its own __aexit__ closes the
transport — the SDK's signal that instances are meant to be reused as a
long-lived singleton, not constructed per call. We therefore cache one
client module-globally (mirroring src/engine/events_notify.py's NOTIFY
connection cache) instead of building a fresh one on every enqueue.

**Dev-mode fallback:** `settings.cloud_tasks_service_account` is empty
until real GCP infra (queue + OIDC service account, `scripts/gcp-
bootstrap.ps1` section 1a) is provisioned — deliberately not required
for local development or CI (ADR-0033/P1-2 postmortem: enqueueing
unconditionally against a real, unprovisioned Cloud Tasks queue made
every local `POST /executions` fail outright). When it's unset,
`enqueue_execution` skips the real Cloud Tasks RPC entirely and instead
dispatches `claim_and_run_execution` (src/api/internal.py) in-process via
`asyncio.create_task` — the exact same claim (FOR UPDATE SKIP LOCKED) and
run/resume logic Cloud Tasks' push delivery would otherwise trigger over
HTTP, just invoked directly instead of round-tripping through the
network. This mirrors the existing dev-mode bypass in `src/api/
internal.py`'s `_verify_internal_oidc` (itself following the ADR-0015
pattern) rather than inventing a second, differently-shaped escape hatch.
`asyncio.create_task` (not a plain `await`) preserves enqueue_execution's
fire-and-forget contract: callers rely on it returning before the
execution finishes (see the 'queued' status comment in
src/api/executions.py), and a long-running workflow must not block the
original HTTP request/response cycle just because there's no real queue.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Literal

from google.cloud import tasks_v2

from src.config import get_settings

logger = logging.getLogger(__name__)

_TaskKind = Literal["run", "resume"]

_client: tasks_v2.CloudTasksAsyncClient | None = None
_client_lock = asyncio.Lock()

# asyncio only holds a *weak* reference to a task created via
# `asyncio.create_task` — with nothing else referencing it, it can be
# garbage-collected mid-run (silently killing the fallback execution)
# before it ever awaits anything. This module-level set is the standard
# workaround (see asyncio.create_task's own docs): keep a strong
# reference until the task finishes, then let its done-callback discard
# it so the set doesn't grow unbounded.
_background_tasks: set[asyncio.Task[None]] = set()


async def _get_client() -> tasks_v2.CloudTasksAsyncClient:
    """Lazily create (and cache) the Cloud Tasks client."""
    global _client
    if _client is not None:
        return _client
    async with _client_lock:
        # Double-check inside the lock in case another concurrent caller
        # already created the client while we were waiting.
        if _client is None:
            _client = tasks_v2.CloudTasksAsyncClient()
        return _client


async def close_cloud_tasks_client() -> None:
    """Close the cached Cloud Tasks client (call on app shutdown)."""
    global _client
    if _client is not None:
        await _client.transport.close()
    _client = None


async def _run_in_process(execution_id: str, kind: _TaskKind, db: Any) -> None:
    """Dev-mode fallback: run `claim_and_run_execution` directly instead of
    enqueueing a real Cloud Task. See this module's docstring.

    Deferred import avoids a module-level cycle risk between the api and
    execution layers (mirrors the existing deferred-import pattern used
    for this exact function elsewhere: src/storage/db.py's
    prisma_lifespan, src/maintenance/execution_sweeper.py's
    sweep_expired_leases).

    checkpointer/event_bus are constructed fresh here rather than fetched
    off `app.state` (what the HTTP endpoint does) — both classes are thin,
    stateless wrappers around `db` (see their own `__init__`), so a fresh
    instance wrapping the SAME `db` connection is behaviorally identical
    to the app-wide singleton, without requiring this module to carry a
    FastAPI `Request`/`app` reference it otherwise has no use for.
    """
    from src.api.internal import claim_and_run_execution
    from src.engine.events_pg import PostgresEventStore
    from src.storage.checkpointer import PrismaCheckpointSaver

    checkpointer = PrismaCheckpointSaver(db)
    event_bus = PostgresEventStore(db)
    try:
        await claim_and_run_execution(
            execution_id,
            kind,
            db,
            checkpointer,
            event_bus,
            worker_id=f"in-process:{execution_id}",
        )
    except Exception:
        # Fire-and-forget from enqueue_execution's caller's point of view
        # (matching real Cloud Tasks' async delivery) — nothing downstream
        # can catch this, so it must be logged here rather than left to
        # surface only as an "asyncio: Task exception was never retrieved"
        # warning. claim_and_run_execution already marks the row 'failed'
        # on its own internal crash paths; this is a last-resort net for
        # anything that escapes that (e.g. a bug in the fallback wiring
        # itself).
        logger.exception(
            "cloud_tasks: in-process fallback failed for execution %s (kind=%s)",
            execution_id,
            kind,
        )


async def enqueue_execution(execution_id: str, *, kind: _TaskKind, db: Any) -> None:
    """Enqueue a Cloud Task that will trigger claim-and-run for `execution_id`.

    `kind` distinguishes a fresh run from an approval-resume so the
    claim-and-run endpoint knows which LangGraphExecutor method to call —
    both still go through the same claim (FOR UPDATE SKIP LOCKED) guard.

    `db` is only used by the dev-mode fallback (see module docstring) —
    real Cloud Tasks delivery re-derives its own `db` from `app.state` via
    the claim-and-run endpoint's normal dependency injection. Every
    caller already has `db` in scope (it's `app.state.db`, threaded in via
    each route's own `Depends(get_db)`), so this adds no new plumbing.
    """
    settings = get_settings()
    if not settings.cloud_tasks_service_account:
        fallback_task = asyncio.create_task(_run_in_process(execution_id, kind, db))
        _background_tasks.add(fallback_task)
        fallback_task.add_done_callback(_background_tasks.discard)
        return
    client = await _get_client()
    queue_path = client.queue_path(
        settings.gcp_project_id, settings.gcp_region, settings.cloud_tasks_queue
    )
    url = f"{settings.backend_public_url}/internal/claim-and-run"
    body = json.dumps({"executionId": execution_id, "kind": kind}).encode()

    task: dict[str, object] = {
        "http_request": {
            "http_method": tasks_v2.HttpMethod.POST,
            "url": url,
            "headers": {"Content-Type": "application/json"},
            "body": body,
            "oidc_token": {
                "service_account_email": settings.cloud_tasks_service_account,
                "audience": url,
            },
        }
    }
    await client.create_task(request={"parent": queue_path, "task": task})


__all__ = ["close_cloud_tasks_client", "enqueue_execution"]
