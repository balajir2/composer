"""Integration — guardrails flags PII via real Anthropic + Neon.

Uses Claude Haiku (cheap + fast).  Workflow: start → set-state(lastOutput=
PII string) → guardrails(piiEnabled=true, action=warn) → end.  Asserts
execution completes and _guardrails_result shows the PII violation.
"""

import asyncio
from typing import Any

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


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


async def test_guardrails_flags_pii_real_llm(client: AsyncClient) -> None:
    wf = await client.post(
        "/workflows",
        json={
            "name": "Phase 6b guardrails integration (PII)",
            "nodes": [
                {
                    "id": "s",
                    "type": "start",
                    "position": {"x": 0, "y": 0},
                    "data": {"label": "S"},
                },
                {
                    "id": "ss",
                    "type": "set-state",
                    "position": {"x": 100, "y": 0},
                    "data": {
                        "label": "SS",
                        "stateKey": "lastOutput",
                        "stateValue": (
                            "Here is my info: my name is Alice Johnson, "
                            "email alice.johnson@example.com, "
                            "phone 555-123-4567."
                        ),
                    },
                },
                {
                    "id": "gr",
                    "type": "guardrails",
                    "position": {"x": 200, "y": 0},
                    "data": {
                        "label": "GR",
                        "piiEnabled": True,
                        "actionOnViolation": "warn",
                        "model": "anthropic/claude-haiku-4-5-20251001",
                    },
                },
                {
                    "id": "e",
                    "type": "end",
                    "position": {"x": 300, "y": 0},
                    "data": {"label": "E"},
                },
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "ss"},
                {"id": "e2", "source": "ss", "target": "gr"},
                {"id": "e3", "source": "gr", "target": "e"},
            ],
        },
    )
    assert wf.status_code == 201, wf.text
    start = await client.post("/executions", json={"workflowId": wf.json()["id"], "input": ""})
    execution_id = start.json()["id"]

    done = await _poll_until_status(client, execution_id, {"completed", "failed"})
    assert done["status"] == "completed", f"Got: {done}"

    variables: dict[str, Any] = done.get("variables") or {}
    assert isinstance(variables, dict)
    result = variables.get("_guardrails_result")
    assert isinstance(result, dict), f"_guardrails_result missing: {variables}"
    assert result["passed"] is False, f"Expected PII violation; got: {result}"
    assert "PII detected" in result["violations"]
    assert result["checks_run"] == ["pii"]
