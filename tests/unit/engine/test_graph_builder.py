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
    result = await compiled.ainvoke(initial_state(), config={"configurable": {"thread_id": "t1"}})
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
