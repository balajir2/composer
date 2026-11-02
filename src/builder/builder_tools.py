"""LangChain tools the builder agent uses to mutate a Draft.

Each tool returns a plain-string result. Invalid inputs return "ERROR: ..."
so the model sees a fixable message instead of a raised exception. Schema
validation happens by constructing the Pydantic node model; shape validation
is deferred to the turn boundary (Task 6).
"""

import re
import secrets
from dataclasses import dataclass, field
from typing import Any, cast

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel

from src.builder.draft import Draft
from src.engine.workflow import (
    AgentNode,
    ArcadeNode,
    ConfluenceNode,
    DataTransformNode,
    DecisionNode,
    DownloadPdfNode,
    EmailNode,
    EndNode,
    ExtractNode,
    FileTriggerNode,
    FileWriteNode,
    GammaAiNode,
    GuardrailsNode,
    HttpNode,
    IfElseNode,
    JiraNode,
    JoinChunksNode,
    JoinNode,
    McpNode,
    NoteNode,
    Position,
    SetStateNode,
    StartInputVariable,
    StartNode,
    StartNodeData,
    TransformNode,
    UserApprovalNode,
    VectorDbNode,
    WhileNode,
    WorkflowEdge,
    WorkflowNode,
)

_NODE_CLASSES: dict[str, type[BaseModel]] = {
    "start": StartNode,
    "end": EndNode,
    "note": NoteNode,
    "file-trigger": FileTriggerNode,
    "file-write": FileWriteNode,
    "agent": AgentNode,
    "mcp": McpNode,
    "http": HttpNode,
    "extract": ExtractNode,
    "transform": TransformNode,
    "data-transform": DataTransformNode,
    "set-state": SetStateNode,
    "if-else": IfElseNode,
    "decision": DecisionNode,
    "while": WhileNode,
    "user-approval": UserApprovalNode,
    "guardrails": GuardrailsNode,
    "vector-db": VectorDbNode,
    "gamma-ai": GammaAiNode,
    "email": EmailNode,
    "arcade": ArcadeNode,
    "join-chunks": JoinChunksNode,
    "jira": JiraNode,
    "confluence": ConfluenceNode,
    "download-pdf": DownloadPdfNode,
    "join": JoinNode,
}

_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

_CREDENTIAL_RESOLVABLE_BY = {
    "mcp-server": "user",
    "vector-db": "user",
    "jira": "user",
    "confluence": "user",
    "google-drive": "user",
    "resend": "admin",
    "tool-provider": "admin",
    "llm-provider": "admin",
}


@dataclass
class BuilderContext:
    draft: Draft
    events: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])


def _build_node(node_type: str, node_id: str, data: dict[str, Any]) -> WorkflowNode | None:
    cls = _NODE_CLASSES.get(node_type)
    if cls is None:
        return None
    if not _ID_RE.match(node_id):
        return None
    payload = {"label": data.get("label", node_type), **data}
    try:
        node = cls(id=node_id, type=node_type, position=Position(x=0, y=0), data=payload)
    except Exception:
        return None
    return cast("WorkflowNode", node)


def make_builder_tools(ctx: BuilderContext) -> list[BaseTool]:
    draft = ctx.draft

    @tool
    def create_node(node_type: str, node_id: str, label: str, data: dict[str, Any] = {}) -> str:  # noqa: B006
        """Create a node. `data` is a dict of node-specific fields (omit label; it is passed separately)."""
        payload = {"label": label, **data}
        node = _build_node(node_type, node_id, payload)
        if node is None:
            return (
                f"ERROR: could not create {node_type!r} node {node_id!r} — check type and fields."
            )
        if node_id in draft.nodes:
            return f"ERROR: node id {node_id!r} already exists."
        draft.nodes[node_id] = node
        return f"ok: created {node_type} node {node_id!r}."

    @tool
    def update_node(node_id: str, data: dict[str, Any]) -> str:
        """Replace the `data` of an existing node (merge with existing fields)."""
        existing = draft.nodes.get(node_id)
        if existing is None:
            return f"ERROR: no node {node_id!r}."
        merged = {**existing.data.model_dump(by_alias=True), **data}
        updated = _build_node(existing.type, node_id, merged)
        if updated is None:
            return f"ERROR: invalid data for node {node_id!r}."
        draft.nodes[node_id] = updated
        return f"ok: updated node {node_id!r}."

    @tool
    def remove_node(node_id: str) -> str:
        """Remove a node and any edges touching it."""
        if node_id not in draft.nodes:
            return f"ERROR: no node {node_id!r}."
        del draft.nodes[node_id]
        for eid in [
            e for e, ed in draft.edges.items() if ed.source == node_id or ed.target == node_id
        ]:
            del draft.edges[eid]
        return f"ok: removed node {node_id!r}."

    @tool
    def connect_nodes(source: str, target: str, branch: str | None = None) -> str:
        """Add an edge from `source` to `target`. `branch` expected for if-else/while/user-approval/decision sources."""
        if source not in draft.nodes:
            return f"ERROR: unknown source {source!r}."
        if target not in draft.nodes:
            return f"ERROR: unknown target {target!r}."
        edge_id = f"e-{secrets.token_hex(4)}"
        draft.edges[edge_id] = WorkflowEdge(id=edge_id, source=source, target=target, branch=branch)
        return f"ok: connected {source!r} -> {target!r}."

    @tool
    def set_start_inputs(variables: list[dict[str, Any]]) -> str:
        """Declare the Start node's input variables (list of {name, type?, required?, description?})."""
        start = next((n for n in draft.nodes.values() if n.type == "start"), None)
        if start is None:
            return "ERROR: no start node — create one first."
        ivars: list[dict[str, Any]] = []
        for v in variables:
            try:
                ivars.append(StartInputVariable(**v).model_dump(by_alias=True))
            except Exception:
                return f"ERROR: invalid input variable: {v!r}."
        data: dict[str, Any] = {**start.data.model_dump(by_alias=True), "inputVariables": ivars}
        node_data = StartNodeData(**data)
        draft.nodes[start.id] = StartNode(
            id=start.id, type="start", position=start.position, data=node_data
        )
        return f"ok: declared {len(ivars)} start inputs."

    @tool
    def request_credential(kind: str, node_id: str, detail: str) -> str:
        """Declare that a node needs a credential the user must supply (kind e.g. mcp-server, vector-db, jira, google-drive)."""
        ctx.events.append(
            {
                "type": "credential_request",
                "kind": kind,
                "node_id": node_id,
                "detail": detail,
                "resolvable_by": _CREDENTIAL_RESOLVABLE_BY.get(kind, "user"),
            }
        )
        return f"credential requested: {kind} for node {node_id!r}."

    return [
        create_node,
        update_node,
        remove_node,
        connect_nodes,
        set_start_inputs,
        request_credential,
    ]
