"""Integration — Start → Set-State → If-Else → [A|B] → End.

Real Neon, no LLM needed.  Verifies conditional-edge routing works end
to end for the if-else node type.
"""

import asyncio
from typing import Any, cast

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def _poll_until_terminal(
    client: AsyncClient,
    execution_id: str,
    timeout: float = 30.0,
) -> dict[str, object]:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        resp = await client.get(f"/executions/{execution_id}")
        body: dict[str, object] = resp.json()
        if body["status"] in {"completed", "failed"}:
            return body
        await asyncio.sleep(0.2)
    raise AssertionError(f"Execution {execution_id} did not terminate within {timeout}s")


async def test_if_else_takes_true_branch(client: AsyncClient) -> None:
    wf = await client.post(
        "/workflows",
        json={
            "name": "Phase 4b if-else (true branch)",
            "nodes": [
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                {
                    "id": "init",
                    "type": "set-state",
                    "position": {"x": 100, "y": 0},
                    "data": {"label": "init", "stateKey": "x", "stateValue": 5},
                },
                {
                    "id": "ie",
                    "type": "if-else",
                    "position": {"x": 200, "y": 0},
                    "data": {"label": "IE", "condition": "variables['x'] > 0"},
                },
                {
                    "id": "ta",
                    "type": "set-state",
                    "position": {"x": 300, "y": -50},
                    "data": {"label": "Ta", "stateKey": "result", "stateValue": "A"},
                },
                {
                    "id": "tb",
                    "type": "set-state",
                    "position": {"x": 300, "y": 50},
                    "data": {"label": "Tb", "stateKey": "result", "stateValue": "B"},
                },
                {"id": "e", "type": "end", "position": {"x": 400, "y": 0}, "data": {"label": "E"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "init"},
                {"id": "e2", "source": "init", "target": "ie"},
                {"id": "e3", "source": "ie", "target": "ta", "branch": "true"},
                {"id": "e4", "source": "ie", "target": "tb", "branch": "false"},
                {"id": "e5", "source": "ta", "target": "e"},
                {"id": "e6", "source": "tb", "target": "e"},
            ],
        },
    )
    assert wf.status_code == 201, wf.text
    start = await client.post(
        "/executions",
        json={"workflowId": wf.json()["id"], "input": ""},
    )
    final = await _poll_until_terminal(client, start.json()["id"])
    assert final["status"] == "completed", f"Got: {final}"
    variables = cast("dict[str, Any]", final.get("variables") or {})
    assert isinstance(variables, dict)
    assert variables.get("result") == "A"


async def test_if_else_takes_false_branch(client: AsyncClient) -> None:
    wf = await client.post(
        "/workflows",
        json={
            "name": "Phase 4b if-else (false branch)",
            "nodes": [
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                {
                    "id": "init",
                    "type": "set-state",
                    "position": {"x": 100, "y": 0},
                    "data": {"label": "init", "stateKey": "x", "stateValue": -3},
                },
                {
                    "id": "ie",
                    "type": "if-else",
                    "position": {"x": 200, "y": 0},
                    "data": {"label": "IE", "condition": "variables['x'] > 0"},
                },
                {
                    "id": "ta",
                    "type": "set-state",
                    "position": {"x": 300, "y": -50},
                    "data": {"label": "Ta", "stateKey": "result", "stateValue": "A"},
                },
                {
                    "id": "tb",
                    "type": "set-state",
                    "position": {"x": 300, "y": 50},
                    "data": {"label": "Tb", "stateKey": "result", "stateValue": "B"},
                },
                {"id": "e", "type": "end", "position": {"x": 400, "y": 0}, "data": {"label": "E"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "init"},
                {"id": "e2", "source": "init", "target": "ie"},
                {"id": "e3", "source": "ie", "target": "ta", "branch": "true"},
                {"id": "e4", "source": "ie", "target": "tb", "branch": "false"},
                {"id": "e5", "source": "ta", "target": "e"},
                {"id": "e6", "source": "tb", "target": "e"},
            ],
        },
    )
    assert wf.status_code == 201, wf.text
    start = await client.post(
        "/executions",
        json={"workflowId": wf.json()["id"], "input": ""},
    )
    final = await _poll_until_terminal(client, start.json()["id"])
    assert final["status"] == "completed", f"Got: {final}"
    variables = cast("dict[str, Any]", final.get("variables") or {})
    assert isinstance(variables, dict)
    assert variables.get("result") == "B"
