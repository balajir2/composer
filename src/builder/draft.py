"""In-memory mutable workflow draft for a single builder turn."""

from src.builder.layout import compute_layout
from src.engine.workflow import Position, WorkflowEdge, WorkflowNode


class Draft:
    def __init__(self) -> None:
        self.nodes: dict[str, WorkflowNode] = {}
        self.edges: dict[str, WorkflowEdge] = {}

    def snapshot(self) -> tuple[list[WorkflowNode], list[WorkflowEdge]]:
        return list(self.nodes.values()), list(self.edges.values())

    def positions(self) -> dict[str, Position]:
        nodes, edges = self.snapshot()
        return compute_layout(nodes, edges)
