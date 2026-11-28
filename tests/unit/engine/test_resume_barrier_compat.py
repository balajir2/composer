"""Resume recompiles the LIVE workflow. A paused run's Join barrier progress is stored under a
channel name derived from the lanes, so an edit that changes the lanes while the run is paused
cannot be carried over: the resume must fail closed (never run, never complete), while an
edit that only reorders edges resumes normally. Real graph and saver, no database."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from langgraph.checkpoint.memory import MemorySaver

from src.engine import langgraph_executor as lge_mod
from src.engine.graph_builder import build_graph
from src.engine.langgraph_executor import LangGraphExecutor
from src.engine.state import initial_state
from src.engine.workflow import Workflow
from tests.unit.engine.test_join_barrier import _ed, _n, _ss, _ua  # pyright: ignore[reportPrivateUsage]
from tests.unit.execution_fakes import FakeExecutions, make_row

S = _n("s", "start")
END = _n("end", "end")
J = _n("j", "join")

NODES = [S, _ss("p"), _ss("x1"), _ua("ua"), _ss("ok"), _ss("b"), J, END]
EDGES = [
    _ed("s", "p"),
    _ed("p", "x1"),
    _ed("x1", "ua"),
    _ed("ua", "ok", "approved"),
    _ed("ok", "j"),
    _ed("ua", "j", "rejected"),
    _ed("p", "b"),
    _ed("b", "j"),
    _ed("j", "end"),
]


async def _paused_then_resume(
    monkeypatch: pytest.MonkeyPatch, nodes: list[dict[str, Any]], edges: list[dict[str, Any]]
) -> tuple[FakeExecutions, MemorySaver]:
    """Pause the original canvas at its approval, then resume against `nodes`/`edges`."""
    saver = MemorySaver()
    original = Workflow.model_validate({"name": "T", "nodes": NODES, "edges": EDGES})
    graph = build_graph(original, saver)
    config: Any = {"configurable": {"thread_id": "t1"}}
    await graph.ainvoke(initial_state("x"), config)
    assert (await graph.aget_state(config)).next == ("ua",)

    fake = FakeExecutions(monkeypatch, make_row(workflow_id="wf1", user_id="dev", input=None))
    monkeypatch.setattr(
        "src.db.repos.workflows.get",
        AsyncMock(return_value=SimpleNamespace(id="wf1", name="n", nodes=nodes, edges=edges)),
    )
    monkeypatch.setattr(lge_mod, "notify_execution_event", AsyncMock())
    executor = LangGraphExecutor(sessionmaker=fake.sessionmaker, checkpointer=saver)
    await executor.resume("ex1", "approved", attempt_id="attempt-1")
    return fake, saver


async def test_reordered_edges_resume_and_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    fake, _ = await _paused_then_resume(monkeypatch, NODES, EDGES[::-1])
    assert [u["status"] for u in fake.updates] == ["completed"]
    assert fake.updates[0]["output"] is not None or fake.updates[0].get("node_results")


@pytest.mark.parametrize("change", ["edge_added", "edge_removed"])
async def test_lane_change_while_paused_fails_closed(
    monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    if change == "edge_added":
        nodes = [*NODES, _ss("extra")]
        edges = [*EDGES, _ed("p", "extra"), _ed("extra", "j")]
    else:
        # The parallel lane through b is deleted: the Join becomes a plain merge.
        nodes = [n for n in NODES if n["id"] != "b"]
        edges = [e for e in EDGES if "b" not in (e["source"], e["target"])]
    fake, _ = await _paused_then_resume(monkeypatch, nodes, edges)
    assert [u["status"] for u in fake.updates] == ["failed"]
    update = fake.updates[0]
    assert update["failure_code"] == "checkpoint_incompatible"
    assert update["attempt_id"] == "attempt-1"
    assert "start a new run" in update["error"].lower()
