"""Integration — Start → HTTP → Extract → Set-State → End.

Real Neon + real Anthropic + public jsonplaceholder HTTP endpoint.
"""

import asyncio
import os
from typing import Any, cast

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def _poll_until_terminal(
    client: AsyncClient,
    execution_id: str,
    timeout: float = 120.0,
) -> dict[str, object]:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        resp = await client.get(f"/executions/{execution_id}")
        body: dict[str, object] = resp.json()
        if body["status"] in {"completed", "failed"}:
            return body
        await asyncio.sleep(0.3)
    raise AssertionError(f"Execution {execution_id} did not terminate within {timeout}s")


async def test_linear_workflow_http_extract_set_state(client: AsyncClient) -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")

    wf = await client.post(
        "/workflows",
        json={
            "name": "Phase 4a linear workflow",
            "nodes": [
                {
                    "id": "s",
                    "type": "start",
                    "position": {"x": 0, "y": 0},
                    "data": {"label": "S"},
                },
                {
                    "id": "h",
                    "type": "http",
                    "position": {"x": 100, "y": 0},
                    "data": {
                        "label": "H",
                        "httpMethod": "GET",
                        "httpUrl": "https://jsonplaceholder.typicode.com/todos/1",
                    },
                },
                {
                    "id": "x",
                    "type": "extract",
                    "position": {"x": 200, "y": 0},
                    "data": {
                        "label": "X",
                        "jsonSchema": {
                            "title": "Todo",
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "completed": {"type": "boolean"},
                            },
                            "required": ["title", "completed"],
                        },
                    },
                },
                {
                    "id": "ss",
                    "type": "set-state",
                    "position": {"x": 300, "y": 0},
                    "data": {
                        "label": "SS",
                        "stateKey": "todoSummary",
                        "stateValue": "{{lastOutput.title}}",
                    },
                },
                {
                    "id": "e",
                    "type": "end",
                    "position": {"x": 400, "y": 0},
                    "data": {"label": "E"},
                },
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "h"},
                {"id": "e2", "source": "h", "target": "x"},
                {"id": "e3", "source": "x", "target": "ss"},
                {"id": "e4", "source": "ss", "target": "e"},
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
    summary = variables.get("todoSummary")
    assert isinstance(summary, str) and len(summary) > 0
