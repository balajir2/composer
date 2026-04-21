"""Integration — SSE stream emits events during user-approval approved path.

Real Neon.  Subscribes to /executions/{id}/events in parallel with
/executions + /resume; collects frames; asserts expected event taxonomy.

Note: snapshot-on-subscribe means if the subscriber connects after
early node-start events, those events are not replayed.  Assertions
therefore focus on events guaranteed to occur after the subscriber
is established: approval-pending (fires when graph pauses), the
status-change(waiting_approval) right after, approval-resumed (fires
when /resume is POSTed), and the terminal status-change(completed).
"""

import asyncio
import json
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

pytestmark = pytest.mark.integration


def _parse_sse_frames(body: str) -> list[dict[str, Any]]:
    """Parse SSE text into a list of event dicts.  Ignores comment (keepalive)
    lines and malformed frames."""
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
            try:
                events.append({"type": kind, "data": json.loads(data)})
            except json.JSONDecodeError:
                continue
    return events


async def _poll_until_status(
    client: AsyncClient,
    execution_id: str,
    target: set[str],
    timeout: float = 30.0,
) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        resp = await client.get(f"/executions/{execution_id}")
        body: dict[str, Any] = resp.json()
        if body["status"] in target:
            return body
        await asyncio.sleep(0.2)
    raise AssertionError(f"Execution {execution_id} did not reach {target} within {timeout}s")


async def test_sse_stream_emits_events_approved_path(
    client: AsyncClient,
    app: FastAPI,
) -> None:
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

    collected_text = ""

    async def _subscribe() -> str:
        nonlocal collected_text
        buf = ""
        async with client.stream("GET", f"/executions/{execution_id}/events") as resp:
            assert resp.status_code == 200
            async for chunk in resp.aiter_text():
                buf += chunk
                # Stop once we see a terminal event so the test doesn't hang
                if '"status": "completed"' in buf or '"status": "failed"' in buf:
                    break
        collected_text = buf
        return buf

    sub_task = asyncio.create_task(_subscribe())

    # Wait for pause (driven by the subscriber running in parallel)
    paused = await _poll_until_status(
        client, execution_id, {"waiting_approval", "completed", "failed"}
    )
    assert paused["status"] == "waiting_approval", f"Got: {paused}"

    # Submit approval
    resume = await client.post(
        f"/executions/{execution_id}/resume",
        json={"decision": "approved", "note": "sse test"},
    )
    assert resume.status_code == 200

    # Wait for the execution to complete on the backend
    done = await _poll_until_status(client, execution_id, {"completed", "failed"})
    assert done["status"] == "completed", f"Got: {done}"

    # Give the subscriber up to 10s to receive the terminal event
    try:
        await asyncio.wait_for(sub_task, timeout=10.0)
    except TimeoutError:
        sub_task.cancel()

    events = _parse_sse_frames(collected_text)
    types = [e["type"] for e in events]

    # Events guaranteed to occur after the subscriber connects:
    assert "approval-pending" in types, f"no approval-pending; types={types}"
    assert "approval-resumed" in types, f"no approval-resumed; types={types}"

    # Terminal status-change(completed) must appear
    statuses = [e["data"]["payload"].get("status") for e in events if e["type"] == "status-change"]
    assert "completed" in statuses, f"no completed status-change; statuses={statuses}"

    # At least one node-start or node-complete should appear (for ok/no/e nodes,
    # which run AFTER /resume so the subscriber definitely saw them)
    assert "node-start" in types or "node-complete" in types, f"no node events; types={types}"

    # approval-pending payload sanity
    pending = next(e for e in events if e["type"] == "approval-pending")
    assert pending["data"]["payload"]["node_id"] == "ua"
    assert pending["data"]["payload"]["prompt"] == "Please approve"

    # approval-resumed payload sanity
    resumed = next(e for e in events if e["type"] == "approval-resumed")
    assert resumed["data"]["payload"]["node_id"] == "ua"
    assert resumed["data"]["payload"]["decision"] == "approved"
