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
"""

from __future__ import annotations

import asyncio
import json
from typing import Literal

from google.cloud import tasks_v2

from src.config import get_settings

_TaskKind = Literal["run", "resume"]

_client: tasks_v2.CloudTasksAsyncClient | None = None
_client_lock = asyncio.Lock()


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


async def enqueue_execution(execution_id: str, *, kind: _TaskKind) -> None:
    """Enqueue a Cloud Task that will trigger claim-and-run for `execution_id`.

    `kind` distinguishes a fresh run from an approval-resume so the
    claim-and-run endpoint knows which LangGraphExecutor method to call —
    both still go through the same claim (FOR UPDATE SKIP LOCKED) guard.
    """
    settings = get_settings()
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
