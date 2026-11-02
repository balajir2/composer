"""Tests for the builder agent loop and BuildSession.

Uses a scripted fake model (returns a canned AIMessage via ainvoke; bind_tools
is a no-op returning self) so the deterministic branches — plan, approve,
reject, diagnose — can be exercised without a provider-backed tool-call round
trip.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from langchain_core.messages import AIMessage

from src.builder.builder_agent import BuildSession, run_builder_turn
from src.engine.workflow import (
    EndNode,
    EndNodeData,
    Position,
    StartNode,
    StartNodeData,
    Workflow,
    WorkflowEdge,
    WorkflowNode,
)

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel


class _ScriptedModel:
    """Scripted fake chat model: returns a canned AIMessage via ainvoke."""

    def __init__(self, text: str) -> None:
        self._text = text

    async def ainvoke(self, messages: Any, **kwargs: Any) -> AIMessage:
        return AIMessage(content=self._text)

    def bind_tools(self, tools: Any) -> _ScriptedModel:
        return self


def _model(text: str) -> BaseChatModel:
    return cast("BaseChatModel", _ScriptedModel(text))


def _empty_workflow() -> Workflow:
    nodes: list[WorkflowNode] = [
        StartNode(
            id="s", type="start", position=Position(x=0, y=0), data=StartNodeData(label="S")
        ),
        EndNode(
            id="e", type="end", position=Position(x=400, y=0), data=EndNodeData(label="E")
        ),
    ]
    edges: list[WorkflowEdge] = [WorkflowEdge(id="e1", source="s", target="e")]
    return Workflow(name="t", nodes=nodes, edges=edges)


async def test_planning_turn_emits_plan_event() -> None:
    session = BuildSession(workflow_id="w1")
    events, _reply, wf = await run_builder_turn(
        model=_model('{"stages":[{"id":"s1","summary":"ingest"}]}'),
        workflow_id="w1",
        workflow=_empty_workflow(),
        session=session,
        message="build me a RAG pipeline",
        execution_id=None,
        db=None,
    )
    assert any(e.get("type") == "plan" for e in events)
    assert session.pending_plan is not None
    assert wf is not None


async def test_approve_patch_applies_pending_patch() -> None:
    session = BuildSession(workflow_id="w1")
    session.pending_patch = {
        "nodes": [n.model_dump(by_alias=True) for n in _empty_workflow().nodes],
        "edges": [e.model_dump(by_alias=True) for e in _empty_workflow().edges],
    }
    events, _reply, wf = await run_builder_turn(
        model=_model(""),
        workflow_id="w1",
        workflow=_empty_workflow(),
        session=session,
        message="__APPROVE_PATCH__",
        execution_id=None,
        db=None,
    )
    assert any(e.get("type") == "stage_applied" for e in events)
    assert session.pending_patch is None
    assert len(wf.nodes) == 2


async def test_reject_patch_discards_pending_patch() -> None:
    session = BuildSession(workflow_id="w1")
    session.pending_patch = {"nodes": [], "edges": []}
    _events, _reply, wf = await run_builder_turn(
        model=_model(""),
        workflow_id="w1",
        workflow=_empty_workflow(),
        session=session,
        message="__REJECT_PATCH__",
        execution_id=None,
        db=None,
    )
    assert session.pending_patch is None
    assert len(wf.nodes) == 2


async def test_diagnose_turn_proposes_without_applying() -> None:
    session = BuildSession(workflow_id="w1")
    session.pending_plan = {"stages": []}  # skip planning branch
    events, _reply, wf = await run_builder_turn(
        model=_model("The http node had a bad URL."),
        workflow_id="w1",
        workflow=_empty_workflow(),
        session=session,
        message="why did it fail?",
        execution_id="x",
        db=None,
    )
    assert any(e.get("type") == "patch_proposal" for e in events)
    assert session.pending_patch is not None
    assert wf is not None
