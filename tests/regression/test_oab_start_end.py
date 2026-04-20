"""Regression: OAB workflow-execution.spec.ts — start→end workflow.

OAB source: D:/GitHub/open-agent-builder/tests/workflow-execution.spec.ts
  "Basic Workflow Flows" describe-block, specifically the
  'should detect and clean invalid edges' test which uses a start→end pair,
  and the broader suite pattern from lines 951-978.
Ported: 2026-04-20 for Phase 1 to establish the regression harness.

Deliberate deviations from OAB's assertions are annotated inline with a
reference to docs/superpowers/specs/2026-04-20-phase-1-execution-engine-design.md §7.1.
"""

import asyncio

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def _poll_until_terminal(
    client: AsyncClient, execution_id: str, timeout: float = 10.0
) -> dict[str, object]:
    """Poll GET /executions/{id} until status is completed or failed."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        resp = await client.get(f"/executions/{execution_id}")
        body: dict[str, object] = resp.json()
        if body["status"] in {"completed", "failed"}:
            return body
        await asyncio.sleep(0.1)
    raise AssertionError(f"Execution {execution_id} did not terminate within {timeout}s")


async def test_oab_start_end_regression(client: AsyncClient) -> None:
    """Minimal start→end workflow runs to completion.

    OAB (workflow-execution.spec.ts "Edge Validation" block, lines 951-978)
    validates that a start→end graph with valid edges runs to 'completed'
    status. OAB's LangGraphExecutor checks `result.status === 'completed'`
    and `result.nodeResults` is defined.

    Deliberate deviation (§7.1): In OAB, a direct start→end workflow with no
    intermediate node leaves `finalOutput` as the empty-string default because
    OAB's Start node does not write `variables.lastOutput`. Composer's Start
    executor explicitly sets `variables.lastOutput = parsed_input` (spec §7.1),
    so `output` here equals the parsed input rather than "".

    This test locks in the Composer-side behavior. The OAB-side expectation
    (empty finalOutput on direct start→end) is recorded in CHANGELOG as a
    known deviation.
    """
    wf_payload = {
        "name": "OAB Regression — start\u2192end",
        "nodes": [
            {
                "id": "s",
                "type": "start",
                "position": {"x": 0, "y": 0},
                "data": {"label": "Start"},
            },
            {
                "id": "e",
                "type": "end",
                "position": {"x": 100, "y": 0},
                "data": {"label": "End"},
            },
        ],
        "edges": [{"id": "e1", "source": "s", "target": "e"}],
    }
    create_resp = await client.post("/workflows", json=wf_payload)
    assert create_resp.status_code == 201, create_resp.text
    workflow_id: str = create_resp.json()["id"]

    start_resp = await client.post(
        "/executions",
        json={"workflowId": workflow_id, "input": "regression-input"},
    )
    assert start_resp.status_code == 202, start_resp.text
    execution_id: str = start_resp.json()["id"]

    final = await _poll_until_terminal(client, execution_id)
    assert final["status"] == "completed", f"Expected completed, got: {final}"
    # OAB would have emitted "" here; Composer emits the parsed input per §7.1.
    assert final["output"] == "regression-input"


async def test_oab_start_end_regression_node_results_defined(client: AsyncClient) -> None:
    """Companion assertion: nodeResults must be present in the execution record.

    OAB (workflow-execution.spec.ts line 252) checks
    `expect(result.nodeResults).toBeDefined()`. Composer's execution response
    includes a `nodeResults` key (list) that is non-null on completion.
    """
    wf_payload = {
        "name": "OAB Regression — nodeResults",
        "nodes": [
            {
                "id": "s",
                "type": "start",
                "position": {"x": 0, "y": 0},
                "data": {"label": "Start"},
            },
            {
                "id": "e",
                "type": "end",
                "position": {"x": 100, "y": 0},
                "data": {"label": "End"},
            },
        ],
        "edges": [{"id": "e1", "source": "s", "target": "e"}],
    }
    wf_id: str = (await client.post("/workflows", json=wf_payload)).json()["id"]

    exec_resp = await client.post(
        "/executions",
        json={"workflowId": wf_id, "input": "node-results-check"},
    )
    execution_id: str = exec_resp.json()["id"]

    final = await _poll_until_terminal(client, execution_id)
    assert final["status"] == "completed"
    # Mirrors OAB's `expect(result.nodeResults).toBeDefined()`
    assert "nodeResults" in final
    assert final["nodeResults"] is not None
