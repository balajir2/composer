"""Pydantic models for Composer's workflow JSON.

Implements ADR-0002 (full OAB fidelity for the workflow envelope). This
module defines the Phase 1 models (Workflow, WorkflowEdge, Position,
BaseNodeData) plus all 18 node discriminators (Start, End, and 16 more
added in Task 4).

See docs/superpowers/specs/2026-04-20-phase-1-execution-engine-design.md §5.
OAB reference: lib/workflow/types.ts.
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Position(BaseModel):
    x: float
    y: float


class BaseNodeData(BaseModel):
    """Fields shared by every node-type data class."""

    # extra="allow" preserves per-type fields not yet modeled (e.g. Agent.tools,
    # Mcp.mcpServers) so workflows authored against later-phase executors still
    # round-trip through Phase 1 CRUD without data loss.
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    label: str
    node_type: str | None = Field(default=None, alias="nodeType")
    node_name: str | None = Field(default=None, alias="nodeName")


# ─── start ───────────────────────────────────────────────────────────────


class StartInputVariable(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    type: str
    required: bool
    description: str
    default_value: Any | None = Field(default=None, alias="defaultValue")


class StartNodeData(BaseNodeData):
    input_variables: list[StartInputVariable] = Field(default_factory=list, alias="inputVariables")


class StartNode(BaseModel):
    id: str
    type: Literal["start"]
    position: Position
    data: StartNodeData


# ─── end ─────────────────────────────────────────────────────────────────


class EndNodeData(BaseNodeData):
    pass


class EndNode(BaseModel):
    id: str
    type: Literal["end"]
    position: Position
    data: EndNodeData


# ─── note (visual-only; executor is a no-op; graph_builder skips) ────────


class NoteNodeData(BaseNodeData):
    content: str | None = None


class NoteNode(BaseModel):
    id: str
    type: Literal["note"]
    position: Position
    data: NoteNodeData


# ─── agent (Phase 2) ─────────────────────────────────────────────────────


class AgentTool(BaseModel):
    """Loose shape; Phase 2 tightens when Agent executor lands."""

    model_config = ConfigDict(extra="allow")


class AgentNodeData(BaseNodeData):
    name: str | None = None
    instructions: str | None = None
    model: str | None = None
    include_chat_history: bool = Field(default=False, alias="includeChatHistory")
    tools: list[AgentTool] = Field(default_factory=list)
    output_format: str | None = Field(default=None, alias="outputFormat")
    reasoning_effort: str | None = Field(default=None, alias="reasoningEffort")
    json_schema: dict[str, Any] | None = Field(default=None, alias="jsonSchema")
    mcp_tools: list[dict[str, Any]] = Field(default_factory=list, alias="mcpTools")
    mcp_server_ids: list[str] = Field(default_factory=list, alias="mcpServerIds")
    selected_tools: list[str] = Field(default_factory=list, alias="selectedTools")


class AgentNode(BaseModel):
    id: str
    type: Literal["agent"]
    position: Position
    data: AgentNodeData


# ─── mcp (Phase 3) ───────────────────────────────────────────────────────


class McpNodeData(BaseNodeData):
    mcp_server_id: str | None = Field(default=None, alias="mcpServerId")
    tool_name: str | None = Field(default=None, alias="toolName")
    arguments: dict[str, Any] = Field(default_factory=dict)


class McpNode(BaseModel):
    id: str
    type: Literal["mcp"]
    position: Position
    data: McpNodeData


# ─── if-else (Phase 4) ───────────────────────────────────────────────────


class IfElseNodeData(BaseNodeData):
    condition: str | None = None
    true_path: str | None = Field(default=None, alias="truePath")
    false_path: str | None = Field(default=None, alias="falsePath")
    true_label: str | None = Field(default=None, alias="trueLabel")
    false_label: str | None = Field(default=None, alias="falseLabel")


class IfElseNode(BaseModel):
    id: str
    type: Literal["if-else"]
    position: Position
    data: IfElseNodeData


# ─── while (Phase 4) ─────────────────────────────────────────────────────


class WhileNodeData(BaseNodeData):
    while_condition: str | None = Field(default=None, alias="whileCondition")
    max_iterations: int | None = Field(default=None, alias="maxIterations")


class WhileNode(BaseModel):
    id: str
    type: Literal["while"]
    position: Position
    data: WhileNodeData


# ─── user-approval (Phase 5) ─────────────────────────────────────────────


class UserApprovalNodeData(BaseNodeData):
    approval_message: str | None = Field(default=None, alias="approvalMessage")


class UserApprovalNode(BaseModel):
    id: str
    type: Literal["user-approval"]
    position: Position
    data: UserApprovalNodeData


# ─── transform (Phase 4 — E2B Python SDK) ────────────────────────────────


class TransformNodeData(BaseNodeData):
    transform_script: str | None = Field(default=None, alias="transformScript")


class TransformNode(BaseModel):
    id: str
    type: Literal["transform"]
    position: Position
    data: TransformNodeData


# ─── data-transform (Phase 4) ────────────────────────────────────────────


class DataTransformNodeData(BaseNodeData):
    operation: str = "map"  # map | filter | reduce
    collection: str = ""  # simpleeval expression that yields an iterable
    expression: str = ""  # per-item expression
    item_var: str = Field(default="item", alias="itemVar")
    initial: Any | None = None  # used only for reduce


class DataTransformNode(BaseModel):
    id: str
    type: Literal["data-transform"]
    position: Position
    data: DataTransformNodeData


# ─── set-state (Phase 4) ─────────────────────────────────────────────────


class SetStateNodeData(BaseNodeData):
    state_key: str | None = Field(default=None, alias="stateKey")
    state_value: Any | None = Field(default=None, alias="stateValue")


class SetStateNode(BaseModel):
    id: str
    type: Literal["set-state"]
    position: Position
    data: SetStateNodeData


# ─── extract (Phase 4 — structured output) ───────────────────────────────


class ExtractNodeData(BaseNodeData):
    extract_config: dict[str, Any] | None = Field(default=None, alias="extractConfig")
    extract_tool: str | None = Field(default=None, alias="extractTool")
    json_schema: dict[str, Any] | None = Field(default=None, alias="jsonSchema")
    input_text: str | None = Field(default=None, alias="input")
    model: str | None = None


class ExtractNode(BaseModel):
    id: str
    type: Literal["extract"]
    position: Position
    data: ExtractNodeData


# ─── http (Phase 4) ──────────────────────────────────────────────────────


class HttpNodeData(BaseNodeData):
    http_url: str | None = Field(default=None, alias="httpUrl")
    http_method: str | None = Field(default=None, alias="httpMethod")
    http_headers: dict[str, str] = Field(default_factory=dict, alias="httpHeaders")
    http_body: Any | None = Field(default=None, alias="httpBody")
    response_path: str | None = Field(default=None, alias="responsePath")


class HttpNode(BaseModel):
    id: str
    type: Literal["http"]
    position: Position
    data: HttpNodeData


# ─── guardrails (Phase 6) ────────────────────────────────────────────────


class GuardrailsNodeData(BaseNodeData):
    # TODO(phase-6): tighten against OAB's guardrails node fields.
    config: dict[str, Any] = Field(default_factory=dict)


class GuardrailsNode(BaseModel):
    id: str
    type: Literal["guardrails"]
    position: Position
    data: GuardrailsNodeData


# ─── vector-db (Phase 6) ─────────────────────────────────────────────────


class VectorDbNodeData(BaseNodeData):
    vector_db_provider: str | None = Field(default=None, alias="vectorDbProvider")
    endpoint: str | None = None
    api_key: str | None = Field(default=None, alias="apiKey")
    collection: str | None = None
    # TODO(phase-6): embedding-specific fields (model, dimension, etc.)
    embedding_config: dict[str, Any] = Field(default_factory=dict, alias="embeddingConfig")


class VectorDbNode(BaseModel):
    id: str
    type: Literal["vector-db"]
    position: Position
    data: VectorDbNodeData


# ─── gamma-ai (Phase 6) ──────────────────────────────────────────────────


class GammaAiNodeData(BaseNodeData):
    # TODO(phase-6): tighten against OAB's Gamma AI node fields.
    config: dict[str, Any] = Field(default_factory=dict)


class GammaAiNode(BaseModel):
    id: str
    type: Literal["gamma-ai"]
    position: Position
    data: GammaAiNodeData


# ─── arcade (Phase 6) ────────────────────────────────────────────────────


class ArcadeNodeData(BaseNodeData):
    arcade_tool: str | None = Field(default=None, alias="arcadeTool")
    arcade_input: dict[str, Any] = Field(default_factory=dict, alias="arcadeInput")
    arcade_user_id: str | None = Field(default=None, alias="arcadeUserId")


class ArcadeNode(BaseModel):
    id: str
    type: Literal["arcade"]
    position: Position
    data: ArcadeNodeData


# ─── join-chunks (Phase 6) ───────────────────────────────────────────────


class JoinChunksNodeData(BaseNodeData):
    # TODO(phase-6): tighten when OAB's join-chunks executor is studied.
    config: dict[str, Any] = Field(default_factory=dict)


class JoinChunksNode(BaseModel):
    id: str
    type: Literal["join-chunks"]
    position: Position
    data: JoinChunksNodeData


# ─── edges ───────────────────────────────────────────────────────────────


class WorkflowEdge(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    source: str
    target: str
    type: str | None = None
    label: str | None = None
    source_handle: str | None = Field(default=None, alias="sourceHandle")
    # Phase 4b: identifies which conditional branch this edge represents.
    # Required when source is if-else or while; forbidden otherwise.
    # if-else edges: branch ∈ {'true', 'false'}
    # while edges:   branch ∈ {'body', 'exit'}
    # normal edges:  branch is None
    branch: str | None = None


# ─── discriminated union ─────────────────────────────────────────────────
# Keep the ordering stable — Pydantic validates in declaration order when the
# discriminator tag is absent (should never happen, but defensive).

WorkflowNode = Annotated[
    StartNode
    | EndNode
    | NoteNode
    | AgentNode
    | McpNode
    | IfElseNode
    | WhileNode
    | UserApprovalNode
    | TransformNode
    | DataTransformNode
    | SetStateNode
    | ExtractNode
    | HttpNode
    | GuardrailsNode
    | VectorDbNode
    | GammaAiNode
    | ArcadeNode
    | JoinChunksNode,
    Field(discriminator="type"),
]


# ─── workflow ────────────────────────────────────────────────────────────


class Workflow(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str | None = None
    user_id: str | None = Field(default=None, alias="userId")
    name: str
    description: str | None = None
    category: str | None = None
    tags: list[str] = Field(default_factory=list)
    difficulty: str | None = None
    estimated_time: str | None = Field(default=None, alias="estimatedTime")
    nodes: list[WorkflowNode]
    edges: list[WorkflowEdge]
    version: str | None = None
    is_template: bool = Field(default=False, alias="isTemplate")
    is_public: bool = Field(default=False, alias="isPublic")
    created_at: str | None = Field(default=None, alias="createdAt")
    updated_at: str | None = Field(default=None, alias="updatedAt")


__all__ = [
    "AgentNode",
    "AgentNodeData",
    "AgentTool",
    "ArcadeNode",
    "ArcadeNodeData",
    "BaseNodeData",
    "DataTransformNode",
    "DataTransformNodeData",
    "EndNode",
    "EndNodeData",
    "ExtractNode",
    "ExtractNodeData",
    "GammaAiNode",
    "GammaAiNodeData",
    "GuardrailsNode",
    "GuardrailsNodeData",
    "HttpNode",
    "HttpNodeData",
    "IfElseNode",
    "IfElseNodeData",
    "JoinChunksNode",
    "JoinChunksNodeData",
    "McpNode",
    "McpNodeData",
    "NoteNode",
    "NoteNodeData",
    "Position",
    "SetStateNode",
    "SetStateNodeData",
    "StartInputVariable",
    "StartNode",
    "StartNodeData",
    "TransformNode",
    "TransformNodeData",
    "UserApprovalNode",
    "UserApprovalNodeData",
    "VectorDbNode",
    "VectorDbNodeData",
    "WhileNode",
    "WhileNodeData",
    "Workflow",
    "WorkflowEdge",
    "WorkflowNode",
]
