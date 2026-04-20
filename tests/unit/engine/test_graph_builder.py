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


def test_build_graph_rejects_unshipped_executor_type() -> None:
    wf = _mk(
        nodes=[
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {"id": "a", "type": "agent", "position": {"x": 0, "y": 0}, "data": {"label": "A"}},
            {"id": "e", "type": "end", "position": {"x": 0, "y": 0}, "data": {"label": "E"}},
        ],
        edges=[
            {"id": "e1", "source": "s", "target": "a"},
            {"id": "e2", "source": "a", "target": "e"},
        ],
    )
    with pytest.raises(NotImplementedError, match="Phase 2"):
        build_graph(wf, MemorySaver())


def test_build_graph_rejects_conditional_edge_source() -> None:
    wf = _mk(
        nodes=[
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {"id": "cond", "type": "if-else", "position": {"x": 0, "y": 0}, "data": {"label": "C"}},
            {"id": "e", "type": "end", "position": {"x": 0, "y": 0}, "data": {"label": "E"}},
        ],
        edges=[
            {"id": "e1", "source": "s", "target": "cond"},
            {"id": "e2", "source": "cond", "target": "e"},
        ],
    )
    with pytest.raises(NotImplementedError, match="Phase 4"):
        build_graph(wf, MemorySaver())


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
