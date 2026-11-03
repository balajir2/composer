"""Builder agent loop + ephemeral in-memory BuildSession."""

import json
import time
from dataclasses import dataclass, field
from typing import Any, cast

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from src.builder.builder_tools import BuilderContext, make_builder_tools
from src.builder.diagnostics import build_diagnosis
from src.builder.draft import Draft
from src.builder.layout import compute_layout
from src.builder.prompts import build_system_prompt
from src.engine.graph_builder import collect_workflow_violations
from src.engine.workflow import Workflow

MAX_TOOL_ITERATIONS = 12
_APPROVE_PATCH = "__APPROVE_PATCH__"
_REJECT_PATCH = "__REJECT_PATCH__"


@dataclass
class BuildSession:
    workflow_id: str
    messages: list[BaseMessage] = field(default_factory=list[BaseMessage])
    pending_plan: dict[str, Any] | None = None
    pending_patch: dict[str, Any] | None = None
    last_activity: float = field(default_factory=time.monotonic)

    def touch(self) -> None:
        self.last_activity = time.monotonic()


def _system_messages() -> list[BaseMessage]:
    return [SystemMessage(content=build_system_prompt())]


def _try_parse_plan(text: str) -> dict[str, Any] | None:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict) and "stages" in data:
        return cast("dict[str, Any]", data)
    return None


def _extract_text(response: Any) -> str:
    content = getattr(response, "content", "") if hasattr(response, "content") else ""
    return content if isinstance(content, str) else str(content)


async def _tool_loop(model: BaseChatModel, ctx: BuilderContext, session: BuildSession) -> str:
    tools = make_builder_tools(ctx)
    bound = model.bind_tools(tools)
    messages: list[BaseMessage] = [*_system_messages(), *session.messages]
    for _ in range(MAX_TOOL_ITERATIONS):
        response = await bound.ainvoke(messages)
        messages.append(response)
        tool_calls: list[Any] = getattr(response, "tool_calls", None) or []
        if not tool_calls:
            return _extract_text(response)
        for call in tool_calls:
            name: str = call.get("name")
            args: dict[str, Any] = call.get("args") or {}
            tool = next((t for t in tools if t.name == name), None)
            if tool is None:
                result = f"ERROR: unknown tool {name!r}."
            else:
                try:
                    result = await tool.ainvoke(args)
                except Exception as exc:
                    result = f"ERROR: {exc}"
            messages.append(ToolMessage(content=str(result), tool_call_id=call.get("id", "0")))
    return "I couldn't finish that within the iteration budget."


def _diff_workflows(original: Workflow, candidate: Workflow) -> dict[str, Any]:
    """Compute a patch diff between two workflow snapshots."""
    orig_nodes = {n.id: n for n in original.nodes}
    cand_nodes = {n.id: n for n in candidate.nodes}
    added_nodes = [n.model_dump(by_alias=True) for i, n in cand_nodes.items() if i not in orig_nodes]
    removed_nodes = [i for i in orig_nodes if i not in cand_nodes]
    changed_nodes = [
        i
        for i in orig_nodes
        if i in cand_nodes
        and orig_nodes[i].model_dump(by_alias=True) != cand_nodes[i].model_dump(by_alias=True)
    ]
    orig_edges = {(e.source, e.target, e.branch) for e in original.edges}
    cand_edges = {(e.source, e.target, e.branch) for e in candidate.edges}
    added_edges = [list(e) for e in cand_edges - orig_edges]
    removed_edges = [list(e) for e in orig_edges - cand_edges]
    return {
        "added_nodes": added_nodes,
        "removed_nodes": removed_nodes,
        "changed_nodes": changed_nodes,
        "added_edges": added_edges,
        "removed_edges": removed_edges,
    }


def _apply_layout(workflow: Workflow) -> Workflow:
    """Reposition builder-generated nodes using the deterministic layered layout.

    ``_build_node`` creates every node at ``Position(x=0, y=0)``, so without
    this the canvas would stack the whole graph at the origin. Positions are
    advisory — the user can drag nodes afterward.
    """
    positions = compute_layout(workflow.nodes, workflow.edges)
    nodes = [n.model_copy(update={"position": positions[n.id]}) for n in workflow.nodes]
    return Workflow(
        id=workflow.id,
        name=workflow.name,
        description=workflow.description,
        nodes=nodes,
        edges=workflow.edges,
    )


def _draft_to_workflow(workflow: Workflow, draft: Draft) -> Workflow:
    nodes, edges = draft.snapshot()
    return _apply_layout(
        Workflow(
            id=workflow.id,
            name=workflow.name,
            description=workflow.description,
            nodes=nodes,
            edges=edges,
        )
    )


def _workflow_from_patch(workflow: Workflow, patch: dict[str, Any]) -> Workflow:
    return _apply_layout(
        Workflow(
            id=workflow.id,
            name=workflow.name,
            description=workflow.description,
            nodes=patch["nodes"],
            edges=patch["edges"],
        )
    )


async def run_builder_turn(
    *,
    model: BaseChatModel,
    workflow_id: str,
    workflow: Workflow,
    session: BuildSession,
    message: str,
    execution_id: str | None,
    db: Any,
) -> tuple[list[dict[str, Any]], str, Workflow]:
    """Run one turn. Returns (events, reply, workflow-to-persist)."""
    session.touch()
    events: list[dict[str, Any]] = []
    session.messages.append(HumanMessage(content=message))

    # 1. Approve/reject a pending patch (deterministic; no LLM).
    if message == _APPROVE_PATCH and session.pending_patch is not None:
        updated = _workflow_from_patch(workflow, session.pending_patch)
        session.pending_patch = None
        session.messages.append(AIMessage(content="Patch applied."))
        return [
            {
                "type": "stage_applied",
                "node_count": len(updated.nodes),
                "edge_count": len(updated.edges),
            }
        ], "Patch applied.", updated
    if message == _REJECT_PATCH and session.pending_patch is not None:
        session.pending_patch = None
        session.messages.append(AIMessage(content="Patch discarded."))
        return [{"type": "message", "content": "Patch discarded."}], "Patch discarded.", workflow

    # 2. Diagnose turn: propose a patch, do NOT apply.
    if execution_id:
        if db is not None:
            diag = await build_diagnosis(db, execution_id)
            if diag is not None:
                session.messages.append(
                    SystemMessage(
                        content=(
                            f"Diagnosis of failed execution {execution_id}: "
                            f"status={diag.status!r}, error={diag.error!r}, "
                            f"node_results={diag.node_results!r}, variables={diag.variables_tail!r}"
                        )
                    )
                )
        ctx = BuilderContext(draft=Draft())
        for node in workflow.nodes:
            ctx.draft.nodes[node.id] = node
        for edge in workflow.edges:
            ctx.draft.edges[edge.id] = edge
        reply = await _tool_loop(model, ctx, session)
        candidate = _draft_to_workflow(workflow, ctx.draft)
        diff = _diff_workflows(workflow, candidate)
        session.pending_patch = {
            "nodes": [n.model_dump(by_alias=True) for n in candidate.nodes],
            "edges": [e.model_dump(by_alias=True) for e in candidate.edges],
        }
        session.messages.append(AIMessage(content=reply))
        events.append({"type": "patch_proposal", "diff": diff, "explanation": reply})
        return events, reply, workflow  # unchanged — nothing applied yet

    # 3. Planning turn.
    if session.pending_plan is None:
        response = await model.ainvoke([*_system_messages(), *session.messages])
        text = _extract_text(response)
        plan = _try_parse_plan(text)
        if plan is not None:
            session.pending_plan = plan
            session.messages.append(AIMessage(content=json.dumps(plan)))
            return [{"type": "plan", "stages": plan.get("stages", [])}], (
                "Here's my plan. Approve it and I'll start building."
            ), workflow
        # Fall through to build if the model didn't return a plan.

    # 4. Build turn.
    ctx = BuilderContext(draft=Draft())
    for node in workflow.nodes:
        ctx.draft.nodes[node.id] = node
    for edge in workflow.edges:
        ctx.draft.edges[edge.id] = edge
    reply = await _tool_loop(model, ctx, session)
    updated = _draft_to_workflow(workflow, ctx.draft)
    violations = collect_workflow_violations(updated)
    if violations:
        events.append({"type": "message", "content": "Remaining issues: " + "; ".join(violations[:5])})
    events.extend(ctx.events)
    events.append(
        {"type": "stage_applied", "node_count": len(updated.nodes), "edge_count": len(updated.edges)}
    )
    session.messages.append(AIMessage(content=reply))
    return events, reply, updated
