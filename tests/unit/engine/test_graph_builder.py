"""Tests for workflow validation and graph compilation in graph_builder."""

from typing import Any

import pytest
from langgraph.checkpoint.memory import MemorySaver

from src.engine.graph_builder import (
    WorkflowValidationError,
    build_graph,
    validate_workflow_shape,
)
from src.engine.workflow import Workflow


def _mk(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> Workflow:
    return Workflow.model_validate({"name": "T", "nodes": nodes, "edges": edges})


def test_missing_start_fails() -> None:
    wf = _mk(
        nodes=[{"id": "e", "type": "end", "position": {"x": 0, "y": 0}, "data": {"label": "E"}}],
        edges=[],
    )
    with pytest.raises(WorkflowValidationError, match=r"exactly one start node \(found: 0\)"):
        validate_workflow_shape(wf)


def test_multiple_start_fails() -> None:
    wf = _mk(
        nodes=[
            {"id": "s1", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {"id": "s2", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {"id": "e", "type": "end", "position": {"x": 0, "y": 0}, "data": {"label": "E"}},
        ],
        edges=[{"id": "e1", "source": "s1", "target": "e"}],
    )
    with pytest.raises(WorkflowValidationError, match=r"exactly one start node \(found: 2\)"):
        validate_workflow_shape(wf)


def test_missing_end_fails() -> None:
    wf = _mk(
        nodes=[{"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}}],
        edges=[],
    )
    with pytest.raises(WorkflowValidationError, match="at least one end node"):
        validate_workflow_shape(wf)


def test_duplicate_node_id_fails() -> None:
    wf = _mk(
        nodes=[
            {"id": "x", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {"id": "x", "type": "end", "position": {"x": 0, "y": 0}, "data": {"label": "E"}},
        ],
        edges=[],
    )
    with pytest.raises(WorkflowValidationError, match="duplicated"):
        validate_workflow_shape(wf)


def test_edge_unknown_source_fails() -> None:
    wf = _mk(
        nodes=[
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {"id": "e", "type": "end", "position": {"x": 0, "y": 0}, "data": {"label": "E"}},
        ],
        edges=[{"id": "e1", "source": "ghost", "target": "e"}],
    )
    with pytest.raises(WorkflowValidationError, match="unknown source node id 'ghost'"):
        validate_workflow_shape(wf)


def test_edge_unknown_target_fails() -> None:
    wf = _mk(
        nodes=[
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {"id": "e", "type": "end", "position": {"x": 0, "y": 0}, "data": {"label": "E"}},
        ],
        edges=[{"id": "e1", "source": "s", "target": "ghost"}],
    )
    with pytest.raises(WorkflowValidationError, match="unknown target node id 'ghost'"):
        validate_workflow_shape(wf)


def test_unreachable_node_fails() -> None:
    wf = _mk(
        nodes=[
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {"id": "e", "type": "end", "position": {"x": 0, "y": 0}, "data": {"label": "E"}},
            {"id": "orphan", "type": "agent", "position": {"x": 0, "y": 0}, "data": {"label": "A"}},
        ],
        edges=[{"id": "e1", "source": "s", "target": "e"}],
    )
    with pytest.raises(WorkflowValidationError, match="unreachable from the start node"):
        validate_workflow_shape(wf)


def test_note_node_unreachable_is_allowed() -> None:
    """Note nodes are visual-only; they can be disconnected."""
    wf = _mk(
        nodes=[
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {"id": "e", "type": "end", "position": {"x": 0, "y": 0}, "data": {"label": "E"}},
            {"id": "n", "type": "note", "position": {"x": 0, "y": 0}, "data": {"label": "memo"}},
        ],
        edges=[{"id": "e1", "source": "s", "target": "e"}],
    )
    # Should not raise.
    validate_workflow_shape(wf)


def test_node_reachable_only_through_note_is_rejected() -> None:
    """P0-8: compile-time (build_graph) drops every edge touching a
    visual-only node (note/file-trigger — see test_build_graph_skips_note_nodes
    below), so a node reachable ONLY via a path through a note is
    validated as reachable but is actually disconnected once compiled.
    Validation's reachability BFS must use the same edge projection as
    compilation, not raw workflow.edges."""
    wf = _mk(
        nodes=[
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {"id": "n", "type": "note", "position": {"x": 0, "y": 0}, "data": {"label": "memo"}},
            {"id": "a", "type": "agent", "position": {"x": 0, "y": 0}, "data": {"label": "A"}},
            {"id": "e", "type": "end", "position": {"x": 0, "y": 0}, "data": {"label": "E"}},
        ],
        edges=[
            {"id": "e1", "source": "s", "target": "n"},
            {"id": "e2", "source": "n", "target": "a"},
            {"id": "e3", "source": "a", "target": "e"},
        ],
    )
    with pytest.raises(WorkflowValidationError, match="unreachable from the start node"):
        validate_workflow_shape(wf)


def test_note_node_can_still_sit_between_reachable_nodes_visually() -> None:
    """A note dangling off a reachable path is fine (still visual-only,
    still allowed disconnected) as long as the real nodes have their own
    real-to-real path — this must keep passing after the P0-8 fix."""
    wf = _mk(
        nodes=[
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {"id": "n", "type": "note", "position": {"x": 0, "y": 0}, "data": {"label": "memo"}},
            {"id": "e", "type": "end", "position": {"x": 0, "y": 0}, "data": {"label": "E"}},
        ],
        edges=[
            {"id": "e1", "source": "s", "target": "e"},
            {"id": "e2", "source": "s", "target": "n"},
        ],
    )
    validate_workflow_shape(wf)  # no raise


def test_valid_start_to_end_passes() -> None:
    wf = _mk(
        nodes=[
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {"id": "e", "type": "end", "position": {"x": 0, "y": 0}, "data": {"label": "E"}},
        ],
        edges=[{"id": "e1", "source": "s", "target": "e"}],
    )
    validate_workflow_shape(wf)  # no raise


# ---------------------------------------------------------------------------
# Task 9: build_graph tests
# ---------------------------------------------------------------------------


def _minimal() -> Workflow:
    return _mk(
        nodes=[
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {"id": "e", "type": "end", "position": {"x": 100, "y": 0}, "data": {"label": "E"}},
        ],
        edges=[{"id": "e1", "source": "s", "target": "e"}],
    )


def test_build_graph_accepts_duplicate_edges() -> None:
    wf = _mk(
        nodes=[
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {"id": "e", "type": "end", "position": {"x": 100, "y": 0}, "data": {"label": "E"}},
        ],
        edges=[
            {"id": "e1", "source": "s", "target": "e"},
            {"id": "e2", "source": "s", "target": "e"},
        ],
    )
    build_graph(wf, MemorySaver())


def test_build_graph_compiles_start_to_end() -> None:
    compiled = build_graph(_minimal(), MemorySaver())
    # CompiledStateGraph exposes .get_graph() which we can use to inspect structure.
    g = compiled.get_graph()
    names = {n.id for n in g.nodes.values()}
    # LangGraph adds synthetic __start__ / __end__ in addition to our nodes
    assert "s" in names
    assert "e" in names


def test_build_graph_skips_note_nodes() -> None:
    wf = _mk(
        nodes=[
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {"id": "note", "type": "note", "position": {"x": 0, "y": 0}, "data": {"label": "memo"}},
            {"id": "e", "type": "end", "position": {"x": 0, "y": 0}, "data": {"label": "E"}},
        ],
        edges=[{"id": "e1", "source": "s", "target": "e"}],
    )
    compiled = build_graph(wf, MemorySaver())
    g = compiled.get_graph()
    names = {n.id for n in g.nodes.values()}
    assert "note" not in names  # skipped at build time
    assert "s" in names
    assert "e" in names


async def test_build_graph_compiles_with_agent_node(monkeypatch: pytest.MonkeyPatch) -> None:
    """After Task 9, build_graph should accept an Agent node without raising."""
    from typing import Any as AnyType

    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    import src.llm.providers as providers

    fake = FakeListChatModel(responses=["ok"])

    def mock_build_chat_model(*args: AnyType, **kwargs: AnyType) -> FakeListChatModel:  # type: ignore[name-defined]
        return fake

    monkeypatch.setattr(providers, "build_chat_model", mock_build_chat_model)

    wf = _mk(
        nodes=[
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {
                "id": "a",
                "type": "agent",
                "position": {"x": 100, "y": 0},
                "data": {"label": "Agent"},
            },
            {"id": "e", "type": "end", "position": {"x": 200, "y": 0}, "data": {"label": "E"}},
        ],
        edges=[
            {"id": "e1", "source": "s", "target": "a"},
            {"id": "e2", "source": "a", "target": "e"},
        ],
    )
    compiled = build_graph(wf, MemorySaver())
    g = compiled.get_graph()
    names = {n.id for n in g.nodes.values()}
    assert {"s", "a", "e"}.issubset(names)


# ---------------------------------------------------------------------------
# Task 4 (Phase 4b): conditional-edge emission + branch validation
# ---------------------------------------------------------------------------


def test_conditional_edges_compile_for_if_else() -> None:
    from langgraph.checkpoint.memory import MemorySaver

    from src.engine.graph_builder import build_graph
    from src.engine.workflow import Workflow

    wf = Workflow.model_validate(
        {
            "id": "w1",
            "name": "if-else test",
            "nodes": [
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                {
                    "id": "ie",
                    "type": "if-else",
                    "position": {"x": 100, "y": 0},
                    "data": {"label": "IE", "condition": "variables['x'] > 0"},
                },
                {"id": "a", "type": "end", "position": {"x": 200, "y": 0}, "data": {"label": "A"}},
                {
                    "id": "b",
                    "type": "end",
                    "position": {"x": 200, "y": 100},
                    "data": {"label": "B"},
                },
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "ie"},
                {"id": "e2", "source": "ie", "target": "a", "branch": "true"},
                {"id": "e3", "source": "ie", "target": "b", "branch": "false"},
            ],
        }
    )
    compiled = build_graph(wf, MemorySaver())
    assert compiled is not None  # graph compiled successfully


def test_conditional_edges_compile_for_while() -> None:
    from langgraph.checkpoint.memory import MemorySaver

    from src.engine.graph_builder import build_graph
    from src.engine.workflow import Workflow

    wf = Workflow.model_validate(
        {
            "id": "w1",
            "name": "while test",
            "nodes": [
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                {
                    "id": "w",
                    "type": "while",
                    "position": {"x": 100, "y": 0},
                    "data": {"label": "W", "condition": "variables['n'] > 0"},
                },
                {
                    "id": "body",
                    "type": "set-state",
                    "position": {"x": 200, "y": 0},
                    "data": {"label": "body", "stateKey": "n", "stateValue": 0},
                },
                {"id": "e", "type": "end", "position": {"x": 300, "y": 0}, "data": {"label": "E"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "w"},
                {"id": "e2", "source": "w", "target": "body", "branch": "body"},
                {"id": "e3", "source": "w", "target": "e", "branch": "exit"},
                {"id": "e4", "source": "body", "target": "w"},  # loop-back, normal edge
            ],
        }
    )
    compiled = build_graph(wf, MemorySaver())
    assert compiled is not None


def test_conditional_source_missing_branch_raises() -> None:
    from langgraph.checkpoint.memory import MemorySaver

    from src.engine.graph_builder import WorkflowValidationError, build_graph
    from src.engine.workflow import Workflow

    wf = Workflow.model_validate(
        {
            "id": "w1",
            "name": "bad",
            "nodes": [
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                {
                    "id": "ie",
                    "type": "if-else",
                    "position": {"x": 100, "y": 0},
                    "data": {"label": "IE", "condition": "True"},
                },
                {"id": "a", "type": "end", "position": {"x": 200, "y": 0}, "data": {"label": "A"}},
                {
                    "id": "b",
                    "type": "end",
                    "position": {"x": 200, "y": 100},
                    "data": {"label": "B"},
                },
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "ie"},
                {"id": "e2", "source": "ie", "target": "a"},  # no branch
                {"id": "e3", "source": "ie", "target": "b", "branch": "false"},
            ],
        }
    )
    with pytest.raises(WorkflowValidationError, match="branch"):
        build_graph(wf, MemorySaver())


def test_normal_source_with_branch_raises() -> None:
    from langgraph.checkpoint.memory import MemorySaver

    from src.engine.graph_builder import WorkflowValidationError, build_graph
    from src.engine.workflow import Workflow

    wf = Workflow.model_validate(
        {
            "id": "w1",
            "name": "bad-normal-branch",
            "nodes": [
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                {"id": "e", "type": "end", "position": {"x": 100, "y": 0}, "data": {"label": "E"}},
            ],
            "edges": [
                # Start is a normal source -> branch should be None
                {"id": "e1", "source": "s", "target": "e", "branch": "true"},
            ],
        }
    )
    with pytest.raises(WorkflowValidationError, match="branch"):
        build_graph(wf, MemorySaver())


def test_conditional_source_wrong_branch_name_raises() -> None:
    from langgraph.checkpoint.memory import MemorySaver

    from src.engine.graph_builder import WorkflowValidationError, build_graph
    from src.engine.workflow import Workflow

    wf = Workflow.model_validate(
        {
            "id": "w1",
            "name": "bad-branch-name",
            "nodes": [
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                {
                    "id": "ie",
                    "type": "if-else",
                    "position": {"x": 100, "y": 0},
                    "data": {"label": "IE", "condition": "True"},
                },
                {"id": "a", "type": "end", "position": {"x": 200, "y": 0}, "data": {"label": "A"}},
                {
                    "id": "b",
                    "type": "end",
                    "position": {"x": 200, "y": 100},
                    "data": {"label": "B"},
                },
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "ie"},
                {"id": "e2", "source": "ie", "target": "a", "branch": "yes"},  # not "true"/"false"
                {"id": "e3", "source": "ie", "target": "b", "branch": "false"},
            ],
        }
    )
    with pytest.raises(WorkflowValidationError, match="yes"):
        build_graph(wf, MemorySaver())


def test_conditional_source_duplicate_branch_raises() -> None:
    from langgraph.checkpoint.memory import MemorySaver

    from src.engine.graph_builder import WorkflowValidationError, build_graph
    from src.engine.workflow import Workflow

    wf = Workflow.model_validate(
        {
            "id": "w1",
            "name": "dup-branch",
            "nodes": [
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                {
                    "id": "ie",
                    "type": "if-else",
                    "position": {"x": 100, "y": 0},
                    "data": {"label": "IE", "condition": "True"},
                },
                {"id": "a", "type": "end", "position": {"x": 200, "y": 0}, "data": {"label": "A"}},
                {
                    "id": "b",
                    "type": "end",
                    "position": {"x": 200, "y": 100},
                    "data": {"label": "B"},
                },
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "ie"},
                {"id": "e2", "source": "ie", "target": "a", "branch": "true"},
                {"id": "e3", "source": "ie", "target": "b", "branch": "true"},  # duplicate
            ],
        }
    )
    with pytest.raises(WorkflowValidationError, match=r"[Dd]uplicate"):
        build_graph(wf, MemorySaver())


# ---------------------------------------------------------------------------
# Task 3 (Phase 5a): user-approval conditional-edge emission
# ---------------------------------------------------------------------------


def test_conditional_edges_compile_for_user_approval() -> None:
    from langgraph.checkpoint.memory import MemorySaver

    from src.engine.graph_builder import build_graph
    from src.engine.workflow import Workflow

    wf = Workflow.model_validate(
        {
            "id": "w1",
            "name": "user-approval test",
            "nodes": [
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                {
                    "id": "ua",
                    "type": "user-approval",
                    "position": {"x": 100, "y": 0},
                    "data": {"label": "UA", "approvalMessage": "Please approve"},
                },
                {"id": "a", "type": "end", "position": {"x": 200, "y": 0}, "data": {"label": "A"}},
                {
                    "id": "b",
                    "type": "end",
                    "position": {"x": 200, "y": 100},
                    "data": {"label": "B"},
                },
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "ua"},
                {"id": "e2", "source": "ua", "target": "a", "branch": "approved"},
                {"id": "e3", "source": "ua", "target": "b", "branch": "rejected"},
            ],
        }
    )
    compiled = build_graph(wf, MemorySaver())
    assert compiled is not None


def test_user_approval_missing_approved_branch_raises() -> None:
    from langgraph.checkpoint.memory import MemorySaver

    from src.engine.graph_builder import WorkflowValidationError, build_graph
    from src.engine.workflow import Workflow

    wf = Workflow.model_validate(
        {
            "id": "w1",
            "name": "bad user-approval",
            "nodes": [
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                {
                    "id": "ua",
                    "type": "user-approval",
                    "position": {"x": 100, "y": 0},
                    "data": {"label": "UA", "approvalMessage": "Please approve"},
                },
                {"id": "b", "type": "end", "position": {"x": 200, "y": 0}, "data": {"label": "B"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "ua"},
                {"id": "e2", "source": "ua", "target": "b", "branch": "rejected"},
            ],
        }
    )
    with pytest.raises(WorkflowValidationError, match="approved"):
        build_graph(wf, MemorySaver())


def test_user_approval_wrong_branch_name_raises() -> None:
    from langgraph.checkpoint.memory import MemorySaver

    from src.engine.graph_builder import WorkflowValidationError, build_graph
    from src.engine.workflow import Workflow

    wf = Workflow.model_validate(
        {
            "id": "w1",
            "name": "bad branch name",
            "nodes": [
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                {
                    "id": "ua",
                    "type": "user-approval",
                    "position": {"x": 100, "y": 0},
                    "data": {"label": "UA", "approvalMessage": "Approve?"},
                },
                {"id": "a", "type": "end", "position": {"x": 200, "y": 0}, "data": {"label": "A"}},
                {
                    "id": "b",
                    "type": "end",
                    "position": {"x": 200, "y": 100},
                    "data": {"label": "B"},
                },
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "ua"},
                {"id": "e2", "source": "ua", "target": "a", "branch": "yes"},
                {"id": "e3", "source": "ua", "target": "b", "branch": "rejected"},
            ],
        }
    )
    with pytest.raises(WorkflowValidationError, match="yes"):
        build_graph(wf, MemorySaver())


def test_file_trigger_node_is_visual_only_like_note() -> None:
    """file-trigger is skipped at build time and allowed disconnected from
    start, exactly like note — the real work happens in the `composer watch`
    CLI, not the execution graph."""
    from src.engine.graph_builder import validate_workflow_shape
    from src.engine.workflow import Workflow

    wf = Workflow.model_validate(
        {
            "id": "w",
            "name": "t",
            "nodes": [
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                {"id": "e", "type": "end", "position": {"x": 1, "y": 0}, "data": {"label": "E"}},
                {
                    "id": "ft1",
                    "type": "file-trigger",
                    "position": {"x": -1, "y": 0},
                    "data": {"label": "File Trigger"},
                },
            ],
            "edges": [{"id": "e1", "source": "s", "target": "e"}],
        }
    )
    validate_workflow_shape(wf)  # must not raise despite ft1 being disconnected


async def test_build_graph_skips_file_trigger_node() -> None:
    from langgraph.checkpoint.memory import MemorySaver

    from src.engine.graph_builder import build_graph
    from src.engine.state import initial_state
    from src.engine.workflow import Workflow

    wf = Workflow.model_validate(
        {
            "id": "w",
            "name": "t",
            "nodes": [
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                {"id": "e", "type": "end", "position": {"x": 1, "y": 0}, "data": {"label": "E"}},
                {
                    "id": "ft1",
                    "type": "file-trigger",
                    "position": {"x": -1, "y": 0},
                    "data": {"label": "File Trigger"},
                },
            ],
            "edges": [{"id": "e1", "source": "s", "target": "e"}],
        }
    )
    compiled = build_graph(wf, MemorySaver())
    result = await compiled.ainvoke(  # pyright: ignore[reportUnknownMemberType]
        initial_state(), config={"configurable": {"thread_id": "t1"}}
    )
    assert "ft1" not in (result.get("node_results") or {})


def test_end_with_zero_incoming_edges_fails() -> None:
    wf = _mk(
        nodes=[
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {"id": "e1", "type": "end", "position": {"x": 1, "y": 0}, "data": {"label": "E1"}},
            {"id": "e2", "type": "end", "position": {"x": 2, "y": 0}, "data": {"label": "E2"}},
        ],
        edges=[{"id": "edge1", "source": "s", "target": "e1"}],
    )
    with pytest.raises(
        WorkflowValidationError, match=r"End node 'e2' must have exactly one incoming edge"
    ):
        validate_workflow_shape(wf)


def test_end_with_two_incoming_edges_fails() -> None:
    wf = _mk(
        nodes=[
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {
                "id": "a",
                "type": "set-state",
                "position": {"x": 1, "y": 0},
                "data": {"label": "A", "stateKey": "x", "stateValue": "1"},
            },
            {
                "id": "b",
                "type": "set-state",
                "position": {"x": 1, "y": 1},
                "data": {"label": "B", "stateKey": "y", "stateValue": "2"},
            },
            {"id": "e", "type": "end", "position": {"x": 2, "y": 0}, "data": {"label": "E"}},
        ],
        edges=[
            {"id": "e1", "source": "s", "target": "a"},
            {"id": "e2", "source": "s", "target": "b"},
            {"id": "e3", "source": "a", "target": "e"},
            {"id": "e4", "source": "b", "target": "e"},
        ],
    )
    with pytest.raises(
        WorkflowValidationError, match=r"End node 'e' must have exactly one incoming edge"
    ):
        validate_workflow_shape(wf)


def test_end_note_edge_does_not_count_toward_incoming_tally() -> None:
    wf = _mk(
        nodes=[
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {"id": "e", "type": "end", "position": {"x": 1, "y": 0}, "data": {"label": "E"}},
            {"id": "n", "type": "note", "position": {"x": -1, "y": 0}, "data": {"label": "N"}},
        ],
        edges=[
            {"id": "e1", "source": "s", "target": "e"},
            {"id": "e2", "source": "n", "target": "e"},
        ],
    )
    validate_workflow_shape(wf)  # must not raise -- the Note edge is decoration, not counted


def test_end_reached_by_both_if_else_branches_is_allowed() -> None:
    """Regression test: an if-else's true/false branches reconverging on one
    shared End (each through its own set-state node) is a completely
    standard, previously-working topology -- CI caught this validation
    incorrectly rejecting it after the fan-out/fan-in fix (found via
    tests/integration/test_if_else_routing.py failing with a false-positive
    "must have exactly one incoming edge" error). Only one of the two
    branches ever executes per run (LangGraph's add_conditional_edges),
    so this is safe without a join node -- unlike
    test_end_with_two_incoming_edges_fails's genuine unconditional fan-out.
    """
    wf = _mk(
        nodes=[
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {
                "id": "ie",
                "type": "if-else",
                "position": {"x": 1, "y": 0},
                "data": {"label": "IE", "condition": "variables['x'] > 0"},
            },
            {
                "id": "ok",
                "type": "set-state",
                "position": {"x": 2, "y": 0},
                "data": {"label": "OK", "stateKey": "result", "stateValue": "true"},
            },
            {
                "id": "no",
                "type": "set-state",
                "position": {"x": 2, "y": 1},
                "data": {"label": "NO", "stateKey": "result", "stateValue": "false"},
            },
            {"id": "e", "type": "end", "position": {"x": 3, "y": 0}, "data": {"label": "E"}},
        ],
        edges=[
            {"id": "e1", "source": "s", "target": "ie"},
            {"id": "e2", "source": "ie", "target": "ok", "branch": "true"},
            {"id": "e3", "source": "ie", "target": "no", "branch": "false"},
            {"id": "e4", "source": "ok", "target": "e"},
            {"id": "e5", "source": "no", "target": "e"},
        ],
    )
    validate_workflow_shape(wf)  # must not raise


def test_end_reached_by_both_user_approval_branches_is_allowed() -> None:
    """Same regression as test_end_reached_by_both_if_else_branches_is_allowed,
    for user-approval's approved/rejected branches (the exact topology
    tests/integration/test_user_approval_approved.py and
    test_user_approval_rejected.py use)."""
    wf = _mk(
        nodes=[
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {
                "id": "ua",
                "type": "user-approval",
                "position": {"x": 1, "y": 0},
                "data": {"label": "UA", "approvalMessage": "Please approve"},
            },
            {
                "id": "ok",
                "type": "set-state",
                "position": {"x": 2, "y": 0},
                "data": {"label": "OK", "stateKey": "result", "stateValue": "approved"},
            },
            {
                "id": "no",
                "type": "set-state",
                "position": {"x": 2, "y": 1},
                "data": {"label": "NO", "stateKey": "result", "stateValue": "rejected"},
            },
            {"id": "e", "type": "end", "position": {"x": 3, "y": 0}, "data": {"label": "E"}},
        ],
        edges=[
            {"id": "e1", "source": "s", "target": "ua"},
            {"id": "e2", "source": "ua", "target": "ok", "branch": "approved"},
            {"id": "e3", "source": "ua", "target": "no", "branch": "rejected"},
            {"id": "e4", "source": "ok", "target": "e"},
            {"id": "e5", "source": "no", "target": "e"},
        ],
    )
    validate_workflow_shape(wf)  # must not raise


def test_end_reached_by_unconditional_fan_out_through_conditional_lookalikes_still_fails() -> None:
    """Two branches that both descend from an if-else's SAME branch (not
    different branches) are NOT mutually exclusive and must still be
    rejected -- guards against a too-permissive fix that treats any
    if-else-adjacent topology as automatically safe."""
    wf = _mk(
        nodes=[
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {
                "id": "ie",
                "type": "if-else",
                "position": {"x": 1, "y": 0},
                "data": {"label": "IE", "condition": "variables['x'] > 0"},
            },
            {
                "id": "a",
                "type": "set-state",
                "position": {"x": 2, "y": 0},
                "data": {"label": "A", "stateKey": "x", "stateValue": "1"},
            },
            {
                "id": "b",
                "type": "set-state",
                "position": {"x": 2, "y": 1},
                "data": {"label": "B", "stateKey": "y", "stateValue": "2"},
            },
            {
                "id": "no",
                "type": "set-state",
                "position": {"x": 2, "y": 2},
                "data": {"label": "NO", "stateKey": "result", "stateValue": "false"},
            },
            {"id": "e", "type": "end", "position": {"x": 3, "y": 0}, "data": {"label": "E"}},
        ],
        edges=[
            {"id": "e1", "source": "s", "target": "ie"},
            # Both "a" and "b" hang off the SAME "true" branch via genuine
            # unconditional fan-out -- both fire together whenever the
            # condition is true, so they're not mutually exclusive.
            {"id": "e2", "source": "ie", "target": "a", "branch": "true"},
            {"id": "e3", "source": "ie", "target": "b", "branch": "true"},
            {"id": "e4", "source": "ie", "target": "no", "branch": "false"},
            {"id": "e5", "source": "a", "target": "e"},
            {"id": "e6", "source": "b", "target": "e"},
            {"id": "e7", "source": "no", "target": "e"},
        ],
    )
    with pytest.raises(
        WorkflowValidationError, match=r"End node 'e' must have exactly one incoming edge"
    ):
        validate_workflow_shape(wf)


def test_join_with_zero_outgoing_edges_fails() -> None:
    # "e" gets its own direct incoming edge from "s" (unrelated to what's
    # under test) specifically so End's own "exactly one incoming edge"
    # check passes and doesn't mask the Join check this test targets.
    wf = _mk(
        nodes=[
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {"id": "j", "type": "join", "position": {"x": 1, "y": 0}, "data": {"label": "J"}},
            {"id": "e", "type": "end", "position": {"x": 2, "y": 0}, "data": {"label": "E"}},
        ],
        edges=[
            {"id": "e1", "source": "s", "target": "j"},
            {"id": "e2", "source": "s", "target": "e"},
        ],
    )
    with pytest.raises(
        WorkflowValidationError, match=r"Join node 'j' must have exactly one outgoing edge"
    ):
        validate_workflow_shape(wf)


def test_join_with_two_outgoing_edges_fails() -> None:
    wf = _mk(
        nodes=[
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {"id": "j", "type": "join", "position": {"x": 1, "y": 0}, "data": {"label": "J"}},
            {"id": "e1", "type": "end", "position": {"x": 2, "y": 0}, "data": {"label": "E1"}},
            {"id": "e2", "type": "end", "position": {"x": 2, "y": 1}, "data": {"label": "E2"}},
        ],
        edges=[
            {"id": "e1e", "source": "s", "target": "j"},
            {"id": "e2e", "source": "j", "target": "e1"},
            {"id": "e3e", "source": "j", "target": "e2"},
        ],
    )
    with pytest.raises(
        WorkflowValidationError, match=r"Join node 'j' must have exactly one outgoing edge"
    ):
        validate_workflow_shape(wf)


async def test_fan_out_through_join_converges_safely() -> None:
    """The recommended pattern: two parallel branches converge through an
    explicit join node before reaching a single End. Both branches use
    the IDENTICAL stateValue deliberately: set-state also aliases its
    value into variables.lastOutput (see src/executors/set_state.py),
    so two branches racing on THAT shared key would be a separate,
    already-flagged, out-of-scope concern if they wrote DIFFERENT
    values -- using the same value keeps this test a clean, honest proof
    of only what this fix claims: that both branches' OWN distinctly-
    named variables survive the merge with no data loss."""
    from src.engine.state import initial_state

    wf = _mk(
        nodes=[
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {
                "id": "a",
                "type": "set-state",
                "position": {"x": 1, "y": 0},
                "data": {"label": "A", "stateKey": "branch_a_ran", "stateValue": "yes"},
            },
            {
                "id": "b",
                "type": "set-state",
                "position": {"x": 1, "y": 1},
                "data": {"label": "B", "stateKey": "branch_b_ran", "stateValue": "yes"},
            },
            {"id": "j", "type": "join", "position": {"x": 2, "y": 0}, "data": {"label": "J"}},
            {"id": "e", "type": "end", "position": {"x": 3, "y": 0}, "data": {"label": "E"}},
        ],
        edges=[
            {"id": "e1", "source": "s", "target": "a"},
            {"id": "e2", "source": "s", "target": "b"},
            {"id": "e3", "source": "a", "target": "j"},
            {"id": "e4", "source": "b", "target": "j"},
            {"id": "e5", "source": "j", "target": "e"},
        ],
    )
    compiled = build_graph(wf, MemorySaver())
    result = await compiled.ainvoke(  # pyright: ignore[reportUnknownMemberType]
        initial_state(), config={"configurable": {"thread_id": "t1"}}
    )

    # Both branches' distinctly-named work survived the merge into the
    # single join/End path -- no data loss on the variables side.
    assert result["variables"]["branch_a_ran"] == "yes"
    assert result["variables"]["branch_b_ran"] == "yes"
    assert result["final_outputs"] == {"e": "yes"}
    assert "j" in result["node_results"]
    assert "e" in result["node_results"]


async def test_fan_out_to_independent_ends_captures_both_outputs() -> None:
    """Two branches never reconverge -- each has its own single-incoming-
    edge End (still legal; see the End/join validation tests above).
    Proves final_outputs captures BOTH branches' outputs with no data
    loss, even though both End nodes fire in the same LangGraph
    superstep -- this is the exact topology that used to silently lose
    one branch's output before this fix. Both branches deliberately use
    the SAME stateValue for the same reason as the join test above: this
    isolates the proof to the final_outputs write-collision fix under
    test, without also depending on the separate, out-of-scope
    variables.lastOutput collision that would occur if the branches
    wrote DIFFERENT values."""
    from src.engine.state import initial_state

    wf = _mk(
        nodes=[
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {
                "id": "a",
                "type": "set-state",
                "position": {"x": 1, "y": 0},
                "data": {"label": "A", "stateKey": "a_marker", "stateValue": "shared-value"},
            },
            {
                "id": "b",
                "type": "set-state",
                "position": {"x": 1, "y": 1},
                "data": {"label": "B", "stateKey": "b_marker", "stateValue": "shared-value"},
            },
            {"id": "end-a", "type": "end", "position": {"x": 2, "y": 0}, "data": {"label": "EA"}},
            {"id": "end-b", "type": "end", "position": {"x": 2, "y": 1}, "data": {"label": "EB"}},
        ],
        edges=[
            {"id": "e1", "source": "s", "target": "a"},
            {"id": "e2", "source": "s", "target": "b"},
            {"id": "e3", "source": "a", "target": "end-a"},
            {"id": "e4", "source": "b", "target": "end-b"},
        ],
    )
    compiled = build_graph(wf, MemorySaver())
    result = await compiled.ainvoke(  # pyright: ignore[reportUnknownMemberType]
        initial_state(), config={"configurable": {"thread_id": "t1"}}
    )

    # Both End nodes' own contributions are present -- this is the exact
    # assertion that would have been flaky/lossy before this fix (one of
    # the two keys would sometimes be missing, depending on LangGraph's
    # unspecified concurrent-write application order).
    assert result["final_outputs"] == {"end-a": "shared-value", "end-b": "shared-value"}
    assert result["variables"]["a_marker"] == "shared-value"
    assert result["variables"]["b_marker"] == "shared-value"


# ---------------------------------------------------------------------------
# Task 5 (decision node): conditional-edge emission + routing
# ---------------------------------------------------------------------------


def test_conditional_edges_compile_for_decision_binary() -> None:
    from langgraph.checkpoint.memory import MemorySaver

    from src.engine.graph_builder import build_graph
    from src.engine.workflow import Workflow

    wf = Workflow.model_validate(
        {
            "id": "w1",
            "name": "decision binary test",
            "nodes": [
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                {
                    "id": "d",
                    "type": "decision",
                    "position": {"x": 100, "y": 0},
                    "data": {"label": "D", "mode": "binary", "instruction": "urgent?"},
                },
                {"id": "a", "type": "end", "position": {"x": 200, "y": 0}, "data": {"label": "A"}},
                {
                    "id": "b",
                    "type": "end",
                    "position": {"x": 200, "y": 100},
                    "data": {"label": "B"},
                },
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "d"},
                {"id": "e2", "source": "d", "target": "a", "branch": "true"},
                {"id": "e3", "source": "d", "target": "b", "branch": "false"},
            ],
        }
    )
    compiled = build_graph(wf, MemorySaver())
    assert compiled is not None


def test_conditional_edges_compile_for_decision_choice_three_way() -> None:
    from langgraph.checkpoint.memory import MemorySaver

    from src.engine.graph_builder import build_graph
    from src.engine.workflow import Workflow

    wf = Workflow.model_validate(
        {
            "id": "w1",
            "name": "decision choice test",
            "nodes": [
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                {
                    "id": "d",
                    "type": "decision",
                    "position": {"x": 100, "y": 0},
                    "data": {
                        "label": "D",
                        "mode": "choice",
                        "instruction": "route it",
                        "options": [
                            {"label": "billing"},
                            {"label": "technical"},
                            {"label": "sales"},
                        ],
                    },
                },
                {"id": "a", "type": "end", "position": {"x": 200, "y": 0}, "data": {"label": "A"}},
                {
                    "id": "b",
                    "type": "end",
                    "position": {"x": 200, "y": 100},
                    "data": {"label": "B"},
                },
                {
                    "id": "c",
                    "type": "end",
                    "position": {"x": 200, "y": 200},
                    "data": {"label": "C"},
                },
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "d"},
                {"id": "e2", "source": "d", "target": "a", "branch": "billing"},
                {"id": "e3", "source": "d", "target": "b", "branch": "technical"},
                {"id": "e4", "source": "d", "target": "c", "branch": "sales"},
            ],
        }
    )
    compiled = build_graph(wf, MemorySaver())
    assert compiled is not None


def test_decision_router_falls_back_on_missing_result() -> None:
    from src.engine.graph_builder import _route_decision  # pyright: ignore[reportPrivateUsage]
    from src.engine.state import initial_state
    from src.engine.workflow import DecisionNode

    node = DecisionNode.model_validate(
        {
            "id": "d",
            "type": "decision",
            "position": {"x": 0, "y": 0},
            "data": {"label": "D", "mode": "binary", "instruction": "x?"},
        }
    )
    router = _route_decision(node)
    state = initial_state()  # no node_results for "d" yet
    assert router(state) == "false"


def test_decision_router_reads_binary_result_from_state() -> None:
    from src.engine.graph_builder import _route_decision  # pyright: ignore[reportPrivateUsage]
    from src.engine.state import initial_state
    from src.engine.workflow import DecisionNode

    node = DecisionNode.model_validate(
        {
            "id": "d",
            "type": "decision",
            "position": {"x": 0, "y": 0},
            "data": {"label": "D", "mode": "binary", "instruction": "x?"},
        }
    )
    router = _route_decision(node)
    state = initial_state()
    state["node_results"]["d"] = {"output": {"decision": True, "confidence": 0.9}}
    assert router(state) == "true"


def test_decision_router_reads_choice_result_from_state() -> None:
    from src.engine.graph_builder import _route_decision  # pyright: ignore[reportPrivateUsage]
    from src.engine.state import initial_state
    from src.engine.workflow import DecisionNode

    node = DecisionNode.model_validate(
        {
            "id": "d",
            "type": "decision",
            "position": {"x": 0, "y": 0},
            "data": {
                "label": "D",
                "mode": "choice",
                "instruction": "x?",
                "options": [{"label": "a"}, {"label": "b"}],
            },
        }
    )
    router = _route_decision(node)
    state = initial_state()
    state["node_results"]["d"] = {"output": {"decision": "b", "confidence": 0.5}}
    assert router(state) == "b"


# --- End incoming-edge rule: edges leaving a conditional node directly ---


def _n(node_id: str, kind: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": kind,
        "position": {"x": 0, "y": 0},
        "data": {"label": node_id, **(data or {})},
    }


def _ss(node_id: str) -> dict[str, Any]:
    return _n(node_id, "set-state", {"stateKey": "k", "stateValue": "v"})


def _ed(edge_id: str, source: str, target: str, branch: str | None = None) -> dict[str, Any]:
    edge: dict[str, Any] = {"id": edge_id, "source": source, "target": target}
    if branch is not None:
        edge["branch"] = branch
    return edge


def test_approval_rejected_straight_to_end_shared_with_approved_path_is_allowed() -> None:
    """Production case: approved -> step -> End, rejected -> the same End."""
    wf = _mk(
        nodes=[
            _n("s", "start"),
            _n("approval-1", "user-approval"),
            _ss("work"),
            _n("end-1", "end"),
        ],
        edges=[
            _ed("e1", "s", "approval-1"),
            _ed("e2", "approval-1", "work", "approved"),
            _ed("e3", "work", "end-1"),
            _ed("e4", "approval-1", "end-1", "rejected"),
        ],
    )
    validate_workflow_shape(wf)


def test_if_else_true_straight_to_end_and_false_through_step_is_allowed() -> None:
    wf = _mk(
        nodes=[
            _n("s", "start"),
            _n("ie", "if-else", {"condition": "True"}),
            _ss("w"),
            _n("e", "end"),
        ],
        edges=[
            _ed("e1", "s", "ie"),
            _ed("e2", "ie", "e", "true"),
            _ed("e3", "ie", "w", "false"),
            _ed("e4", "w", "e"),
        ],
    )
    validate_workflow_shape(wf)


def test_both_approval_branches_straight_to_same_end_is_allowed() -> None:
    wf = _mk(
        nodes=[_n("s", "start"), _n("ua", "user-approval"), _n("e", "end")],
        edges=[
            _ed("e1", "s", "ua"),
            _ed("e2", "ua", "e", "approved"),
            _ed("e3", "ua", "e", "rejected"),
        ],
    )
    validate_workflow_shape(wf)


def test_decision_options_straight_to_same_end_is_allowed() -> None:
    decision = _n(
        "d",
        "decision",
        {"mode": "choice", "options": [{"label": "a"}, {"label": "b"}], "instruction": "q"},
    )
    wf = _mk(
        nodes=[_n("s", "start"), decision, _ss("w"), _n("e", "end")],
        edges=[
            _ed("e1", "s", "d"),
            _ed("e2", "d", "w", "a"),
            _ed("e3", "w", "e"),
            _ed("e4", "d", "e", "b"),
        ],
    )
    validate_workflow_shape(wf)


def test_two_sequential_steps_into_one_end_still_rejected() -> None:
    wf = _mk(
        nodes=[_n("s", "start"), _ss("a"), _ss("b"), _n("e", "end")],
        edges=[
            _ed("e1", "s", "a"),
            _ed("e2", "a", "b"),
            _ed("e3", "a", "e"),
            _ed("e4", "b", "e"),
        ],
    )
    with pytest.raises(WorkflowValidationError, match="exactly one incoming edge"):
        validate_workflow_shape(wf)


def test_two_edges_from_same_branch_into_one_end_still_rejected() -> None:
    wf = _mk(
        nodes=[_n("s", "start"), _n("ua", "user-approval"), _ss("w"), _n("e", "end")],
        edges=[
            _ed("e1", "s", "ua"),
            _ed("e2", "ua", "w", "approved"),
            _ed("e3", "ua", "e", "approved"),
            _ed("e4", "w", "e"),
            _ed("e5", "ua", "e", "rejected"),
        ],
    )
    with pytest.raises(WorkflowValidationError, match="exactly one incoming edge"):
        validate_workflow_shape(wf)


# --- End incoming-edge rule: parallel fan-out ancestors ---


def _ie(node_id: str) -> dict[str, Any]:
    return _n(node_id, "if-else", {"condition": "True"})


def _ee(source: str, target: str, branch: str | None = None) -> dict[str, Any]:
    return _ed(f"{source}-{target}-{branch}", source, target, branch)


def _topologies() -> dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]]]:
    """Shapes where an End has several incoming edges and a fan-out upstream."""
    return {
        "fanout_bypass_delayed": (
            [_n("s", "start"), _ss("p"), _ie("a"), _ss("y0"), _ss("y"), _n("end", "end")],
            [
                _ee("s", "p"),
                _ee("p", "a"),
                _ee("p", "y0"),
                _ee("y0", "y"),
                _ee("a", "end", "true"),
                _ee("a", "y", "false"),
                _ee("y", "end"),
            ],
        ),
        "fanout_two_conds": (
            [_n("s", "start"), _ss("p"), _ie("a"), _ie("b"), _ss("x"), _n("end", "end")],
            [
                _ee("s", "p"),
                _ee("p", "a"),
                _ee("p", "b"),
                _ee("a", "end", "true"),
                _ee("a", "x", "false"),
                _ee("b", "x", "true"),
                _ee("b", "x", "false"),
                _ee("x", "end"),
            ],
        ),
        "fanout_join": (
            [_n("s", "start"), _ss("p"), _ie("a"), _ss("y"), _n("j", "join"), _n("end", "end")],
            [
                _ee("s", "p"),
                _ee("p", "a"),
                _ee("p", "y"),
                _ee("a", "end", "true"),
                _ee("a", "j", "false"),
                _ee("y", "j"),
                _ee("j", "end"),
            ],
        ),
        "refan_after_join_into_one_end": (
            [
                _n("s", "start"),
                _ss("p"),
                _ss("a"),
                _ss("b"),
                _n("j", "join"),
                _ss("q"),
                _ss("q1"),
                _ss("q2"),
                _n("end", "end"),
            ],
            [
                _ee("s", "p"),
                _ee("p", "a"),
                _ee("p", "b"),
                _ee("a", "j"),
                _ee("b", "j"),
                _ee("j", "q"),
                _ee("q", "q1"),
                _ee("q", "q2"),
                _ee("q1", "end"),
                _ee("q2", "end"),
            ],
        ),
        "double_c": (
            [_n("s", "start"), _ss("p"), _ss("q"), _ie("c"), _n("end", "end")],
            [
                _ee("s", "p"),
                _ee("s", "q"),
                _ee("p", "c"),
                _ee("q", "c"),
                _ee("c", "end", "true"),
                _ee("c", "end", "false"),
            ],
        ),
        "bypass_old_hole": (
            [_n("s", "start"), _ss("p"), _ie("a"), _ss("x"), _ss("y"), _n("end", "end")],
            [
                _ee("s", "p"),
                _ee("p", "a"),
                _ee("p", "x"),
                _ee("a", "x", "true"),
                _ee("a", "y", "false"),
                _ee("x", "end"),
                _ee("y", "end"),
            ],
        ),
    }


def _accepted_topologies() -> dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]]]:
    decision = _n(
        "d",
        "decision",
        {
            "mode": "choice",
            "options": [{"label": "a"}, {"label": "b"}, {"label": "c"}],
            "instruction": "q",
        },
    )
    return {
        "approval_shape": (
            [_n("s", "start"), _n("ua", "user-approval"), _ss("w"), _n("end", "end")],
            [
                _ee("s", "ua"),
                _ee("ua", "w", "approved"),
                _ee("w", "end"),
                _ee("ua", "end", "rejected"),
            ],
        ),
        "nested_if_else": (
            [_n("s", "start"), _ie("a"), _ie("b"), _n("end", "end")],
            [
                _ee("s", "a"),
                _ee("a", "b", "true"),
                _ee("b", "end", "true"),
                _ee("b", "end", "false"),
                _ee("a", "end", "false"),
            ],
        ),
        "branch_edge_plus_descendant": (
            [_n("s", "start"), _ie("a"), _ss("x"), _n("end", "end")],
            [
                _ee("s", "a"),
                _ee("a", "end", "true"),
                _ee("a", "x", "false"),
                _ee("x", "end"),
            ],
        ),
        "decision_three_options": (
            [_n("s", "start"), decision, _ss("w"), _n("end", "end")],
            [
                _ee("s", "d"),
                _ee("d", "w", "a"),
                _ee("w", "end"),
                _ee("d", "end", "b"),
                _ee("d", "end", "c"),
            ],
        ),
        "while_body_and_exit": (
            [
                _n("s", "start"),
                _n("w", "while", {"condition": "False", "maxIterations": 3}),
                _n("end", "end"),
            ],
            [_ee("s", "w"), _ee("w", "end", "body"), _ee("w", "end", "exit")],
        ),
        "fanout_join_if_both_end": (
            [
                _n("s", "start"),
                _ss("p"),
                _ss("a"),
                _ss("a2"),
                _ss("b"),
                _n("j", "join"),
                _ie("c"),
                _n("end", "end"),
            ],
            [
                _ee("s", "p"),
                _ee("p", "a"),
                _ee("a", "a2"),
                _ee("a2", "j"),
                _ee("p", "b"),
                _ee("b", "j"),
                _ee("j", "c"),
                _ee("c", "end", "true"),
                _ee("c", "end", "false"),
            ],
        ),
        "note_edge_into_end": (
            [_n("s", "start"), _ss("x"), _n("nt", "note"), _n("end", "end")],
            [_ee("s", "x"), _ee("x", "end"), _ee("nt", "end")],
        ),
    }


@pytest.mark.parametrize("name", list(_topologies()))
def test_end_reachable_by_parallel_paths_is_refused(name: str) -> None:
    nodes, edges = _topologies()[name]
    wf = _mk(nodes, edges)
    with pytest.raises(WorkflowValidationError, match="reached by parallel paths") as info:
        validate_workflow_shape(wf)
    msg = str(info.value)
    assert msg.startswith("End node 'end' must have exactly one incoming edge")
    assert "Give each parallel path its own End step, or merge them with a Join first." in msg


@pytest.mark.parametrize("name", list(_accepted_topologies()))
def test_exclusive_end_shapes_are_accepted(name: str) -> None:
    nodes, edges = _accepted_topologies()[name]
    validate_workflow_shape(_mk(nodes, edges))


@pytest.mark.parametrize(
    "name",
    [
        n
        for n in _accepted_topologies()
        if n not in ("approval_shape", "decision_three_options")  # pause / call an LLM
    ],
)
async def test_accepted_end_shapes_run_end_exactly_once(name: str) -> None:
    """Whatever the validator accepts must never run End twice. (The approval
    shape pauses and the decision shape calls an LLM; tests/integration/test_approval_branch_straight_to_end.py
    runs it.)"""
    from src.engine.state import initial_state

    nodes, edges = _accepted_topologies()[name]
    wf = _mk(nodes, edges)
    graph = build_graph(wf, MemorySaver())
    count = 0
    config: Any = {"configurable": {"thread_id": f"t-{name}"}, "recursion_limit": 60}
    async for chunk in graph.astream(initial_state("x"), config, stream_mode="updates"):
        for key in chunk:
            if key == "end":
                count += 1
    assert count == 1


# --- Required branches: warn on save, block run (single source of truth) ---

from src.engine.graph_builder import (  # noqa: E402
    _branch_mapping,  # pyright: ignore[reportPrivateUsage]
    collect_missing_branches,
    collect_workflow_violations,
    required_branches,
    validate_workflow_runnable,
)


def _approval_wf(branches: list[str]) -> Workflow:
    edges = [_ed("e0", "s", "approval-1")]
    for i, branch in enumerate(branches):
        edges.append(_ed(f"b{i}", "approval-1", "end-1", branch))
    return _mk(
        nodes=[_n("s", "start"), _n("approval-1", "user-approval"), _n("end-1", "end")],
        edges=edges,
    )


def test_missing_rejected_is_reported_in_user_words() -> None:
    wf = _approval_wf(["approved"])
    assert collect_missing_branches(wf) == [
        'User approval step "approval-1" has no "rejected" path. '
        'Connect its "rejected" handle to a step (for example End).'
    ]


def test_missing_message_uses_node_label_when_it_differs_from_id() -> None:
    wf = _mk(
        nodes=[
            _n("s", "start"),
            {**_n("approval-1", "user-approval"), "data": {"label": "Manager sign-off"}},
            _n("end-1", "end"),
        ],
        edges=[_ed("e0", "s", "approval-1"), _ed("e1", "approval-1", "end-1", "approved")],
    )
    (message,) = collect_missing_branches(wf)
    assert '"Manager sign-off" (approval-1)' in message


def test_missing_false_branch_of_if_else() -> None:
    wf = _mk(
        nodes=[_n("s", "start"), _n("ie", "if-else", {"condition": "True"}), _n("e", "end")],
        edges=[_ed("e1", "s", "ie"), _ed("e2", "ie", "e", "true")],
    )
    (message,) = collect_missing_branches(wf)
    assert message.startswith('If / Else step "ie" has no "false" path.')


def test_missing_exit_branch_of_while() -> None:
    wf = _mk(
        nodes=[_n("s", "start"), _n("w", "while", {"condition": "True"}), _ss("b"), _n("e", "end")],
        edges=[_ed("e1", "s", "w"), _ed("e2", "w", "b", "body"), _ed("e3", "b", "e")],
    )
    (message,) = collect_missing_branches(wf)
    assert message.startswith('While step "w" has no "exit" path.')


def test_missing_decision_option_is_reported() -> None:
    decision = _n(
        "d",
        "decision",
        {"mode": "choice", "options": [{"label": "a"}, {"label": "b"}], "instruction": "q"},
    )
    wf = _mk(
        nodes=[_n("s", "start"), decision, _n("e", "end")],
        edges=[_ed("e1", "s", "d"), _ed("e2", "d", "e", "a")],
    )
    (message,) = collect_missing_branches(wf)
    assert message.startswith('Decision step "d" has no "b" path.')


def test_no_missing_branches_when_all_connected() -> None:
    assert collect_missing_branches(_approval_wf(["approved", "rejected"])) == []
    validate_workflow_runnable(_approval_wf(["approved", "rejected"]))


def test_runnable_gate_raises_with_the_message_but_shape_validation_still_passes() -> None:
    wf = _approval_wf(["approved"])
    assert collect_workflow_violations(wf) == []  # saves
    validate_workflow_shape(wf)  # saves
    with pytest.raises(WorkflowValidationError, match='no "rejected" path'):
        validate_workflow_runnable(wf)


@pytest.mark.parametrize("branches", [["approved"], ["rejected"], ["approved", "rejected"], []])
def test_missing_branches_agree_with_branch_mapping(branches: list[str]) -> None:
    wf = _approval_wf(branches)
    node = wf.nodes[1]
    required = required_branches(node)
    assert required == {"approved", "rejected"}
    assert required is not None
    try:
        _branch_mapping(node, list(wf.edges), required)
        mapping_accepts = True
    except WorkflowValidationError:
        mapping_accepts = False
    assert mapping_accepts == (collect_missing_branches(wf) == [])
