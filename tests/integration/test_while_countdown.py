"""Integration — Start → Set-State(n=3) → While(n>0) body: Transform(n-1) → Set-State(n) → loop → End.

Real Neon; no LLM.  Verifies:
  - Conditional edge from while loops back to itself via the body nodes
  - Iteration counter increments as expected
  - When condition becomes false, exit branch is taken and workflow completes
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


async def test_while_countdown_from_three(client: AsyncClient) -> None:
    wf = await client.post(
        "/workflows",
        json={
            "name": "Phase 4b while countdown",
            "nodes": [
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                {
                    "id": "init",
                    "type": "set-state",
                    "position": {"x": 100, "y": 0},
                    "data": {"label": "init", "stateKey": "n", "stateValue": 3},
                },
                {
                    "id": "w",
                    "type": "while",
                    "position": {"x": 200, "y": 0},
                    "data": {"label": "W", "condition": "int(variables['n']) > 0"},
                },
                {
                    "id": "dec",
                    "type": "transform",
                    "position": {"x": 300, "y": 0},
                    "data": {"label": "Decrement", "transformScript": "int(variables['n']) - 1"},
                },
                {
                    "id": "write",
                    "type": "set-state",
                    "position": {"x": 400, "y": 0},
                    "data": {"label": "Write n", "stateKey": "n", "stateValue": "{{lastOutput}}"},
                },
                {"id": "e", "type": "end", "position": {"x": 500, "y": 0}, "data": {"label": "E"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "init"},
                {"id": "e2", "source": "init", "target": "w"},
                {"id": "e3", "source": "w", "target": "dec", "branch": "body"},
                {"id": "e4", "source": "w", "target": "e", "branch": "exit"},
                {"id": "e5", "source": "dec", "target": "write"},
                {"id": "e6", "source": "write", "target": "w"},  # loop back
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
    # After countdown, n should be 0 (may be int or str depending on substitution)
    n = variables.get("n")
    assert n == 0 or n == "0", f"Expected n=0 after countdown, got {n!r}"
    # Iteration counter: 3 body traversals + 1 exit check = 4
    iter_counts = cast("dict[str, Any]", variables.get("_while_iterations") or {})
    assert isinstance(iter_counts, dict)
    assert iter_counts.get("w") == 4, f"Expected 4 while iterations, got {iter_counts}"
