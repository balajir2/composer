"""Deterministic layered auto-layout for builder-generated graphs.

No external dependency (no dagre). Depth = BFS from the start node; nodes not
reachable from start are placed one layer past the deepest layer. Within a
layer, nodes are ordered by id so output is deterministic. Positions are
advisory — the user can drag nodes afterward.
"""

from collections import deque

from src.engine.workflow import Position, WorkflowEdge, WorkflowNode

_LAYER_X = 260.0
_NODE_Y = 120.0


def compute_layout(nodes: list[WorkflowNode], edges: list[WorkflowEdge]) -> dict[str, Position]:
    start = next((n for n in nodes if n.type == "start"), None)
    adj: dict[str, list[str]] = {n.id: [] for n in nodes}
    for e in edges:
        if e.source in adj and e.target in adj:
            adj[e.source].append(e.target)

    depth: dict[str, int] = {n.id: 0 for n in nodes}
    if start is not None:
        seen: set[str] = set()
        queue: deque[str] = deque([start.id])
        while queue:
            cur = queue.popleft()
            if cur in seen:
                continue
            seen.add(cur)
            for nxt in sorted(adj[cur]):
                depth[nxt] = max(depth.get(nxt, 0), depth[cur] + 1)
                queue.append(nxt)

    max_depth = max(depth.values(), default=0)
    for n in nodes:
        if start is None or (
            n.id != start.id and depth[n.id] == 0 and not any(e.target == n.id for e in edges)
        ):
            depth[n.id] = max_depth + 1

    layers: dict[int, list[str]] = {}
    for n in nodes:
        layers.setdefault(depth[n.id], []).append(n.id)
    for ids in layers.values():
        ids.sort()

    positions: dict[str, Position] = {}
    for layer, ids in layers.items():
        for row, nid in enumerate(ids):
            positions[nid] = Position(x=layer * _LAYER_X, y=row * _NODE_Y)
    return positions
