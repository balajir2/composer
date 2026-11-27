"""A Join waits for every parallel path, exactly once.

LangGraph fires a node in every superstep in which any plain-edge predecessor
finishes, so a Join fed by parallel paths of different lengths used to run
(and everything after it) once per arrival. `build_graph` now wires such a
Join as a barrier and keeps plain edges for a Join whose inputs are
alternatives. Every shape the validator accepts is compiled AND run here.
"""

from collections import Counter
from typing import Any

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from src.engine.graph_builder import (
    WorkflowValidationError,
    build_graph,
    collect_workflow_violations,
    find_join_problems,
    validate_workflow_runnable,
    validate_workflow_shape,
)
from src.engine.state import initial_state
from src.engine.workflow import Workflow


def _n(node_id: str, kind: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": kind,
        "position": {"x": 0, "y": 0},
        "data": {"label": node_id, **(data or {})},
    }


def _ss(node_id: str) -> dict[str, Any]:
    return _n(node_id, "set-state", {"stateKey": f"ran_{node_id}", "stateValue": "yes"})


def _ie(node_id: str, condition: str) -> dict[str, Any]:
    return _n(node_id, "if-else", {"condition": condition})


def _ua(node_id: str) -> dict[str, Any]:
    return _n(node_id, "user-approval", {"approvalMessage": "ok?"})


def _while(node_id: str, bound: int) -> dict[str, Any]:
    cond = f'"{node_id}" not in node_results or node_results["{node_id}"]["input"]["iteration"] < {bound}'
    return _n(node_id, "while", {"condition": cond, "maxIterations": 10})


def _ed(source: str, target: str, branch: str | None = None) -> dict[str, Any]:
    edge: dict[str, Any] = {"id": f"{source}-{target}-{branch}", "source": source, "target": target}
    if branch is not None:
        edge["branch"] = branch
    return edge


def _wf(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> Workflow:
    return Workflow.model_validate({"name": "T", "nodes": nodes, "edges": edges})


S = _n("s", "start")
END = _n("end", "end")
J = _n("j", "join")


class _Run:
    def __init__(self, counts: Counter[str], values: dict[str, Any], paused: tuple[str, ...]):
        self.counts = counts
        self.values = values
        self.paused = paused

    @property
    def ran(self) -> set[str]:
        variables = self.values.get("variables") or {}
        return {k[4:] for k in variables if k.startswith("ran_")}


async def _run(wf: Workflow, resume: str | None = None, thread: str = "t") -> _Run:
    graph = build_graph(wf, MemorySaver())
    config: Any = {"configurable": {"thread_id": thread}, "recursion_limit": 80}
    counts: Counter[str] = Counter()

    async def drive(payload: Any) -> None:
        async for chunk in graph.astream(payload, config, stream_mode="updates"):
            for key in chunk:
                if key != "__interrupt__":
                    counts[key] += 1

    await drive(initial_state("x"))
    snap = await graph.aget_state(config)
    if snap.next and resume is not None:
        await drive(Command(resume=resume))
        snap = await graph.aget_state(config)
    return _Run(counts, snap.values, tuple(snap.next))


def _real(counts: Counter[str]) -> dict[str, int]:
    return {k: v for k, v in counts.items() if not k.startswith("__join_lane__")}


# name -> (workflow, expected ran_* variables, node ids that must each run exactly once)
def _cases() -> dict[str, tuple[Workflow, set[str], set[str]]]:
    cases: dict[str, tuple[Workflow, set[str], set[str]]] = {}
    cases["unbalanced_single_end_edge"] = (
        _wf(
            [S, _ss("p"), _ss("a"), _ss("a2"), _ss("b"), J, END],
            [
                _ed("s", "p"),
                _ed("p", "a"),
                _ed("a", "a2"),
                _ed("a2", "j"),
                _ed("p", "b"),
                _ed("b", "j"),
                _ed("j", "end"),
            ],
        ),
        {"p", "a", "a2", "b"},
        {"j", "end"},
    )
    for c in ("True", "False"):
        cases[f"fanout_join_if_both_end[{c}]"] = (
            _wf(
                [S, _ss("p"), _ss("a"), _ss("a2"), _ss("b"), J, _ie("c", c), END],
                [
                    _ed("s", "p"),
                    _ed("p", "a"),
                    _ed("a", "a2"),
                    _ed("a2", "j"),
                    _ed("p", "b"),
                    _ed("b", "j"),
                    _ed("j", "c"),
                    _ed("c", "end", "true"),
                    _ed("c", "end", "false"),
                ],
            ),
            {"p", "a", "a2", "b"},
            {"j", "c", "end"},
        )
        lane_ran = {"p", "a", "b", "b2", "b3"} | ({"x"} if c == "True" else set())
        cases[f"fanout_lane_with_if[{c}]"] = (
            _wf(
                [
                    S,
                    _ss("p"),
                    _ss("a"),
                    _ie("c", c),
                    _ss("x"),
                    _ss("b"),
                    _ss("b2"),
                    _ss("b3"),
                    J,
                    END,
                ],
                [
                    _ed("s", "p"),
                    _ed("p", "a"),
                    _ed("p", "b"),
                    _ed("a", "c"),
                    _ed("c", "x", "true"),
                    _ed("x", "j"),
                    _ed("c", "j", "false"),
                    _ed("b", "b2"),
                    _ed("b2", "b3"),
                    _ed("b3", "j"),
                    _ed("j", "end"),
                ],
            ),
            lane_ran,
            {"j", "end"},
        )
        inner = {"f", "a", "a2", "b"} if c == "True" else {"d"}
        cases[f"mixed_fixed_with_inner_join[{c}]"] = (
            _wf(
                [
                    S,
                    _ie("c", c),
                    _ss("f"),
                    _ss("a"),
                    _ss("a2"),
                    _ss("b"),
                    _ss("d"),
                    _n("j1", "join"),
                    J,
                    END,
                ],
                [
                    _ed("s", "c"),
                    _ed("c", "f", "true"),
                    _ed("f", "a"),
                    _ed("a", "a2"),
                    _ed("f", "b"),
                    _ed("a2", "j1"),
                    _ed("b", "j1"),
                    _ed("j1", "j"),
                    _ed("c", "d", "false"),
                    _ed("d", "j"),
                    _ed("j", "end"),
                ],
            ),
            inner,
            {"j", "end"} | ({"j1"} if c == "True" else set()),
        )
    cases["third_lane_own_end"] = (
        _wf(
            [S, _ss("p"), _ss("a"), _ss("a2"), _ss("b"), _ss("z"), J, END, _n("end2", "end")],
            [
                _ed("s", "p"),
                _ed("p", "a"),
                _ed("a", "a2"),
                _ed("a2", "j"),
                _ed("p", "b"),
                _ed("b", "j"),
                _ed("p", "z"),
                _ed("z", "end2"),
                _ed("j", "end"),
            ],
        ),
        {"p", "a", "a2", "b", "z"},
        {"j", "end", "end2"},
    )
    cases["nested_joins"] = (
        _wf(
            [
                S,
                _ss("p"),
                _ss("a"),
                _ss("b"),
                _ss("c"),
                _ss("c2"),
                _ss("c3"),
                _n("j1", "join"),
                _n("j2", "join"),
                END,
            ],
            [
                _ed("s", "p"),
                _ed("p", "a"),
                _ed("p", "b"),
                _ed("p", "c"),
                _ed("a", "j1"),
                _ed("b", "j1"),
                _ed("j1", "j2"),
                _ed("c", "c2"),
                _ed("c2", "c3"),
                _ed("c3", "j2"),
                _ed("j2", "end"),
            ],
        ),
        {"p", "a", "b", "c", "c2", "c3"},
        {"j1", "j2", "end"},
    )
    cases["nested_fanout_one_join"] = (
        _wf(
            [S, _ss("p"), _ss("a"), _ss("x"), _ss("y"), _ss("y2"), _ss("b"), J, END],
            [
                _ed("s", "p"),
                _ed("p", "a"),
                _ed("p", "b"),
                _ed("a", "x"),
                _ed("a", "y"),
                _ed("y", "y2"),
                _ed("x", "j"),
                _ed("y2", "j"),
                _ed("b", "j"),
                _ed("j", "end"),
            ],
        ),
        {"p", "a", "x", "y", "y2", "b"},
        {"j", "end"},
    )
    cases["fan_out_direct_into_join"] = (
        _wf(
            [S, _ss("p"), _ss("a"), _ss("a2"), J, END],
            [
                _ed("s", "p"),
                _ed("p", "j"),
                _ed("p", "a"),
                _ed("a", "a2"),
                _ed("a2", "j"),
                _ed("j", "end"),
            ],
        ),
        {"p", "a", "a2"},
        {"j", "end"},
    )
    cases["start_fans_out_unbalanced"] = (
        _wf(
            [S, _ss("a"), _ss("a2"), _ss("b"), J, END],
            [
                _ed("s", "a"),
                _ed("a", "a2"),
                _ed("a2", "j"),
                _ed("s", "b"),
                _ed("b", "j"),
                _ed("j", "end"),
            ],
        ),
        {"a", "a2", "b"},
        {"j", "end"},
    )
    cases["note_edge_into_join_ignored"] = (
        _wf(
            [S, _ss("p"), _ss("a"), _ss("a2"), _ss("b"), _n("nt", "note"), J, END],
            [
                _ed("s", "p"),
                _ed("p", "a"),
                _ed("a", "a2"),
                _ed("a2", "j"),
                _ed("p", "b"),
                _ed("b", "j"),
                _ed("nt", "j"),
                _ed("j", "end"),
            ],
        ),
        {"p", "a", "a2", "b"},
        {"j", "end"},
    )
    cases["balanced_fanout_join"] = (
        _wf(
            [S, _ss("a"), _ss("b"), J, END],
            [_ed("s", "a"), _ed("s", "b"), _ed("a", "j"), _ed("b", "j"), _ed("j", "end")],
        ),
        {"a", "b"},
        {"j", "end"},
    )
    return cases


@pytest.mark.parametrize("name", list(_cases()))
async def test_accepted_join_topology_runs_join_and_end_once(name: str) -> None:
    wf, expected_ran, once = _cases()[name]
    assert collect_workflow_violations(wf) == []
    assert find_join_problems(wf) == []
    validate_workflow_runnable(wf)
    run = await _run(wf, thread=name)
    counts = _real(run.counts)
    for node_id in once:
        assert counts.get(node_id) == 1, f"{node_id} ran {counts.get(node_id)}x: {counts}"
    assert run.ran == expected_ran
    assert run.paused == ()


async def test_third_lane_own_end_each_lane_output_present() -> None:
    wf, _, _ = _cases()["third_lane_own_end"]
    run = await _run(wf)
    assert set((run.values["final_outputs"]).keys()) == {"end", "end2"}


# --- Loops -----------------------------------------------------------------


async def test_while_body_fanout_join_runs_join_once_per_iteration() -> None:
    wf = _wf(
        [S, _while("w", 3), _ss("p"), _ss("a"), _ss("a2"), _ss("b"), J, END],
        [
            _ed("s", "w"),
            _ed("w", "p", "body"),
            _ed("p", "a"),
            _ed("a", "a2"),
            _ed("a2", "j"),
            _ed("p", "b"),
            _ed("b", "j"),
            _ed("j", "w"),
            _ed("w", "end", "exit"),
        ],
    )
    assert collect_workflow_violations(wf) == []
    run = await _run(wf)
    counts = _real(run.counts)
    assert counts["p"] >= 2
    assert counts["j"] == counts["p"] == counts["a"] == counts["a2"] == counts["b"]
    assert counts["end"] == 1
    assert run.paused == ()


async def test_while_inside_lane_runs_join_once() -> None:
    wf = _wf(
        [S, _ss("p"), _while("w", 4), _ss("body"), _ss("b"), J, END],
        [
            _ed("s", "p"),
            _ed("p", "w"),
            _ed("w", "body", "body"),
            _ed("body", "w"),
            _ed("w", "j", "exit"),
            _ed("p", "b"),
            _ed("b", "j"),
            _ed("j", "end"),
        ],
    )
    run = await _run(wf)
    counts = _real(run.counts)
    assert counts["body"] >= 2
    assert counts["j"] == 1
    assert counts["end"] == 1
    assert run.ran == {"p", "body", "b"}


async def test_join_reentered_by_loop_keeps_plain_wiring() -> None:
    wf = _wf(
        [S, J, _while("w", 3), _ss("y"), END],
        [
            _ed("s", "j"),
            _ed("j", "w"),
            _ed("w", "y", "body"),
            _ed("y", "j"),
            _ed("w", "end", "exit"),
        ],
    )
    assert _join_channels(build_graph(wf, MemorySaver())) == []
    run = await _run(wf)
    counts = _real(run.counts)
    assert counts["y"] >= 2
    assert counts["j"] == counts["y"] + 1  # once per entry
    assert counts["end"] == 1


# --- Approval --------------------------------------------------------------


def _approval_wf() -> Workflow:
    return _wf(
        [S, _ss("p"), _ua("ua"), _ss("ok"), _ss("b"), _ss("b2"), J, END],
        [
            _ed("s", "p"),
            _ed("p", "ua"),
            _ed("ua", "ok", "approved"),
            _ed("ok", "j"),
            _ed("ua", "j", "rejected"),
            _ed("p", "b"),
            _ed("b", "b2"),
            _ed("b2", "j"),
            _ed("j", "end"),
        ],
    )


@pytest.mark.parametrize("decision", ["approved", "rejected"])
async def test_approval_in_lane_join_and_end_run_once(decision: str) -> None:
    run = await _run(_approval_wf(), resume=decision)
    counts = _real(run.counts)
    assert counts["j"] == 1
    assert counts["end"] == 1
    assert run.paused == ()
    assert {"p", "b", "b2"} <= run.ran
    assert ("ok" in run.ran) == (decision == "approved")


@pytest.mark.parametrize("decision", ["approved", "rejected"])
async def test_approval_pause_with_barrier_partly_filled(decision: str) -> None:
    """The other lane has already reached the Join when the run pauses."""
    wf = _wf(
        [S, _ss("p"), _ss("x1"), _ss("x2"), _ua("ua"), _ss("ok"), _ss("b"), J, END],
        [
            _ed("s", "p"),
            _ed("p", "x1"),
            _ed("x1", "x2"),
            _ed("x2", "ua"),
            _ed("ua", "ok", "approved"),
            _ed("ok", "j"),
            _ed("ua", "j", "rejected"),
            _ed("p", "b"),
            _ed("b", "j"),
            _ed("j", "end"),
        ],
    )
    graph = build_graph(wf, MemorySaver())
    config: Any = {"configurable": {"thread_id": "pp"}, "recursion_limit": 80}
    before: Counter[str] = Counter()
    async for chunk in graph.astream(initial_state("x"), config, stream_mode="updates"):
        before.update(k for k in chunk if k != "__interrupt__")
    snap = await graph.aget_state(config)
    assert snap.next == ("ua",)
    assert "b" in before
    assert "j" not in before  # the barrier is partly filled, not fired
    after: Counter[str] = Counter()
    async for chunk in graph.astream(Command(resume=decision), config, stream_mode="updates"):
        after.update(k for k in chunk if k != "__interrupt__")
    assert _real(after)["j"] == 1
    assert _real(after)["end"] == 1
    assert (await graph.aget_state(config)).next == ()


# --- Refusals: save warns, run blocks --------------------------------------


def _refused() -> dict[str, tuple[Workflow, str, str, str]]:
    """name -> (workflow, warning code, node id, message fragment)."""
    return {
        "mixed_parallel_and_alternative": (
            _wf(
                [S, _ie("c", "True"), _ss("f"), _ss("a"), _ss("b"), _ss("d"), J, END],
                [
                    _ed("s", "c"),
                    _ed("c", "f", "true"),
                    _ed("f", "a"),
                    _ed("f", "b"),
                    _ed("a", "j"),
                    _ed("b", "j"),
                    _ed("c", "d", "false"),
                    _ed("d", "j"),
                    _ed("j", "end"),
                ],
            ),
            "join_mixed_inputs",
            "j",
            'Join step "j" mixes parallel paths with an alternative path: the steps "a" and '
            '"b" run in parallel, but "d" runs instead of one of them. Merge the parallel '
            "paths with their own Join first, then connect that Join and the alternative path "
            "to this one.",
        ),
        "lane_escapes_to_end": (
            _wf(
                [S, _ss("p"), _ss("a"), _ie("c", "False"), _ss("b"), J, END, _n("end2", "end")],
                [
                    _ed("s", "p"),
                    _ed("p", "a"),
                    _ed("a", "c"),
                    _ed("c", "j", "true"),
                    _ed("c", "end2", "false"),
                    _ed("p", "b"),
                    _ed("b", "j"),
                    _ed("j", "end"),
                ],
            ),
            "join_lane_escapes",
            "j",
            'Join step "j" waits for every parallel path, but the path through "a" can finish '
            'at "end2" without reaching it, so the Join would wait forever. Connect that path '
            "to the Join, or end it after the Join.",
        ),
        "approval_rejected_escapes": (
            _wf(
                [S, _ss("p"), _ua("ua"), _ss("ok"), _ss("b"), J, END, _n("end2", "end")],
                [
                    _ed("s", "p"),
                    _ed("p", "ua"),
                    _ed("ua", "ok", "approved"),
                    _ed("ok", "j"),
                    _ed("ua", "end2", "rejected"),
                    _ed("p", "b"),
                    _ed("b", "j"),
                    _ed("j", "end"),
                ],
            ),
            "join_lane_escapes",
            "j",
            'can finish at "end2" without reaching it',
        ),
        "collision_before_join": (
            _wf(
                [S, _ss("p"), _ss("a"), _ss("b"), _ss("m"), _ss("c"), J, END],
                [
                    _ed("s", "p"),
                    _ed("p", "a"),
                    _ed("p", "b"),
                    _ed("p", "c"),
                    _ed("a", "m"),
                    _ed("b", "m"),
                    _ed("m", "j"),
                    _ed("c", "j"),
                    _ed("j", "end"),
                ],
            ),
            "join_collision",
            "m",
            'Step "m" is reached by more than one parallel path before Join step "j", so it '
            "would run more than once. Connect those paths to a Join first.",
        ),
    }


@pytest.mark.parametrize("name", list(_refused()))
def test_join_problem_warns_on_save(name: str) -> None:
    wf, code, node_id, fragment = _refused()[name]
    assert collect_workflow_violations(wf) == []
    validate_workflow_shape(wf)
    (problem,) = find_join_problems(wf)
    assert problem.code == code
    assert problem.node_id == node_id
    assert fragment in problem.message


@pytest.mark.parametrize("name", list(_refused()))
def test_join_problem_blocks_run(name: str) -> None:
    wf, _, _, fragment = _refused()[name]
    with pytest.raises(WorkflowValidationError) as gate:
        validate_workflow_runnable(wf)
    assert fragment in str(gate.value)
    with pytest.raises(WorkflowValidationError) as compiled:
        build_graph(wf, MemorySaver())
    assert fragment in str(compiled.value)


def test_separate_lane_outside_the_region_is_not_an_escape() -> None:
    wf, _, _ = _cases()["third_lane_own_end"]
    assert find_join_problems(wf) == []


def test_join_problem_label_differs_from_id() -> None:
    wf, _, _, _ = _refused()["lane_escapes_to_end"]
    nodes = [
        {**n, "data": {**n["data"], "label": "Merge"}} if n["id"] == "j" else n
        for n in _node_dicts(wf)
    ]
    labelled = _wf(nodes, _edge_dicts(wf))
    (problem,) = find_join_problems(labelled)
    assert problem.message.startswith('Join step "Merge" (j) waits')


def _node_dicts(wf: Workflow) -> list[dict[str, Any]]:
    return [n.model_dump(by_alias=True, mode="json") for n in wf.nodes]


def _edge_dicts(wf: Workflow) -> list[dict[str, Any]]:
    return [e.model_dump(by_alias=True, mode="json", exclude_none=True) for e in wf.edges]


# --- Wiring ----------------------------------------------------------------


def _join_channels(graph: Any) -> list[set[str]]:
    out: list[set[str]] = []
    for name in graph.channels:
        if isinstance(name, str) and name.startswith("join:"):
            _, members, _target = name.split(":")
            out.append(set(members.split("+")))
    return out


def _relays(graph: Any) -> list[str]:
    return sorted(n for n in graph.nodes if n.startswith("__join_lane__"))


def test_merge_join_has_no_barrier_channel() -> None:
    wf = _wf(
        [S, _ie("c", "True"), _ss("x"), _ss("y"), J, END],
        [
            _ed("s", "c"),
            _ed("c", "x", "true"),
            _ed("x", "j"),
            _ed("c", "j", "false"),
            _ed("j", "y"),
            _ed("y", "end"),
        ],
    )
    graph = build_graph(wf, MemorySaver())
    assert _join_channels(graph) == []
    assert _relays(graph) == []


def test_barrier_join_has_one_channel_and_no_relay_for_plain_lanes() -> None:
    wf, _, _ = _cases()["unbalanced_single_end_edge"]
    graph = build_graph(wf, MemorySaver())
    assert _join_channels(graph) == [{"a2", "b"}]
    assert _relays(graph) == []


def test_barrier_join_relays_only_for_conditional_lanes() -> None:
    wf, _, _ = _cases()["fanout_lane_with_if[True]"]
    graph = build_graph(wf, MemorySaver())
    (members,) = _join_channels(graph)
    assert "b3" in members
    assert len(members) == 2
    assert _relays(graph) == ["__join_lane__j__0"] or _relays(graph) == ["__join_lane__j__1"]
    assert members - {"b3"} == set(_relays(graph))


def test_relay_id_avoids_collision_with_a_user_node() -> None:
    wf, _, _ = _cases()["fanout_lane_with_if[True]"]
    nodes = [*_node_dicts(wf), _n("__join_lane__j__0", "note"), _n("__join_lane__j__1", "note")]
    graph = build_graph(_wf(nodes, _edge_dicts(wf)), MemorySaver())
    assert all(r.endswith("_") for r in _relays(graph))


async def test_relay_lane_emits_no_events_and_no_node_result() -> None:
    from unittest.mock import AsyncMock, patch

    from src.engine.context import set_current_event_bus, set_current_execution_id
    from src.engine.events import ExecutionEvent

    class _Store:
        def __init__(self) -> None:
            self.events: list[ExecutionEvent] = []

        async def append(self, event: ExecutionEvent) -> int:
            self.events.append(event)
            return len(self.events)

    wf, _, _ = _cases()["fanout_lane_with_if[False]"]  # the lane runs through the relay
    store = _Store()
    set_current_execution_id("e1")
    set_current_event_bus(store)  # pyright: ignore[reportArgumentType]
    try:
        with patch("src.engine.events_wrapper.notify_execution_event", AsyncMock()):
            run = await _run(wf)
    finally:
        set_current_execution_id(None)
        set_current_event_bus(None)

    assert any(k.startswith("__join_lane__") for k in run.counts)  # the relay did run
    node_ids = {e.payload.get("nodeId") for e in store.events}
    assert node_ids  # events were captured
    assert not any(str(n).startswith("__join_lane__") for n in node_ids)
    assert not any(k.startswith("__join_lane__") for k in run.values["node_results"])
    assert not any(k.startswith("__join_lane__") for k in run.values["final_outputs"])
    assert not any(k.startswith("__join_lane__") for k in (run.values.get("variables") or {}))
