"""Editing a workflow while a run is paused mid-Join, on the real Postgres saver.

Resume compiles the LIVE workflow. The barrier's progress is saved under a channel named after
its lanes, so reordering edges must resume normally (the Join and End run once) and an edit that
changes the lanes must fail the run closed with ``checkpoint_incompatible``, never complete it.
"""

import json
from typing import Any

import pytest
from sqlalchemy import text

from tests.integration.sqla_phase4a.conftest import HTTPX_MARK, Db, Member, edge, node
from tests.integration.sqla_phase4b.conftest import resume
from tests.integration.sqla_phase5.conftest import (
    RunningApp,
    join_lane_graph,
    running_app,
    start_paused,
)

pytestmark = [pytest.mark.integration, HTTPX_MARK]


async def _edit_workflow(
    a: RunningApp, workflow_id: str, nodes: list[dict[str, Any]], edges: list[dict[str, Any]]
) -> None:
    async with a.sm() as s:
        await s.execute(
            text(
                "UPDATE workflows SET nodes = CAST(:n AS jsonb), edges = CAST(:e AS jsonb) "
                "WHERE id = :id"
            ),
            {"n": json.dumps(nodes), "e": json.dumps(edges), "id": workflow_id},
        )
        await s.commit()


async def _events(db: Db, eid: str) -> list[tuple[str, Any]]:
    return [(e.type, e.payload.get("nodeId")) for e in await db.events(eid)]


async def test_reordered_edges_resume_and_run_join_once(
    db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with running_app(monkeypatch) as a:
        eid, _thread = await start_paused(a.client, db, member, monkeypatch, graph=join_lane_graph)
        nodes, edges = join_lane_graph()
        await _edit_workflow(a, (await db.must(eid)).workflow_id, nodes, edges[::-1])
        assert (await resume(a.client, member, eid, "approved")).status_code == 200
        row = await db.must(eid)
        assert row.status == "completed", (row.status, row.error)
        assert row.variables["fin"] == "yes"
    events = await _events(db, eid)
    for node_id in ("j", "fin", "e"):
        assert events.count(("node_started", node_id)) == 1, (node_id, events)


@pytest.mark.parametrize("change", ["lane_added", "lane_removed"])
async def test_lane_change_while_paused_fails_closed(
    db: Db, member: Member, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    async with running_app(monkeypatch) as a:
        eid, _thread = await start_paused(a.client, db, member, monkeypatch, graph=join_lane_graph)
        nodes, edges = join_lane_graph()
        if change == "lane_added":
            nodes.append(node("c", "set-state", stateKey="c", stateValue="yes"))
            edges += [edge("p", "c"), edge("c", "j")]
        else:  # the parallel lane through b is deleted: the Join becomes a plain merge
            nodes = [n for n in nodes if n["id"] != "b"]
            edges = [e for e in edges if "b" not in (e["source"], e["target"])]
        await _edit_workflow(a, (await db.must(eid)).workflow_id, nodes, edges)
        assert (await resume(a.client, member, eid, "approved")).status_code == 200
        row = await db.must(eid)
        assert (row.status, row.failure_code) == ("failed", "checkpoint_incompatible"), row.error
        assert "new run" in (row.error or "").lower()
    events = await _events(db, eid)
    assert ("node_started", "j") not in events
    assert ("node_started", "fin") not in events
