"""Workflow → LangGraph StateGraph compilation.

Task 8 implements validation. Task 9 adds compile().

Reference: OAB lib/workflow/langgraph.ts:169-427.
"""

from collections import deque
from collections.abc import Callable, Hashable, Iterable
from dataclasses import dataclass, field
from typing import Any, Literal, NamedTuple, cast

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.constants import END, START
from langgraph.graph import StateGraph  # pyright: ignore[reportMissingTypeStubs]
from langgraph.graph.state import CompiledStateGraph  # pyright: ignore[reportMissingTypeStubs]

from src.engine.events_wrapper import wrap_executor_with_events
from src.engine.state import WorkflowStateDict
from src.engine.workflow import (
    DecisionNode,
    IfElseNode,
    UserApprovalNode,
    WhileNode,
    Workflow,
    WorkflowEdge,
    WorkflowNode,
)

# Executors are registered as a side effect of import; importing them here
# ensures the registry is populated before build_graph reads it.
from src.executors import (
    agent as _agent_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from src.executors import (
    arcade as _arcade_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from src.executors import (
    confluence as _confluence_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from src.executors import (
    data_transform as _data_transform_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from src.executors import (
    decision as _decision_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from src.executors import (
    download_pdf as _download_pdf_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from src.executors import (
    email as _email_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from src.executors import end as _end_executor  # noqa: F401  # pyright: ignore[reportUnusedImport]
from src.executors import (
    extract as _extract_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from src.executors import (
    file_write as _file_write_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from src.executors import (
    gamma_ai as _gamma_ai_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from src.executors import (
    guardrails as _guardrails_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from src.executors import (
    http as _http_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from src.executors import (
    if_else as _if_else_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from src.executors import (
    jira as _jira_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from src.executors import (
    join as _join_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from src.executors import (
    join_chunks as _join_chunks_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from src.executors import (
    mcp as _mcp_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from src.executors import (
    set_state as _set_state_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from src.executors import (
    start as _start_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from src.executors import (
    transform as _transform_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from src.executors import (
    user_approval as _user_approval_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from src.executors import (
    vector_db as _vector_db_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from src.executors import (
    while_loop as _while_loop_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from src.executors._eval import EvalError, evaluate
from src.executors.base import build_executor


class WorkflowValidationError(ValueError):
    """Raised when a workflow's shape is invalid before execution."""


# Node types that never execute: they are pure canvas annotations / metadata
# consumed by tooling outside the execution graph (note = author's memo;
# file-trigger = config read by the `composer watch` CLI). graph_builder
# skips them entirely and workflow-shape validation allows them disconnected.
_VISUAL_ONLY_TYPES = {"note", "file-trigger"}


def _nodes_by_id(nodes: Iterable[WorkflowNode]) -> dict[str, WorkflowNode]:  # pyright: ignore[reportUnusedFunction]
    seen: dict[str, WorkflowNode] = {}
    for node in nodes:
        if node.id in seen:
            raise WorkflowValidationError(f"Node id {node.id!r} is duplicated.")
        seen[node.id] = node
    return seen


def _check_edges(edges: Iterable[WorkflowEdge], ids: set[str]) -> None:  # pyright: ignore[reportUnusedFunction]
    for edge in edges:
        if edge.source not in ids:
            raise WorkflowValidationError(
                f"Edge {edge.id!r} references unknown source node id {edge.source!r}."
            )
        if edge.target not in ids:
            raise WorkflowValidationError(
                f"Edge {edge.id!r} references unknown target node id {edge.target!r}."
            )


def _check_reachability(  # pyright: ignore[reportUnusedFunction]
    start_id: str, nodes: dict[str, WorkflowNode], edges: list[WorkflowEdge]
) -> None:
    """BFS from start; every non-visual-only node must be reachable.

    Uses the same edge projection build_graph uses at compile time (edges
    touching a visual-only node on either end are dropped — see the
    "normal edges" pass below) so a node reachable only via a detour
    through a note/file-trigger isn't falsely validated as reachable
    when it would actually be disconnected once compiled (P0-8).
    """
    outgoing: dict[str, list[str]] = {node_id: [] for node_id in nodes}
    for edge in edges:
        if nodes[edge.source].type in _VISUAL_ONLY_TYPES:
            continue
        if nodes[edge.target].type in _VISUAL_ONLY_TYPES:
            continue
        outgoing[edge.source].append(edge.target)

    reachable: set[str] = set()
    queue: deque[str] = deque([start_id])
    while queue:
        current = queue.popleft()
        if current in reachable:
            continue
        reachable.add(current)
        queue.extend(outgoing[current])

    for node_id, node in nodes.items():
        if node.type in _VISUAL_ONLY_TYPES:
            continue  # visual-only, allowed disconnected
        if node_id not in reachable:
            raise WorkflowValidationError(
                f"Node {node_id!r} ({node.type!r}) is unreachable from the start node."
            )


def _branch_markers(
    nodes: dict[str, WorkflowNode], edges: list[WorkflowEdge]
) -> dict[str, set[tuple[str, str]]]:
    """For each node, the (conditional_node_id, branch_label) pairs that hold
    on every path from Start to that node -- i.e. which branch of which
    if-else/while/user-approval ancestor was taken to reach it. Used to prove
    two `End`-reaching branches are mutually exclusive (can never both fire
    in the same run) rather than requiring a `join` node in between.

    Approximate for cyclic graphs (a `while` body edge feeds back into an
    already-visited node): iterates to a fixed point instead of a strict
    topological walk. Workflows are capped at 100 nodes / 200 edges
    (Phase 8), so the extra passes are cheap regardless.
    """
    markers: dict[str, set[tuple[str, str]]] = {node_id: set() for node_id in nodes}
    incoming: dict[str, list[WorkflowEdge]] = {}
    for edge in edges:
        incoming.setdefault(edge.target, []).append(edge)

    start_id = next((n.id for n in nodes.values() if n.type == "start"), None)
    changed = True
    iterations = 0
    while changed and iterations <= len(nodes):
        changed = False
        iterations += 1
        for node_id, node_edges in incoming.items():
            if node_id == start_id:
                continue
            new_marks: set[tuple[str, str]] = set()
            for edge in node_edges:
                source = nodes.get(edge.source)
                if source is None:
                    continue
                new_marks |= markers.get(edge.source, set())
                if source.type in CONDITIONAL_SOURCE_TYPES and edge.branch is not None:
                    new_marks.add((edge.source, edge.branch))
            if not new_marks.issubset(markers[node_id]):
                markers[node_id] |= new_marks
                changed = True
    return markers


def _mutually_exclusive(markers_a: set[tuple[str, str]], markers_b: set[tuple[str, str]]) -> bool:
    """True if some shared conditional ancestor was reached via disjoint
    branch sets on the way to each -- meaning a single run that reaches one
    can never also reach the other."""
    conds_a = {cond for cond, _ in markers_a}
    conds_b = {cond for cond, _ in markers_b}
    for cond in conds_a & conds_b:
        branches_a = {branch for c, branch in markers_a if c == cond}
        branches_b = {branch for c, branch in markers_b if c == cond}
        if branches_a.isdisjoint(branches_b):
            return True
    return False


def _fan_out_ancestors_of(
    target_id: str,
    nodes: dict[str, WorkflowNode],
    edges: list[WorkflowEdge],
    cleared: frozenset[str] = frozenset(),
) -> bool:
    """True if any ancestor of `target_id` (walking executable edges backwards)
    is a fan-out node: not a conditional router, with two or more distinct
    executable successors. `build_graph` compiles those into parallel
    `add_edge`s, so tokens from different successors can reach one node in
    the same run -- which branch markers alone cannot see. Visual-only nodes
    and edges do not count. A `join` (one successor) is not itself a fan-out,
    but the fan-out feeding it still is an ancestor -- unless a barrier Join
    has `cleared` it: every parallel path of a cleared fan-out is merged
    back into one before this point (see `analyze_joins`).
    """
    successors: dict[str, set[str]] = {}
    predecessors: dict[str, set[str]] = {}
    for edge in edges:
        if nodes[edge.source].type in _VISUAL_ONLY_TYPES:
            continue
        if nodes[edge.target].type in _VISUAL_ONLY_TYPES:
            continue
        successors.setdefault(edge.source, set()).add(edge.target)
        predecessors.setdefault(edge.target, set()).add(edge.source)

    seen: set[str] = set()
    stack = list(predecessors.get(target_id, ()))
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        if (
            current not in cleared
            and nodes[current].type not in CONDITIONAL_SOURCE_TYPES
            and len(successors.get(current, ())) >= 2
        ):
            return True
        stack.extend(predecessors.get(current, ()))
    return False


def dedupe_edges(edges: list[WorkflowEdge]) -> list[WorkflowEdge]:
    """Keep the first edge per (source, target, branch); drop later copies.

    The engine routes on `branch` only (never `sourceHandle`), so two edges
    agreeing on all three are indistinguishable at run time. Applied on every
    save and run path instead of being reported as a violation: rejecting
    duplicates would make workflows that already contain them unsaveable.
    """
    seen: set[tuple[str, str, str | None]] = set()
    kept: list[WorkflowEdge] = []
    for edge in edges:
        key = (edge.source, edge.target, edge.branch)
        if key not in seen:
            seen.add(key)
            kept.append(edge)
    return kept


# --- Join analysis: merge Joins vs barrier Joins ---------------------------
#
# LangGraph fires a node in every superstep in which any plain-edge
# predecessor finishes, so a Join fed by parallel paths of different lengths
# would run (and everything after it) once per arrival. `plan_join` decides,
# per Join, whether its inputs are alternatives (a "merge": plain edges are
# right) or parallel paths (a "barrier": `add_edge([lanes...], join)` waits
# for all of them). `collect_workflow_violations` (End rule) and `build_graph`
# (wiring) both read the same `JoinAnalysis`, so they cannot drift apart.

JOIN_RELAY_PREFIX = "__join_lane__"

JoinProblemCode = Literal["join_mixed_inputs", "join_lane_escapes", "join_collision"]


# (source, target, branch): what identifies an edge once duplicates are dropped.
_EdgeKey = tuple[str, str, str | None]


class JoinProblem(NamedTuple):
    """A Join that cannot wait correctly for its parallel paths."""

    node_id: str  # the Join, or (join_collision) the step two paths collide on
    code: JoinProblemCode
    message: str


@dataclass(frozen=True)
class JoinPlan:
    """How one Join is wired.

    `lanes` is empty for a merge Join. For a barrier Join each lane is a set
    of incoming edges of which at most one carries a token per run; the
    barrier waits for one arrival per lane. `separating_fan_outs` are the
    fan-outs whose parallel paths the Join waits for. `violations` holds at
    most one problem; a Join with a violation cannot be compiled.
    """

    join_id: str
    kind: Literal["merge", "barrier"]
    lanes: tuple[tuple[WorkflowEdge, ...], ...] = ()
    separating_fan_outs: frozenset[str] = frozenset()
    violations: tuple[JoinProblem, ...] = ()


@dataclass(frozen=True)
class JoinAnalysis:
    plans: dict[str, JoinPlan] = field(default_factory=lambda: {})
    # Fan-outs whose every path is merged by a valid barrier Join: an End
    # reached through such a Join no longer counts as reachable by parallel paths.
    cleared: frozenset[str] = frozenset()

    @property
    def problems(self) -> list[JoinProblem]:
        return [v for plan in self.plans.values() for v in plan.violations]


def _quoted_label(node: WorkflowNode) -> str:
    """`"Label"` or, when the label differs from the id, `"Label" (id)`."""
    label = getattr(node.data, "label", None) or node.id
    return f'"{label}"' if label == node.id else f'"{label}" ({node.id})'


class _ExecutableGraph:
    """The executable part of a workflow: visual-only nodes and every edge
    touching one are dropped, exactly as `_check_reachability` and
    `build_graph` do."""

    def __init__(self, nodes: dict[str, WorkflowNode], edges: list[WorkflowEdge]) -> None:
        self.nodes = nodes
        self.edges = [
            e
            for e in dedupe_edges(edges)
            if e.source in nodes
            and e.target in nodes
            and nodes[e.source].type not in _VISUAL_ONLY_TYPES
            and nodes[e.target].type not in _VISUAL_ONLY_TYPES
        ]
        self.successors: dict[str, set[str]] = {node_id: set() for node_id in nodes}
        self.incoming: dict[str, list[WorkflowEdge]] = {node_id: [] for node_id in nodes}
        for edge in self.edges:
            self.successors[edge.source].add(edge.target)
            self.incoming[edge.target].append(edge)
        self.fan_outs = {
            node_id
            for node_id, targets in self.successors.items()
            if nodes[node_id].type not in CONDITIONAL_SOURCE_TYPES and len(targets) >= 2
        }
        self._markers = _branch_markers(nodes, self.edges)
        self._forward_cache: dict[tuple[str, frozenset[str], bool], frozenset[str]] = {}

    def is_join(self, node_id: str) -> bool:
        return self.nodes[node_id].type == "join"

    def is_sink(self, node_id: str) -> bool:
        return self.nodes[node_id].type == "end" or not self.successors[node_id]

    def edge_markers(self, edge: WorkflowEdge) -> set[tuple[str, str]]:
        """Branch markers of one edge: how its source was reached, plus the
        branch it leaves a conditional source by."""
        marks = set(self._markers[edge.source])
        if self.nodes[edge.source].type in CONDITIONAL_SOURCE_TYPES and edge.branch is not None:
            marks.add((edge.source, edge.branch))
        return marks

    def forward(self, start: str, avoid: frozenset[str], *, through_joins: bool) -> frozenset[str]:
        """Nodes reachable from `start` without entering `avoid`. Without
        `through_joins` a Join can be reached but not walked through: a Join's
        own output is a new token, not the lane's."""
        key = (start, avoid, through_joins)
        cached = self._forward_cache.get(key)
        if cached is not None:
            return cached
        seen: set[str] = set()
        stack = [t for t in self.successors[start] if t not in avoid]
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            if not through_joins and self.is_join(current):
                continue
            stack.extend(t for t in self.successors[current] if t not in avoid)
        result = frozenset(seen)
        self._forward_cache[key] = result
        return result

    def lanes_from(self, fan_out: str, edge: WorkflowEdge) -> set[str]:
        """The successors of `fan_out` whose token can arrive over `edge`."""
        lanes: set[str] = set()
        avoid = frozenset({edge.target, fan_out})
        for successor in self.successors[fan_out]:
            if (edge.source == fan_out and successor == edge.target) or (
                successor not in avoid
                and (
                    successor == edge.source
                    or edge.source in self.forward(successor, avoid, through_joins=False)
                )
            ):
                lanes.add(successor)
        return lanes

    def co_fire(self, first: WorkflowEdge, second: WorkflowEdge) -> set[str]:
        """Fan-outs that can put a token on both edges in one run (empty =
        the edges can never both carry a token)."""
        if _mutually_exclusive(self.edge_markers(first), self.edge_markers(second)):
            return set()
        return {
            fan_out
            for fan_out in self.fan_outs
            if any(
                a != b
                for a in self.lanes_from(fan_out, first)
                for b in self.lanes_from(fan_out, second)
            )
        }


def plan_join(graph: _ExecutableGraph, join_id: str) -> JoinPlan:
    """Decide how `join_id` is wired and whether it can wait correctly.

    Merge: fewer than two incoming edges, or no pair of them can both carry a
    token in one run (if/else branches meeting, a loop re-entering): plain
    edges are right. Barrier: some pair co-fires, so the Join must wait for
    one arrival per lane -- lanes being the classes of "never both fire".
    """
    incoming = graph.incoming[join_id]
    if len(incoming) < 2:
        return JoinPlan(join_id, "merge")
    co_fire: dict[tuple[int, int], set[str]] = {}
    for a in range(len(incoming)):
        for b in range(a + 1, len(incoming)):
            hits = graph.co_fire(incoming[a], incoming[b])
            if hits:
                co_fire[(a, b)] = hits
    if not co_fire:
        return JoinPlan(join_id, "merge")
    separating = frozenset(f for hits in co_fire.values() for f in hits)
    join_label = _quoted_label(graph.nodes[join_id])

    def label(node_id: str) -> str:
        return _quoted_label(graph.nodes[node_id])

    def refuse(problem: JoinProblem) -> JoinPlan:
        return JoinPlan(join_id, "barrier", (), separating, (problem,))

    # Lanes: union-find over the pairs that never both fire.
    parent = list(range(len(incoming)))

    def find(x: int) -> int:
        while parent[x] != x:
            x = parent[x]
        return x

    for a in range(len(incoming)):
        for b in range(a + 1, len(incoming)):
            if (a, b) not in co_fire:
                parent[find(a)] = find(b)
    classes: dict[int, list[int]] = {}
    for index in range(len(incoming)):
        classes.setdefault(find(index), []).append(index)

    # "Never both fire" must be an equivalence: no lane may contain a co-firing pair.
    for members in classes.values():
        for x in members:
            for y in members:
                if x < y and (x, y) in co_fire:
                    others = [
                        incoming[i].source
                        for i in members
                        if i != x and (min(i, x), max(i, x)) not in co_fire
                    ]
                    instead = label(others[0]) if others else "another incoming step"
                    return refuse(
                        JoinProblem(
                            join_id,
                            "join_mixed_inputs",
                            f"Join step {join_label} mixes parallel paths with an alternative "
                            f"path: the steps {label(incoming[x].source)} and "
                            f"{label(incoming[y].source)} run in parallel, but {instead} runs "
                            f"instead of one of them. Merge the parallel paths with their own "
                            f"Join first, then connect that Join and the alternative path to "
                            f"this one.",
                        )
                    )
    lanes = tuple(tuple(incoming[i] for i in members) for members in classes.values())

    # The region between each separating fan-out and the Join is what a
    # parallel path passes through. A path that can finish before the Join
    # leaves the barrier waiting forever; two paths meeting at a plain step
    # run it twice.
    for fan_out in sorted(separating):
        region = sorted(
            n
            for n in graph.forward(fan_out, frozenset({join_id}), through_joins=True)
            if n not in (fan_out, join_id)
            and join_id in graph.forward(n, frozenset(), through_joins=True)
        )
        for node_id in region:
            reachable = {node_id} | graph.forward(node_id, frozenset({join_id}), through_joins=True)
            sinks = sorted(t for t in reachable if graph.is_sink(t))
            if sinks:
                return refuse(
                    JoinProblem(
                        join_id,
                        "join_lane_escapes",
                        f"Join step {join_label} waits for every parallel path, but the path "
                        f"through {label(node_id)} can finish at {label(sinks[0])} without "
                        f"reaching it, so the Join would wait forever. Connect that path to the "
                        f"Join, or end it after the Join.",
                    )
                )
            if graph.is_join(node_id):
                continue
            arriving = graph.incoming[node_id]
            for a in range(len(arriving)):
                for b in range(a + 1, len(arriving)):
                    if graph.co_fire(arriving[a], arriving[b]):
                        return refuse(
                            JoinProblem(
                                node_id,
                                "join_collision",
                                f"Step {label(node_id)} is reached by more than one parallel "
                                f"path before Join step {join_label}, so it would run more than "
                                f"once. Connect those paths to a Join first.",
                            )
                        )
    return JoinPlan(join_id, "barrier", lanes, separating)


def analyze_joins(nodes: dict[str, WorkflowNode], edges: list[WorkflowEdge]) -> JoinAnalysis:
    """Plan every Join, and find the fan-outs they fully merge.

    A fan-out is cleared by a Join when the Join is a valid barrier Join
    separating it and no path from the fan-out can reach a sink without
    entering the Join: every parallel path of that fan-out is folded back
    into one token there.
    """
    if not any(node.type == "join" for node in nodes.values()):
        return JoinAnalysis()
    graph = _ExecutableGraph(nodes, edges)
    plans = {
        node_id: plan_join(graph, node_id) for node_id, node in nodes.items() if node.type == "join"
    }
    cleared: set[str] = set()
    for plan in plans.values():
        if plan.kind != "barrier" or plan.violations:
            continue
        for fan_out in plan.separating_fan_outs:
            reachable = graph.forward(fan_out, frozenset({plan.join_id}), through_joins=True)
            if not any(graph.is_sink(t) for t in reachable):
                cleared.add(fan_out)
    return JoinAnalysis(plans, frozenset(cleared))


def _analyze_workflow_joins(workflow: Workflow) -> JoinAnalysis:
    """`analyze_joins` for a workflow that may be half-built: duplicate node
    ids keep the first, edges to unknown nodes are ignored."""
    nodes: dict[str, WorkflowNode] = {}
    for node in workflow.nodes:
        nodes.setdefault(node.id, node)
    return analyze_joins(nodes, workflow.edges)


def find_join_problems(workflow: Workflow) -> list[JoinProblem]:
    """Every Join that cannot wait correctly for its parallel paths.

    Like required branches, these are warnings on save (a half-built canvas
    must save) and refusals at run, publish and resume
    (`validate_workflow_runnable`); `build_graph` also refuses to compile them
    rather than deadlock.
    """
    return _analyze_workflow_joins(workflow).problems


def collect_join_problems(workflow: Workflow) -> list[str]:
    """One user-facing message per problem in `find_join_problems`."""
    return [p.message for p in find_join_problems(workflow)]


def collect_workflow_violations(workflow: Workflow) -> list[str]:
    """Return every structural violation as human-readable strings (empty = valid).

    Unlike validate_workflow_shape (raise-on-first), this gathers all so the
    builder can report them and the agent can rectify them incrementally.
    Check order mirrors the original raise order so `violations[0]` preserves
    the historical first-error message.
    """
    violations: list[str] = []

    # 1. Duplicate node ids.
    nodes: dict[str, WorkflowNode] = {}
    for node in workflow.nodes:
        if node.id in nodes:
            violations.append(f"Node id {node.id!r} is duplicated.")
        else:
            nodes[node.id] = node

    # 2. Start cardinality.
    start_ids = [n.id for n in workflow.nodes if n.type == "start"]
    if len(start_ids) != 1:
        violations.append(
            f"Workflow must contain exactly one start node (found: {len(start_ids)})."
        )

    # 3. End cardinality.
    end_ids = [n.id for n in workflow.nodes if n.type == "end"]
    if not end_ids:
        violations.append("Workflow must contain at least one end node.")

    # 4. Edge endpoints reference known nodes (drop bad edges for later checks).
    known_ids = set(nodes.keys())
    clean_edges: list[WorkflowEdge] = []
    for edge in workflow.edges:
        if edge.source not in known_ids:
            violations.append(
                f"Edge {edge.id!r} references unknown source node id {edge.source!r}."
            )
            continue
        if edge.target not in known_ids:
            violations.append(
                f"Edge {edge.id!r} references unknown target node id {edge.target!r}."
            )
            continue
        clean_edges.append(edge)

    if len(start_ids) != 1:
        return violations
    start_id = start_ids[0]

    # 5. End incoming-edge rule.
    branch_markers = _branch_markers(nodes, clean_edges)
    cleared = analyze_joins(nodes, clean_edges).cleared
    for end_id in end_ids:
        incoming = [
            e
            for e in clean_edges
            if e.target == end_id and nodes[e.source].type not in _VISUAL_ONLY_TYPES
        ]
        if len(incoming) == 1:
            continue
        if len(incoming) > 1 and _fan_out_ancestors_of(end_id, nodes, clean_edges, cleared):
            violations.append(
                f"End node {end_id!r} must have exactly one incoming edge from an "
                f"executable node (found {len(incoming)}): it can be reached by parallel "
                f"paths (an earlier step fans out to several steps), so it would run more "
                f"than once. Give each parallel path its own End step, or merge them with a "
                f"Join first."
            )
            continue
        if len(incoming) > 1:
            # Markers per incoming EDGE, not per source: an edge leaving a
            # conditional node directly (approval --rejected--> End) carries
            # its own (source, branch) pair, which the source node's markers
            # do not contain (those describe how the source was reached).
            edge_markers: list[set[tuple[str, str]]] = []
            for e in incoming:
                marks = set(branch_markers[e.source])
                if nodes[e.source].type in CONDITIONAL_SOURCE_TYPES and e.branch is not None:
                    marks.add((e.source, e.branch))
                edge_markers.append(marks)
            if all(
                _mutually_exclusive(edge_markers[i], edge_markers[j])
                for i in range(len(edge_markers))
                for j in range(i + 1, len(edge_markers))
            ):
                continue
        violations.append(
            f"End node {end_id!r} must have exactly one incoming edge from an "
            f"executable node (found {len(incoming)})."
        )

    # 6. Join outgoing-edge rule.
    for node in nodes.values():
        if node.type != "join":
            continue
        outgoing = [
            e
            for e in clean_edges
            if e.source == node.id and nodes[e.target].type not in _VISUAL_ONLY_TYPES
        ]
        if len(outgoing) != 1:
            violations.append(
                f"Join node {node.id!r} must have exactly one outgoing edge (found {len(outgoing)})."
            )

    # 7. Branch-label rules.
    for edge in clean_edges:
        source = nodes[edge.source]
        if source.type in CONDITIONAL_SOURCE_TYPES:
            if edge.branch is None:
                violations.append(
                    f"Edge {edge.id!r} leaves {source.type} node {source.id!r} "
                    f"but has no branch label."
                )
        elif edge.branch is not None:
            violations.append(
                f"Edge {edge.id!r} has branch={edge.branch!r} but its source "
                f"{source.id!r} is not a conditional node."
            )

    # 8. Reachability.
    outgoing_map: dict[str, list[str]] = {node_id: [] for node_id in nodes}
    for edge in clean_edges:
        if nodes[edge.source].type in _VISUAL_ONLY_TYPES:
            continue
        if nodes[edge.target].type in _VISUAL_ONLY_TYPES:
            continue
        outgoing_map[edge.source].append(edge.target)
    reachable: set[str] = set()
    queue: deque[str] = deque([start_id])
    while queue:
        current = queue.popleft()
        if current in reachable:
            continue
        reachable.add(current)
        queue.extend(outgoing_map[current])
    for node_id, node in nodes.items():
        if node.type in _VISUAL_ONLY_TYPES:
            continue
        if node_id not in reachable:
            violations.append(
                f"Node {node_id!r} ({node.type!r}) is unreachable from the start node."
            )

    return violations


def validate_workflow_shape(workflow: Workflow) -> None:
    """Validate a workflow's structural invariants. Raises WorkflowValidationError on failure."""
    violations = collect_workflow_violations(workflow)
    if violations:
        raise WorkflowValidationError(violations[0])


CONDITIONAL_SOURCE_TYPES = {"if-else", "while", "user-approval", "decision"}

# Names shown to the user for each conditional node type (the Designer's palette wording).
_CONDITIONAL_DISPLAY_NAMES = {
    "if-else": "If / Else",
    "while": "While",
    "user-approval": "User approval",
    "decision": "Decision",
}


def required_branches(node: WorkflowNode) -> set[str] | None:
    """Branch labels that must each have an outgoing edge, or None if `node`
    is not a conditional source.

    The single definition of "which exits does this node need": build_graph
    (via _branch_mapping) and collect_missing_branches both use it, so the
    save-time warning, the run gate and the compiler cannot disagree.
    """
    if isinstance(node, IfElseNode):
        return {"true", "false"}
    if isinstance(node, WhileNode):
        return {"body", "exit"}
    if isinstance(node, UserApprovalNode):
        return {"approved", "rejected"}
    if isinstance(node, DecisionNode):
        if node.data.mode == "binary":
            return {"true", "false"}
        assert node.data.options is not None
        return {o.label for o in node.data.options}
    return None


class MissingBranch(NamedTuple):
    """A required branch with no outgoing edge."""

    node_id: str
    branch: str
    message: str


def find_missing_branches(workflow: Workflow) -> list[MissingBranch]:
    """Every required branch with no outgoing edge, with a user-facing message.

    Only absent branches are reported; mislabelled or duplicate edges stay the
    job of collect_workflow_violations.
    """
    connected: dict[str, set[str]] = {}
    for edge in workflow.edges:
        if edge.branch is not None:
            connected.setdefault(edge.source, set()).add(edge.branch)
    found: list[MissingBranch] = []
    for node in workflow.nodes:
        required = required_branches(node)
        if required is None:
            continue
        shown = _quoted_label(node)
        kind = _CONDITIONAL_DISPLAY_NAMES.get(node.type, node.type)
        for branch in sorted(required - connected.get(node.id, set())):
            found.append(
                MissingBranch(
                    node.id,
                    branch,
                    f'{kind} step {shown} has no "{branch}" path. '
                    f'Connect its "{branch}" handle to a step (for example End).',
                )
            )
    return found


def collect_missing_branches(workflow: Workflow) -> list[str]:
    """One user-facing message per required branch with no outgoing edge.

    Saving a half-built canvas must succeed, so this is reported as a warning
    on save; running or publishing refuses on it (validate_workflow_runnable),
    and the AI Builder treats it as an issue to repair.
    """
    return [m.message for m in find_missing_branches(workflow)]


def validate_workflow_runnable(workflow: Workflow) -> None:
    """Raise WorkflowValidationError if the workflow cannot run as drawn.

    Gate for running, publishing and resuming a paused run. It enforces every
    structural rule (`collect_workflow_violations`, so a canvas edited into an
    invalid shape is refused with the same user-facing message instead of
    failing inside `build_graph`), then the required-branch and Join checks
    (`collect_join_problems`: a Join that would wait forever or run steps
    twice). NOT part of
    validate_workflow_shape: that one runs on every canvas save, and a
    half-built canvas must save.
    """
    violations = collect_workflow_violations(workflow)
    if violations:
        raise WorkflowValidationError(violations[0])
    problems = [*collect_missing_branches(workflow), *collect_join_problems(workflow)]
    if problems:
        raise WorkflowValidationError(" ".join(problems))


def _branch_mapping(
    node: WorkflowNode,
    edges: list[WorkflowEdge],
    required: set[str],
) -> dict[str, str]:
    """Build a {branch: target_node_id} mapping for a conditional source.

    Raises WorkflowValidationError if any edge has no branch label, duplicate
    branch labels exist, or the edge set doesn't exactly match
    `required`.
    """
    out_edges = [e for e in edges if e.source == node.id]
    seen: dict[str, str] = {}
    for e in out_edges:
        if e.branch is None:
            raise WorkflowValidationError(
                f"Edge {e.id!r} leaving {node.type} node {node.id!r} has no branch label."
            )
        if e.branch in seen:
            raise WorkflowValidationError(
                f"Duplicate branch {e.branch!r} on edges leaving {node.id!r}."
            )
        if e.branch not in required:
            raise WorkflowValidationError(
                f"Edge {e.id!r} leaving {node.type} node {node.id!r} has "
                f"unexpected branch {e.branch!r}; allowed: {sorted(required)}."
            )
        seen[e.branch] = e.target
    missing = required - seen.keys()
    if missing:
        raise WorkflowValidationError(
            f"{node.type} node {node.id!r} branches mismatch: "
            f"missing={sorted(missing)}, got={sorted(seen.keys())}."
        )
    return seen


def _route_if_else(node: IfElseNode) -> Callable[[WorkflowStateDict], str]:
    """Router closure for an if-else node.  Evaluates the condition fresh on
    each traversal (not reading from node_results — that can be stale on retry).
    Returns the fallback 'false' on EvalError to avoid wedging the graph.
    """
    expr = node.data.condition or ""

    def _router(state: WorkflowStateDict) -> str:
        if not expr:
            return "false"
        try:
            result = evaluate(expr, state)
        except EvalError:
            return "false"
        return "true" if result else "false"

    return _router


def _route_while(node: WhileNode) -> Callable[[WorkflowStateDict], str]:
    """Router closure for a while node.  Same logic as _route_if_else but
    returns 'body' / 'exit' branch keys.  The executor handles the
    max-iterations check before this router runs.
    """
    expr = node.data.condition or ""

    def _router(state: WorkflowStateDict) -> str:
        if not expr:
            return "exit"
        try:
            result = evaluate(expr, state)
        except EvalError:
            return "exit"
        return "body" if result else "exit"

    return _router


def _route_user_approval(
    node: UserApprovalNode,
) -> Callable[[WorkflowStateDict], str]:
    """Router for user-approval: reads _approval_<node_id> from variables."""

    def _router(state: WorkflowStateDict) -> str:
        variables = state.get("variables") or {}
        decision = variables.get(f"_approval_{node.id}")
        return "approved" if decision == "approved" else "rejected"

    return _router


def _route_decision(node: DecisionNode) -> Callable[[WorkflowStateDict], str]:
    """Router closure for a decision node. Reads the already-computed result
    from node_results (an LLM/judgment call isn't free or idempotent enough
    to repeat during routing, unlike if-else's simpleeval re-evaluation).
    Falls back to the first branch on a missing/malformed result so a
    provider hiccup can't wedge the graph.
    """

    def _fallback() -> str:
        if node.data.mode == "binary":
            return "false"
        assert node.data.options is not None
        return node.data.options[0].label

    def _router(state: WorkflowStateDict) -> str:
        node_results = state.get("node_results") or {}
        result = node_results.get(node.id)
        if not result or "output" not in result:
            return _fallback()
        decision = result["output"].get("decision")
        if node.data.mode == "binary":
            return "true" if decision else "false"
        assert node.data.options is not None
        labels = {o.label for o in node.data.options}
        if isinstance(decision, str) and decision in labels:
            return decision
        return _fallback()

    return _router


_ROUTER_FACTORIES: dict[str, Callable[[Any], Callable[[WorkflowStateDict], str]]] = {
    "if-else": _route_if_else,
    "while": _route_while,
    "user-approval": _route_user_approval,
    "decision": _route_decision,
}


async def _join_lane_relay(_state: WorkflowStateDict) -> dict[str, Any]:
    """Barrier lane member that stands in for several exclusive incoming edges
    (or one conditional branch) of a Join. Writes no state and, being added
    without the events wrapper, emits no events: users never see it."""
    return {}


def build_graph(
    workflow: Workflow,
    checkpointer: BaseCheckpointSaver[Any],
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Validate the workflow, compile it into a LangGraph StateGraph, return the compiled graph.

    LangGraph's generic type parameters (StateT, InputT, OutputT, RawInputT) are
    parameterized with Any here — the state type `WorkflowStateDict` is passed
    to the StateGraph constructor at runtime and LangGraph infers reducers from
    it, but pyright can't bridge the runtime-inferred type to the compile-time
    generics. Any is the honest annotation rather than fighting the type system.

    A Join is wired per `plan_join`: plain edges when its inputs are
    alternatives, otherwise one barrier (`add_edge([lanes...], join)`) that
    waits for every parallel lane exactly once. A Join that cannot wait
    correctly is refused here rather than compiled into a deadlock.
    """
    workflow = workflow.model_copy(update={"edges": dedupe_edges(workflow.edges)})
    validate_workflow_shape(workflow)
    nodes_by_id = {node.id: node for node in workflow.nodes}
    join_plans = analyze_joins(nodes_by_id, workflow.edges)
    if join_plans.problems:
        raise WorkflowValidationError(join_plans.problems[0].message)

    builder: StateGraph[Any, Any, Any, Any] = StateGraph(WorkflowStateDict)

    for node in workflow.nodes:
        if node.type in _VISUAL_ONLY_TYPES:
            continue  # visual-only; skipped at build time per OAB behavior
        executor = build_executor(node)  # may raise NotImplementedError
        arun_with_events = wrap_executor_with_events(executor, node)
        builder.add_node(node.id, arun_with_events)  # pyright: ignore[reportUnknownMemberType,reportArgumentType]

    # Barrier Joins: each lane contributes one member, and its edges are
    # redirected into it. A lane that is a single plain edge uses its source
    # node itself; any other lane (several exclusive edges, or an edge leaving
    # a conditional branch) gets a no-op relay that the branch or edges
    # target instead, because a barrier waits on a node finishing, not on
    # one of its branches being taken.
    relayed: dict[_EdgeKey, str] = {}
    barrier_edges: set[_EdgeKey] = set()
    barriers: list[tuple[list[str], str]] = []
    taken_ids = set(nodes_by_id)
    for plan in join_plans.plans.values():
        if plan.kind != "barrier":
            continue
        members: list[str] = []
        for index, lane in enumerate(plan.lanes):
            first = lane[0]
            if len(lane) == 1 and nodes_by_id[first.source].type not in CONDITIONAL_SOURCE_TYPES:
                members.append(first.source)
                barrier_edges.add((first.source, first.target, first.branch))
                continue
            relay_id = f"{JOIN_RELAY_PREFIX}{plan.join_id}__{index}"
            while relay_id in taken_ids:
                relay_id += "_"
            taken_ids.add(relay_id)
            builder.add_node(relay_id, _join_lane_relay)  # pyright: ignore[reportUnknownMemberType,reportArgumentType]
            members.append(relay_id)
            for edge in lane:
                relayed[(edge.source, edge.target, edge.branch)] = relay_id
        barriers.append((members, plan.join_id))

    # Emit normal edges first; conditional edges handled in a second pass.
    for edge in workflow.edges:
        source_node = nodes_by_id[edge.source]
        if source_node.type in CONDITIONAL_SOURCE_TYPES:
            continue  # handled by conditional-edges pass below
        # Skip edges whose source or target is a visual-only node
        if source_node.type in _VISUAL_ONLY_TYPES:
            continue
        if nodes_by_id[edge.target].type in _VISUAL_ONLY_TYPES:
            continue
        key = (edge.source, edge.target, edge.branch)
        if key in barrier_edges:
            continue  # the barrier below carries this edge
        builder.add_edge(edge.source, relayed.get(key, edge.target))  # pyright: ignore[reportUnknownMemberType]
    for members, join_id in barriers:
        builder.add_edge(members, join_id)  # pyright: ignore[reportUnknownMemberType]

    # Conditional edges pass — AFTER normal edges + all nodes are in place.
    for node in workflow.nodes:
        router_factory = _ROUTER_FACTORIES.get(node.type)
        if router_factory is None:
            continue
        branch_targets = _branch_mapping(
            node, list(workflow.edges), required_branches(node) or set()
        )
        mapping = cast(
            "dict[Hashable, str]",
            {
                branch: relayed.get((node.id, target, branch), target)
                for branch, target in branch_targets.items()
            },
        )
        builder.add_conditional_edges(node.id, router_factory(node), mapping)  # pyright: ignore[reportUnknownMemberType]

    start_id = next(n.id for n in workflow.nodes if n.type == "start")
    end_ids = [n.id for n in workflow.nodes if n.type == "end"]

    builder.add_edge(START, start_id)
    for end_id in end_ids:
        builder.add_edge(end_id, END)

    return builder.compile(checkpointer=checkpointer)  # pyright: ignore[reportUnknownMemberType]


__all__ = [
    "JOIN_RELAY_PREFIX",
    "JoinAnalysis",
    "JoinPlan",
    "JoinProblem",
    "MissingBranch",
    "WorkflowValidationError",
    "analyze_joins",
    "build_graph",
    "collect_join_problems",
    "collect_missing_branches",
    "collect_workflow_violations",
    "find_join_problems",
    "find_missing_branches",
    "plan_join",
    "required_branches",
    "validate_workflow_runnable",
    "validate_workflow_shape",
]
