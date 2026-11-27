"""Integration -- a Join that cannot wait correctly for its parallel paths.

Same product rule as a missing branch ("warn on save, block run"): a canvas
with a J1/J2/J3 Join SAVES (with a warning naming the offending step), while
running, publishing and the external invoke are refused with the user-facing
message and no execution row exists.
"""

import secrets
from typing import Any

import pytest
from httpx import AsyncClient

from src.db.models import WorkflowExecution
from tests.integration._rows import Rows

pytestmark = pytest.mark.integration


def _node(node_id: str, kind: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": kind,
        "position": {"x": 0, "y": 0},
        "data": {"label": node_id, **(data or {})},
    }


def _set(node_id: str) -> dict[str, Any]:
    return _node(node_id, "set-state", {"stateKey": f"ran_{node_id}", "stateValue": "yes"})


def _edge(source: str, target: str, branch: str | None = None) -> dict[str, Any]:
    edge: dict[str, Any] = {"id": f"{source}-{target}-{branch}", "source": source, "target": target}
    if branch is not None:
        edge["branch"] = branch
    return edge


def _wf(name: str, nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> dict[str, Any]:
    return {"name": name, "nodes": nodes, "edges": edges}


_START, _END, _JOIN = _node("s", "start"), _node("end", "end"), _node("j", "join")
_IF = _node("c", "if-else", {"condition": "True"})

# J1: a and b run in parallel, d runs instead of them.
J1 = _wf(
    "j1",
    [_START, _IF, _set("f"), _set("a"), _set("b"), _set("d"), _JOIN, _END],
    [
        _edge("s", "c"),
        _edge("c", "f", "true"),
        _edge("f", "a"),
        _edge("f", "b"),
        _edge("a", "j"),
        _edge("b", "j"),
        _edge("c", "d", "false"),
        _edge("d", "j"),
        _edge("j", "end"),
    ],
)
# J2: the "false" path of c ends at end2 without reaching the Join.
J2 = _wf(
    "j2",
    [_START, _set("p"), _set("a"), _IF, _set("b"), _JOIN, _END, _node("end2", "end")],
    [
        _edge("s", "p"),
        _edge("p", "a"),
        _edge("a", "c"),
        _edge("c", "j", "true"),
        _edge("c", "end2", "false"),
        _edge("p", "b"),
        _edge("b", "j"),
        _edge("j", "end"),
    ],
)
# J3: a and b both reach m before the Join.
J3 = _wf(
    "j3",
    [_START, _set("p"), _set("a"), _set("b"), _set("m"), _set("c"), _JOIN, _END],
    [
        _edge("s", "p"),
        _edge("p", "a"),
        _edge("p", "b"),
        _edge("p", "c"),
        _edge("a", "m"),
        _edge("b", "m"),
        _edge("m", "j"),
        _edge("c", "j"),
        _edge("j", "end"),
    ],
)
OK = _wf(
    "ok",
    [_START, _set("p"), _set("a"), _set("a2"), _set("b"), _JOIN, _END],
    [
        _edge("s", "p"),
        _edge("p", "a"),
        _edge("a", "a2"),
        _edge("a2", "j"),
        _edge("p", "b"),
        _edge("b", "j"),
        _edge("j", "end"),
    ],
)

CASES = {
    "j1": (J1, "join_mixed_inputs", "j", "mixes parallel paths with an alternative path"),
    "j2": (J2, "join_lane_escapes", "j", 'can finish at "end2" without reaching it'),
    "j3": (J3, "join_collision", "m", "is reached by more than one parallel path"),
}


@pytest.mark.parametrize("case", list(CASES))
async def test_join_problem_saves_with_a_warning(client: AsyncClient, case: str) -> None:
    body, code, node_id, fragment = CASES[case]
    created = await client.post("/workflows", json=body)
    assert created.status_code == 201, created.text
    wf_id = created.json()["id"]
    for response in (
        created,
        await client.put(f"/workflows/{wf_id}", json=body),
        await client.get(f"/workflows/{wf_id}"),
    ):
        assert response.status_code in (200, 201), response.text
        (warning,) = response.json()["warnings"]
        assert (warning["code"], warning["nodeId"], warning["branch"]) == (code, node_id, None)
        assert fragment in warning["message"]


async def test_a_sound_join_saves_without_warnings_and_runs(client: AsyncClient) -> None:
    created = await client.post("/workflows", json=OK)
    assert created.status_code == 201, created.text
    assert created.json()["warnings"] == []
    started = await client.post(
        "/executions", json={"workflowId": created.json()["id"], "input": ""}
    )
    assert started.status_code == 202, started.text


@pytest.mark.parametrize("case", list(CASES))
async def test_run_is_refused_before_any_execution_row_exists(
    client: AsyncClient, rows: Rows, case: str
) -> None:
    body, _code, _node_id, fragment = CASES[case]
    wf_id = (await client.post("/workflows", json=body)).json()["id"]

    resp = await client.post("/executions", json={"workflowId": wf_id, "input": ""})

    assert resp.status_code == 422, resp.text
    assert fragment in resp.json()["detail"]
    assert await rows.count(WorkflowExecution, WorkflowExecution.workflow_id == wf_id) == 0


async def test_publish_is_refused_then_allowed_once_the_join_is_sound(
    client: AsyncClient,
) -> None:
    wf_id = (await client.post("/workflows", json=J2)).json()["id"]
    slug = f"jp-{secrets.token_hex(4)}"

    refused = await client.put(
        f"/workflows/{wf_id}", json={**J2, "isProduction": True, "externalSlug": slug}
    )
    assert refused.status_code == 422, refused.text
    assert "waits for every parallel path" in refused.json()["detail"]
    assert (await client.get(f"/workflows/{wf_id}")).json()["isProduction"] is False

    allowed = await client.put(
        f"/workflows/{wf_id}", json={**OK, "isProduction": True, "externalSlug": slug}
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["isProduction"] is True
