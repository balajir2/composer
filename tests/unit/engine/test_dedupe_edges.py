"""Duplicate edges (same source, target, branch) are collapsed, never stored.

2026-11-19: duplicate edges reappeared on a builder-made canvas. The builder
path was fixed in 2b073c2; these tests pin the shared rule used by every
other path that saves or runs a workflow.
"""

from src.engine.graph_builder import dedupe_edges
from src.engine.workflow import WorkflowEdge


def _e(eid: str, source: str, target: str, branch: str | None = None) -> WorkflowEdge:
    return WorkflowEdge(id=eid, source=source, target=target, branch=branch)


def test_first_edge_per_source_target_branch_wins() -> None:
    edges = [_e("a", "s", "x"), _e("b", "s", "x"), _e("c", "x", "e"), _e("d", "s", "x")]
    assert [e.id for e in dedupe_edges(edges)] == ["a", "c"]


def test_different_branches_are_different_edges() -> None:
    edges = [_e("t", "if", "e", "true"), _e("f", "if", "e", "false")]
    assert [e.id for e in dedupe_edges(edges)] == ["t", "f"]


def test_no_duplicates_returns_equal_list() -> None:
    edges = [_e("a", "s", "x"), _e("b", "x", "e")]
    assert dedupe_edges(edges) == edges
