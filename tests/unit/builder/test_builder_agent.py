"""Tests for the builder agent loop and BuildSession.

Uses a scripted fake model (returns a canned AIMessage via ainvoke; bind_tools
is a no-op returning self) so the deterministic branches — plan, approve,
reject, diagnose — can be exercised without a provider-backed tool-call round
trip.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from langchain_core.messages import AIMessage, HumanMessage

from src.builder.builder_agent import (
    MAX_REPAIR_ROUNDS,
    REPAIR_ROUND_ITERATIONS,
    BuildSession,
    run_builder_turn,
)
from src.db.repos import executions as executions_repo
from src.engine.graph_builder import collect_workflow_violations
from src.engine.workflow import (
    AgentNode,
    AgentNodeData,
    EndNode,
    EndNodeData,
    Position,
    StartNode,
    StartNodeData,
    Workflow,
    WorkflowEdge,
    WorkflowNode,
)
from tests.unit.db_fakes import RecordingSessionmaker, assert_no_network_inside_session

if TYPE_CHECKING:
    import pytest
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
        StartNode(id="s", type="start", position=Position(x=0, y=0), data=StartNodeData(label="S")),
        EndNode(id="e", type="end", position=Position(x=400, y=0), data=EndNodeData(label="E")),
    ]
    edges: list[WorkflowEdge] = [WorkflowEdge(id="e1", source="s", target="e")]
    return Workflow(name="t", nodes=nodes, edges=edges)


def _origin_workflow() -> Workflow:
    """Three chained nodes all at the origin — proves layout repositions them."""
    nodes: list[WorkflowNode] = [
        StartNode(id="s", type="start", position=Position(x=0, y=0), data=StartNodeData(label="S")),
        AgentNode(id="a", type="agent", position=Position(x=0, y=0), data=AgentNodeData(label="A")),
        EndNode(id="e", type="end", position=Position(x=0, y=0), data=EndNodeData(label="E")),
    ]
    edges: list[WorkflowEdge] = [
        WorkflowEdge(id="e1", source="s", target="a"),
        WorkflowEdge(id="e2", source="a", target="e"),
    ]
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
        sessionmaker=None,
        user_id="u1",
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
        sessionmaker=None,
        user_id="u1",
    )
    assert any(e.get("type") == "stage_applied" for e in events)
    assert session.pending_patch is None
    assert len(wf.nodes) == 2


async def test_approved_patch_collapses_duplicate_edges() -> None:
    wf0 = _empty_workflow()
    edge = wf0.edges[0].model_dump(by_alias=True)
    session = BuildSession(workflow_id="w1")
    session.pending_patch = {
        "nodes": [n.model_dump(by_alias=True) for n in wf0.nodes],
        "edges": [edge, {**edge, "id": "dup"}],
    }
    _events, _reply, wf = await run_builder_turn(
        model=_model(""),
        workflow_id="w1",
        workflow=wf0,
        session=session,
        message="__APPROVE_PATCH__",
        execution_id=None,
        sessionmaker=None,
        user_id="u1",
    )
    assert [e.id for e in wf.edges] == ["e1"]


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
        sessionmaker=None,
        user_id="u1",
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
        sessionmaker=None,
        user_id="u1",
    )
    assert any(e.get("type") == "patch_proposal" for e in events)
    assert session.pending_patch is not None
    assert wf is not None


async def test_diagnose_turn_closes_its_read_session_before_the_model_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class _RecordingModel(_ScriptedModel):
        async def ainvoke(self, messages: Any, **kwargs: Any) -> AIMessage:
            events.append("net:model")
            return await super().ainvoke(messages, **kwargs)

    async def _get(_session: Any, execution_id: str) -> Any:
        events.append("db:read")
        return type(
            "Row",
            (),
            {
                "id": execution_id,
                "workflow_id": "w1",
                "user_id": "u1",
                "status": "failed",
                "error": "boom",
                "node_results": {},
                "variables": {},
            },
        )()

    monkeypatch.setattr(executions_repo, "get", _get)
    session = BuildSession(workflow_id="w1")
    session.pending_plan = {"stages": []}
    await run_builder_turn(
        model=cast("BaseChatModel", _RecordingModel("bad URL")),
        workflow_id="w1",
        workflow=_empty_workflow(),
        session=session,
        message="why did it fail?",
        execution_id="x",
        sessionmaker=cast("Any", RecordingSessionmaker(events)),
        user_id="u1",
    )
    assert "db:read" in events and "net:model" in events
    assert_no_network_inside_session(events)


async def test_build_turn_applies_layout_to_generated_nodes() -> None:
    session = BuildSession(workflow_id="w1")
    session.pending_plan = {"stages": []}  # skip the planning branch
    _events, _reply, wf = await run_builder_turn(
        model=_model(""),
        workflow_id="w1",
        workflow=_origin_workflow(),
        session=session,
        message="build it",
        execution_id=None,
        sessionmaker=None,
        user_id="u1",
    )
    positions = {n.id: n.position for n in wf.nodes}
    # All three nodes started at the origin; layout must separate them.
    assert len({(p.x, p.y) for p in positions.values()}) == 3
    assert positions["s"].x < positions["a"].x < positions["e"].x
    assert any(p.x != 0.0 or p.y != 0.0 for p in positions.values())


async def test_plan_wrapped_in_code_fence_still_becomes_a_plan_card() -> None:
    session = BuildSession(workflow_id="w1")
    fenced = 'Here you go:\n```json\n{"stages":[{"id":"s1","summary":"search"}]}\n```'
    events, _reply, _wf = await run_builder_turn(
        model=_model(fenced),
        workflow_id="w1",
        workflow=_empty_workflow(),
        session=session,
        message="search the web",
        execution_id=None,
        sessionmaker=None,
        user_id="u1",
    )
    assert [e for e in events if e.get("type") == "plan"] == [
        {"type": "plan", "stages": [{"id": "s1", "summary": "search"}]}
    ]


class _ToolCallingModel:
    """Replays scripted AIMessages so the real tool loop runs end to end.

    Records every message list it receives (`self.seen`), so tests can assert
    what the builder fed back. Once the script runs out it answers with plain
    text, which ends the tool loop.
    """

    def __init__(self, script: list[AIMessage]) -> None:
        self._script = list(script)
        self.seen: list[list[Any]] = []

    async def ainvoke(self, messages: Any, **kwargs: Any) -> AIMessage:
        self.seen.append(list(messages))
        return self._script.pop(0) if self._script else AIMessage(content="(script exhausted)")

    def bind_tools(self, tools: Any) -> _ToolCallingModel:
        return self


def _call(name: str, args: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


async def test_build_turn_repairs_a_second_start_and_disconnected_end() -> None:
    """Regression for the 2026-11-04 screenshot: the model added its own Start
    beside the default Start -> End and never wired the End."""
    script = [
        AIMessage(
            content="",
            tool_calls=[  # pyright: ignore[reportArgumentType]
                _call(
                    "create_node", {"node_type": "start", "node_id": "start", "label": "Start"}, "1"
                ),
                _call(
                    "create_node",
                    {
                        "node_type": "agent",
                        "node_id": "search",
                        "label": "Search",
                        "data": {"instructions": "Search the web"},
                    },
                    "2",
                ),
                _call("connect_nodes", {"source": "start", "target": "search"}, "3"),
            ],
        ),
        AIMessage(content="Built it."),
    ]
    session = BuildSession(workflow_id="w1")
    session.pending_plan = {"stages": []}
    _events, reply, wf = await run_builder_turn(
        model=cast("BaseChatModel", _ToolCallingModel(script)),
        workflow_id="w1",
        workflow=_empty_workflow(),
        session=session,
        message="approve the plan",
        execution_id=None,
        sessionmaker=None,
        user_id="u1",
    )

    assert reply == "Built it."
    assert [n.id for n in wf.nodes if n.type == "start"] == ["s"]
    assert {(e.source, e.target) for e in wf.edges} == {("s", "search"), ("search", "e")}
    assert collect_workflow_violations(wf) == []


async def _build(
    script: list[AIMessage], workflow: Workflow | None = None, **kw: Any
) -> tuple[_ToolCallingModel, list[dict[str, Any]], str, Workflow]:
    model = _ToolCallingModel(script)
    session = BuildSession(workflow_id="w1")
    session.pending_plan = {"stages": []}
    events, reply, wf = await run_builder_turn(
        model=cast("BaseChatModel", model),
        workflow_id="w1",
        workflow=workflow or _empty_workflow(),
        session=session,
        message="approve the plan",
        execution_id=None,
        sessionmaker=None,
        user_id="u1",
        **kw,
    )
    return model, events, reply, wf


def _last_feedback(model: _ToolCallingModel, idx: int = 2) -> str:
    return str([m.content for m in model.seen[idx] if isinstance(m, HumanMessage)][-1])


async def test_model_reconnecting_visible_edges_leaves_no_duplicates() -> None:
    """2026-11-19: the model re-added edges it saw in the canvas listing."""
    script = [
        AIMessage(
            content="",
            tool_calls=[  # pyright: ignore[reportArgumentType]
                _call(
                    "create_node",
                    {
                        "node_type": "agent",
                        "node_id": "a",
                        "label": "A",
                        "data": {"instructions": "go"},
                    },
                    "1",
                ),
                _call("connect_nodes", {"source": "s", "target": "a"}, "2"),
                _call("connect_nodes", {"source": "a", "target": "e"}, "3"),
                _call("connect_nodes", {"source": "a", "target": "e"}, "4"),
                _call("connect_nodes", {"source": "s", "target": "a"}, "5"),
            ],
        ),
        AIMessage(content="Done."),
    ]
    _, _, _, wf = await _build(script)
    pairs = [(e.source, e.target, e.branch) for e in wf.edges]
    assert len(pairs) == len(set(pairs))
    assert collect_workflow_violations(wf) == []


async def test_stored_duplicates_are_gone_after_any_build_turn() -> None:
    base = _empty_workflow()
    base = base.model_copy(
        update={"edges": [*base.edges, WorkflowEdge(id="dup", source="s", target="e")]}
    )
    _, _, _, wf = await _build([AIMessage(content="Nothing to change.")], workflow=base)
    assert [e.id for e in wf.edges] == ["e1"]


async def test_bare_node_is_sent_back_and_the_fix_is_kept() -> None:
    """The model creates an agent with no data; the check makes it fill one in."""
    script = [
        AIMessage(
            content="",
            tool_calls=[  # pyright: ignore[reportArgumentType]
                _call("create_node", {"node_type": "agent", "node_id": "a", "label": "A"}, "1"),
                _call("connect_nodes", {"source": "s", "target": "a"}, "2"),
            ],
        ),
        AIMessage(content="Built it."),
        AIMessage(
            content="",
            tool_calls=[  # pyright: ignore[reportArgumentType]
                _call(
                    "update_node",
                    {"node_id": "a", "data": {"instructions": "Summarise the input"}},
                    "3",
                ),
            ],
        ),
        AIMessage(content="Filled in the agent."),
    ]
    model, events, reply, wf = await _build(script)
    feedback = _last_feedback(model)
    assert "'a'" in feedback and "instructions" in feedback
    agent = next(n for n in wf.nodes if n.id == "a")
    assert agent.data.instructions == "Summarise the input"  # pyright: ignore[reportAttributeAccessIssue]
    assert reply == "Filled in the agent."
    assert not any("Remaining issues" in str(e.get("content")) for e in events)


async def test_model_that_never_fixes_gets_one_repair_round_then_user_sees_issues() -> None:
    script = [
        AIMessage(
            content="",
            tool_calls=[  # pyright: ignore[reportArgumentType]
                _call(
                    "create_node",
                    {"node_type": "guardrails", "node_id": "pii", "label": "PII"},
                    "1",
                ),
            ],
        ),
        AIMessage(content="Built."),
    ]
    model, events, _, _ = await _build(script)
    # 1 build call + 1 closing call + 1 repair call. The repair fixed nothing
    # (same issue list), so the second allowed round is skipped.
    assert MAX_REPAIR_ROUNDS >= 2
    assert len(model.seen) == 1 + 1 + 1
    remaining = [e["content"] for e in events if e.get("type") == "message"]
    assert any("Remaining issues" in c and "pii" in c and "piiEnabled" in c for c in remaining)


async def test_repair_prompt_shows_the_current_settings_of_each_flagged_node() -> None:
    """The canvas listing has only id/type/label, so without this the model
    repairs blind: it cannot see which checks are off or what it already wrote."""
    script = [
        AIMessage(
            content="",
            tool_calls=[  # pyright: ignore[reportArgumentType]
                _call(
                    "create_node",
                    {"node_type": "guardrails", "node_id": "pii", "label": "PII"},
                    "1",
                ),
            ],
        ),
        AIMessage(content="Built."),
    ]
    model, _, _, _ = await _build(script)
    feedback = _last_feedback(model)
    assert "Current settings" in feedback
    assert "`pii`" in feedback
    assert '"piiEnabled": false' in feedback


async def test_repair_prompt_never_echoes_secret_values() -> None:
    script = [
        AIMessage(
            content="",
            tool_calls=[  # pyright: ignore[reportArgumentType]
                _call(
                    "create_node",
                    {
                        "node_type": "jira",
                        "node_id": "j1",
                        "label": "Jira",
                        "data": {"jql": "project = X", "apiToken": "plain-secret-123"},
                    },
                    "1",
                ),
            ],
        ),
        AIMessage(content="Built."),
    ]
    model, _, _, _ = await _build(script)
    feedback = _last_feedback(model)
    assert "`j1`" in feedback and "project = X" in feedback
    assert "plain-secret-123" not in feedback


async def test_empty_closing_reply_never_becomes_an_empty_assistant_message() -> None:
    script = [
        AIMessage(
            content="",
            tool_calls=[  # pyright: ignore[reportArgumentType]
                _call(
                    "create_node",
                    {"node_type": "guardrails", "node_id": "pii", "label": "PII"},
                    "1",
                ),
            ],
        ),
        AIMessage(content=""),
    ]
    model, _, _, _ = await _build(script)
    repair_call = model.seen[2]
    assistant_turns = [m for m in repair_call if isinstance(m, AIMessage) and not m.tool_calls]
    assert assistant_turns
    assert all(str(m.content).strip() for m in assistant_turns)


async def test_one_repair_round_is_capped_at_repair_round_iterations() -> None:
    build = [
        AIMessage(
            content="",
            tool_calls=[  # pyright: ignore[reportArgumentType]
                _call(
                    "create_node",
                    {"node_type": "guardrails", "node_id": "pii", "label": "PII"},
                    "1",
                ),
            ],
        ),
        AIMessage(content="Built."),
    ]
    endless = [
        AIMessage(
            content="",
            tool_calls=[_call("no_such_tool", {}, str(i))],  # pyright: ignore[reportArgumentType]
        )
        for i in range(20)
    ]
    model, _, _, _ = await _build([*build, *endless])
    assert len(model.seen) == 2 + REPAIR_ROUND_ITERATIONS


async def test_undeclared_variable_is_reported_back_to_the_model() -> None:
    script = [
        AIMessage(
            content="",
            tool_calls=[  # pyright: ignore[reportArgumentType]
                _call(
                    "create_node",
                    {
                        "node_type": "agent",
                        "node_id": "a",
                        "label": "A",
                        "data": {"instructions": "Research {{topic}}"},
                    },
                    "1",
                ),
            ],
        ),
        AIMessage(content="Built."),
    ]
    model, _, _, _ = await _build(script)
    feedback = _last_feedback(model)
    assert "topic" in feedback and "set_start_inputs" in feedback


async def test_model_catalog_reaches_the_prompt_and_the_check() -> None:
    script = [
        AIMessage(
            content="",
            tool_calls=[  # pyright: ignore[reportArgumentType]
                _call(
                    "create_node",
                    {
                        "node_type": "agent",
                        "node_id": "a",
                        "label": "A",
                        "data": {"instructions": "go"},
                    },
                    "1",
                ),
            ],
        ),
        AIMessage(content="Built."),
    ]
    model, _, _, _ = await _build(script, model_catalog={"anthropic/claude-sonnet-4-5": "Sonnet"})
    system = model.seen[0][0].content
    assert "`anthropic/claude-sonnet-4-5`" in system
    assert "model" in _last_feedback(model)


async def test_requested_credential_silences_the_mcp_server_issue() -> None:
    script = [
        AIMessage(
            content="",
            tool_calls=[  # pyright: ignore[reportArgumentType]
                _call(
                    "create_node",
                    {
                        "node_type": "mcp",
                        "node_id": "m",
                        "label": "Search",
                        "data": {"instructions": "search the web"},
                    },
                    "1",
                ),
                _call(
                    "request_credential",
                    {"kind": "mcp-server", "node_id": "m", "detail": "a Tavily MCP server"},
                    "2",
                ),
            ],
        ),
        AIMessage(content="Built; connect a Tavily MCP server."),
    ]
    model, events, _, _ = await _build(script)
    assert len(model.seen) == 2  # no repair round
    assert any(e.get("type") == "credential_request" for e in events)


def _with_old_bare_agent() -> Workflow:
    base = _empty_workflow()
    old = AgentNode(
        id="old",
        type="agent",
        position=Position(x=200, y=0),
        data=AgentNodeData(label="Old"),
    )
    return base.model_copy(
        update={
            "nodes": [*base.nodes, old],
            # old -> End is left for normalize to wire, so a new node can hang off old.
            "edges": [WorkflowEdge(id="e1", source="s", target="old")],
        }
    )


async def test_untouched_incomplete_nodes_do_not_trigger_repair() -> None:
    script = [
        AIMessage(
            content="",
            tool_calls=[  # pyright: ignore[reportArgumentType]
                _call(
                    "create_node",
                    {
                        "node_type": "agent",
                        "node_id": "a",
                        "label": "A",
                        "data": {"instructions": "go"},
                    },
                    "1",
                ),
                _call("connect_nodes", {"source": "old", "target": "a"}, "2"),
            ],
        ),
        AIMessage(content="Done."),
    ]
    model, events, _, _ = await _build(script, workflow=_with_old_bare_agent())
    assert len(model.seen) == 2
    assert not any("old" in str(e.get("content")) for e in events)


async def test_existing_node_the_builder_edits_is_checked() -> None:
    script = [
        AIMessage(
            content="",
            tool_calls=[  # pyright: ignore[reportArgumentType]
                _call("update_node", {"node_id": "old", "data": {"label": "Old step"}}, "1"),
            ],
        ),
        AIMessage(content="Renamed."),
    ]
    model, _, _, _ = await _build(script, workflow=_with_old_bare_agent())
    assert len(model.seen) > 2
    assert "'old'" in _last_feedback(model)


async def test_missing_approval_branch_is_sent_back_and_the_fix_is_kept() -> None:
    """Production case: the builder wires only `approved`; it must be told to
    connect `rejected` and keep the fix, never hand back an unrunnable flow."""
    script = [
        AIMessage(
            content="",
            tool_calls=[  # pyright: ignore[reportArgumentType]
                _call(
                    "create_node",
                    {"node_type": "user-approval", "node_id": "approval-1", "label": "Approve"},
                    "1",
                ),
                _call("disconnect_nodes", {"source": "s", "target": "e"}, "2"),
                _call("connect_nodes", {"source": "s", "target": "approval-1"}, "3"),
                _call(
                    "connect_nodes",
                    {"source": "approval-1", "target": "e", "branch": "approved"},
                    "4",
                ),
            ],
        ),
        AIMessage(content="Built it."),
        AIMessage(
            content="",
            tool_calls=[  # pyright: ignore[reportArgumentType]
                _call(
                    "connect_nodes",
                    {"source": "approval-1", "target": "e", "branch": "rejected"},
                    "5",
                ),
            ],
        ),
        AIMessage(content="Connected the rejected path."),
    ]
    model, events, reply, wf = await _build(script)
    feedback = _last_feedback(model)
    assert 'has no "rejected" path' in feedback
    assert {(e.source, e.branch) for e in wf.edges if e.source == "approval-1"} == {
        ("approval-1", "approved"),
        ("approval-1", "rejected"),
    }
    assert reply == "Connected the rejected path."
    assert not any("Remaining issues" in str(e.get("content")) for e in events)


async def test_repair_prompt_attaches_settings_of_a_missing_branch_node_with_a_different_label() -> (
    None
):
    """The issue names the node as `"Label" (node-id)`; the id is not quoted."""
    script = [
        AIMessage(
            content="",
            tool_calls=[  # pyright: ignore[reportArgumentType]
                _call(
                    "create_node",
                    {"node_type": "user-approval", "node_id": "approval-1", "label": "Approve"},
                    "1",
                ),
                _call("disconnect_nodes", {"source": "s", "target": "e"}, "2"),
                _call("connect_nodes", {"source": "s", "target": "approval-1"}, "3"),
                _call(
                    "connect_nodes",
                    {"source": "approval-1", "target": "e", "branch": "approved"},
                    "4",
                ),
            ],
        ),
        AIMessage(content="Built it."),
    ]
    model, _, _, _ = await _build(script)
    feedback = _last_feedback(model)
    assert '"Approve" (approval-1)' in feedback
    assert "Current settings" in feedback
    assert "- `approval-1` (user-approval)" in feedback
