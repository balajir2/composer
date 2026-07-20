"""Pydantic models for Composer's workflow JSON.

Implements ADR-0002 (full OAB fidelity for the workflow envelope). This
module defines the Phase 1 models (Workflow, WorkflowEdge, Position,
BaseNodeData) plus all 18 node discriminators (Start, End, and 16 more
added in Task 4).

See docs/superpowers/specs/2026-04-20-phase-1-execution-engine-design.md §5.
OAB reference: lib/workflow/types.ts.
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    # extra="allow" lets the canvas store arbitrary future fields (e.g. a UI
    # label) without making migrations hard.  description is optional so
    # workflows declaring only name/type/required still validate.
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    name: str
    type: str = "text"
    required: bool = False
    description: str = ""
    default_value: Any | None = Field(default=None, alias="defaultValue")


class StartNodeData(BaseNodeData):
    # Canonical field: inputVariables.  Prior canvas builds saved under
    # `inputs` — read that as a fallback via a pre-validator so existing
    # saved workflows continue to resolve declared variables.
    input_variables: list[StartInputVariable] = Field(default_factory=list, alias="inputVariables")

    @model_validator(mode="before")
    @classmethod
    def _coerce_legacy_inputs(cls, data: Any) -> Any:
        if isinstance(data, dict) and "inputVariables" not in data and "inputs" in data:
            legacy = data.get("inputs")
            if isinstance(legacy, list):
                data = {**data, "inputVariables": legacy}
        return data


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


# ─── file-trigger (visual-only; executor is a no-op; graph_builder skips) ─


class FileTriggerNodeData(BaseNodeData):
    provider: Literal["local", "google-drive"] = "local"
    source_path: str | None = Field(default=None, alias="sourcePath")  # local only
    dest_path: str | None = Field(default=None, alias="destPath")  # local only
    error_path: str | None = Field(default=None, alias="errorPath")  # local only
    target_input_variable: str | None = Field(default=None, alias="targetInputVariable")
    poll_interval_seconds: int = Field(default=30, alias="pollIntervalSeconds")  # local (CLI) only
    connection_id: str | None = Field(default=None, alias="connectionId")  # google-drive only
    drive_folder_id: str | None = Field(default=None, alias="driveFolderId")  # google-drive only
    # Optional visible-move destinations, mirroring dest_path/error_path
    # above -- both None by default (marker-only claim behavior, unchanged
    # for existing configs). See GoogleDriveProvider.move_file.
    drive_processed_folder_id: str | None = Field(
        default=None, alias="driveProcessedFolderId"
    )  # google-drive only
    drive_error_folder_id: str | None = Field(
        default=None, alias="driveErrorFolderId"
    )  # google-drive only


class FileTriggerNode(BaseModel):
    id: str
    type: Literal["file-trigger"]
    position: Position
    data: FileTriggerNodeData


# ─── file-write (writes generated content to a file — PDF/DOCX/MD) ───────


class FileWriteNodeData(BaseNodeData):
    # Plain str (not Literal["local"]): unlike file-trigger (visual-only,
    # never executes), file-write's executor must be able to *receive* an
    # unrecognized provider value and raise a runtime
    # UnknownStorageProviderError from its own registry lookup — a Literal
    # type would instead reject it at parse time via ValidationError,
    # one layer too early (see FileWriteExecutor._PROVIDERS.get()).
    provider: str = "local"
    destination_path: str | None = Field(default=None, alias="destinationPath")
    filename: str | None = None
    format: Literal["md", "docx", "pdf", "html"] = "md"
    content: str | None = None


class FileWriteNode(BaseModel):
    id: str
    type: Literal["file-write"]
    position: Position
    data: FileWriteNodeData


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
    # Cap on the tool-call → LLM-response loop.  Default stays 10 (OAB
    # parity); long research flows that call search + scrape repeatedly
    # can bump this per-node.
    max_iterations: int | None = Field(default=None, alias="maxIterations")


class AgentNode(BaseModel):
    id: str
    type: Literal["agent"]
    position: Position
    data: AgentNodeData


# ─── mcp (Phase 3) ───────────────────────────────────────────────────────


class McpNodeData(BaseNodeData):
    mcp_server_id: str | None = Field(default=None, alias="mcpServerId")
    # Agent mode (preferred): LLM drives the selected tools from the server
    # using the natural-language `instructions`.  Multiple tools can be
    # selected; the model picks which one(s) to call.
    selected_tool_names: list[str] = Field(default_factory=list, alias="selectedToolNames")
    instructions: str | None = None
    model: str | None = None  # "provider/modelId" string; defaults when unset
    # Cap on the tool-call → LLM-response loop in agent mode.  Default
    # stays 10; async-tool flows (e.g. firecrawl_agent + status polling)
    # often need 20+.
    max_iterations: int | None = Field(default=None, alias="maxIterations")
    # Legacy deterministic mode (still supported for back-compat):
    #   exactly one `toolName` is called with the resolved `arguments` dict.
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
    condition: str | None = None
    max_iterations: int = Field(default=100, alias="maxIterations")


class WhileNode(BaseModel):
    id: str
    type: Literal["while"]
    position: Position
    data: WhileNodeData


# ─── user-approval (Phase 5) ─────────────────────────────────────────────


class UserApprovalNodeData(BaseNodeData):
    # extra="forbid": see the identical comment on HttpNodeData. The
    # Designer's User Approval panel used to write `message` instead of
    # `approvalMessage`. See P0-0 in docs/claude-improvement-backlog.md.
    # One live workflow had both keys at once (approvalMessage correctly
    # set, message a dead leftover) — cleaned up in the database as part
    # of this change, since forbid would otherwise reject a currently-
    # working node over an inert extra key.
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    approval_message: str | None = Field(default=None, alias="approvalMessage")
    approver_email: str | None = Field(default=None, alias="approverEmail")
    approver_cc: str | None = Field(default=None, alias="approverCc")
    attachment_path: str | None = Field(default=None, alias="attachmentPath")


class UserApprovalNode(BaseModel):
    id: str
    type: Literal["user-approval"]
    position: Position
    data: UserApprovalNodeData


# ─── transform (Phase 4 — E2B Python SDK) ────────────────────────────────


class TransformNodeData(BaseNodeData):
    transform_script: str | None = Field(default=None, alias="transformScript")
    # Optional named state variable to write the result to, in addition
    # to `lastOutput`.  Lets workflows compute and persist in one node
    # — without it, "increment a counter" or "append to a list" each
    # need a transform → set-state pair, which makes loops 2x as
    # cluttered as they should be.  Reserved names blocked at execute
    # time (variables / lastOutput / node_results / `_*` internals).
    output_key: str | None = Field(default=None, alias="outputKey")


class TransformNode(BaseModel):
    id: str
    type: Literal["transform"]
    position: Position
    data: TransformNodeData


# ─── data-transform (Phase 4) ────────────────────────────────────────────


class DataTransformNodeData(BaseNodeData):
    # extra="forbid": no known legacy-key collision exists in the DB today
    # (the panel already wrote `expression` under its canonical name), but
    # this node type is now fully contract-audited (P0-0) so future drift
    # should fail loudly instead of silently.
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

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
    # extra="forbid": see the identical comment on HttpNodeData. The
    # Designer's Extract panel used to write `inputVariable`/`schema`
    # instead of `input`/`jsonSchema`. See P0-0 in
    # docs/claude-improvement-backlog.md. extract_config/extract_tool
    # below are declared but never read by ExtractExecutor — confirmed
    # dead per the audit, kept as-is pending a separately-approved
    # cleanup rather than removed here.
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

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
    # extra="forbid" (overriding BaseNodeData's extra="allow"): this node
    # type is fully contract-audited, so an unrecognized field is a bug —
    # e.g. the Designer's HTTP panel used to write `method`/`url` instead
    # of `httpMethod`/`httpUrl` and extra="allow" let it pass through
    # silently, leaving the real fields empty. See P0-0 in
    # docs/claude-improvement-backlog.md.
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

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
    model_config = ConfigDict(populate_by_name=True)

    pii_enabled: bool = Field(default=False, alias="piiEnabled")
    moderation_enabled: bool = Field(default=False, alias="moderationEnabled")
    jailbreak_enabled: bool = Field(default=False, alias="jailbreakEnabled")
    hallucination_enabled: bool = Field(default=False, alias="hallucinationEnabled")
    action_on_violation: Literal["block", "warn"] = Field(default="warn", alias="actionOnViolation")
    model: str | None = Field(default=None, alias="model")


class GuardrailsNode(BaseModel):
    id: str
    type: Literal["guardrails"]
    position: Position
    data: GuardrailsNodeData


# ─── vector-db (Phase 6) ─────────────────────────────────────────────────

VectorDbProvider = Literal["pinecone", "qdrant", "chroma", "weaviate", "milvus"]
EmbeddingProvider = Literal[
    "openai",
    "dashscope",
    "siliconflow",
    "zhipu",
    "cohere",
    "jina",
    "voyage",
    "pinecone-inference",
    "custom-openai-compatible",
]


class VectorDbNodeData(BaseNodeData):
    model_config = ConfigDict(populate_by_name=True)

    provider: VectorDbProvider = Field(default="pinecone", alias="vectorDbProvider")
    endpoint: str = Field(default="", alias="vectorDbEndpoint")
    api_key: str = Field(default="", alias="vectorDbApiKey")
    collection: str = Field(default="", alias="vectorDbCollection")
    dimension: int = Field(default=1536, alias="vectorDbDimension")
    embedding_provider: EmbeddingProvider = Field(
        default="openai", alias="vectorDbEmbeddingProvider"
    )
    embedding_model: str = Field(default="text-embedding-3-small", alias="vectorDbEmbeddingModel")
    embedding_api_key: str = Field(default="", alias="vectorDbEmbeddingApiKey")
    embedding_base_url: str = Field(default="", alias="vectorDbEmbeddingBaseUrl")
    query_prompt: str = Field(default="", alias="vectorDbQueryPrompt")
    top_k: int = Field(default=5, alias="vectorDbTopK")
    score_threshold: float = Field(default=0.0, alias="vectorDbScoreThreshold")
    namespace: str | None = Field(default=None, alias="vectorDbNamespace")
    include_metadata: bool = Field(default=True, alias="vectorDbIncludeMetadata")
    include_vector: bool = Field(default=False, alias="vectorDbIncludeVector")
    text_field: str | None = Field(default=None, alias="vectorDbTextField")
    output_variable: str = Field(default="vectorDbResults", alias="vectorDbOutputVariable")
    metadata_filter: str | None = Field(default=None, alias="vectorDbMetadataFilter")
    join_results: bool = Field(default=False, alias="vectorDbJoinResults")
    join_separator: str = Field(default="----", alias="vectorDbJoinSeparator")
    join_prefix: str = Field(default="", alias="vectorDbJoinPrefix")
    join_suffix: str = Field(default="", alias="vectorDbJoinSuffix")
    # ─── Insert/upsert mode (Phase 6e+ insert) ──────────────────────
    # `query` (default) keeps the pre-existing retrieval path unchanged;
    # `upsert` switches the executor into ingest mode where it embeds
    # the supplied documents and writes them to the configured collection.
    operation: str = Field(default="query", alias="vectorDbOperation")
    # `documents` is a simpleeval expression evaluated against state to
    # yield either:
    #   - a list of dicts: [{id?, text, metadata?}, ...]  (pre-chunked)
    #   - a single string                                 (auto-chunked
    #     by the executor using chunk_size / chunk_overlap below)
    # When the expression yields nothing or unset, upsert is rejected
    # with a clear error.
    documents: str | None = Field(default=None, alias="vectorDbDocuments")
    chunk_size: int = Field(default=1000, alias="vectorDbChunkSize")
    chunk_overlap: int = Field(default=100, alias="vectorDbChunkOverlap")


class VectorDbNode(BaseModel):
    id: str
    type: Literal["vector-db"]
    position: Position
    data: VectorDbNodeData


# ─── gamma-ai (Phase 6) ──────────────────────────────────────────────────


class GammaAiNodeData(BaseNodeData):
    model_config = ConfigDict(populate_by_name=True)

    prompt: str | None = Field(default=None)
    format: Literal["presentation", "document", "social"] = Field(default="presentation")
    text_mode: Literal["generate", "condense", "preserve"] = Field(
        default="generate", alias="textMode"
    )
    num_cards: int | None = Field(default=None, alias="numCards")
    text_amount: Literal["brief", "medium", "detailed"] | None = Field(
        default=None, alias="textAmount"
    )
    image_source: str | None = Field(default=None, alias="imageSource")
    language: str | None = Field(default=None)
    export_as: Literal["pptx", "pdf", "web"] = Field(default="web", alias="exportAs")


class GammaAiNode(BaseModel):
    id: str
    type: Literal["gamma-ai"]
    position: Position
    data: GammaAiNodeData


# ─── arcade (Phase 6) ────────────────────────────────────────────────────


JIRA_TOKEN_ENC_PREFIX = "enc:v1:"
JIRA_TOKEN_REDACTED = "••••••••"


def is_jira_api_token_encrypted(value: str) -> bool:
    return value.startswith(JIRA_TOKEN_ENC_PREFIX)


def encrypt_jira_api_token(plaintext: str) -> str:
    """Encrypt a Jira API token for storage in the workflow's `nodes` JSON."""
    from src.security.encryption import encrypt

    return JIRA_TOKEN_ENC_PREFIX + encrypt(plaintext)


def decrypt_jira_api_token(value: str) -> str:
    """Reverse of encrypt_jira_api_token(); returns unencrypted values as-is
    so tokens stored before this fix shipped keep working without a backfill."""
    from src.security.encryption import decrypt

    if not is_jira_api_token_encrypted(value):
        return value
    return decrypt(value[len(JIRA_TOKEN_ENC_PREFIX) :])


class JiraNodeData(BaseNodeData):
    domain: str | None = None
    email: str | None = None
    api_token: str | None = Field(default=None, alias="apiToken")
    instructions: str | None = None
    model: str | None = None
    max_iterations: int | None = Field(default=None, alias="maxIterations")
    # Semantic success policy (P0-2): distinguishes "the agent produced a
    # response" from "the agent actually performed the requested Jira
    # action" — a clarification/explanation text response can otherwise
    # complete a node with zero Jira issues created. Default preserves
    # existing behavior (no enforcement) for backward compatibility.
    action_policy: Literal["best_effort", "require_tool_call", "require_successful_tool_call"] = (
        Field(default="best_effort", alias="actionPolicy")
    )
    minimum_successful_calls: int = Field(default=1, alias="minimumSuccessfulCalls")
    # operation="agent" (default) is the original LLM tool-calling loop,
    # unchanged. operation="extract" is deterministic, paginated JQL search
    # with no LLM call at all — see src/executors/jira.py's _run_extract.
    # Every existing saved workflow has no `operation` key at all and must
    # keep behaving exactly as before.
    operation: Literal["agent", "extract"] = "agent"
    jql: str | None = None
    fields: list[str] | None = None
    expand_changelog: bool = Field(default=True, alias="expandChangelog")
    max_issues: int = Field(default=1000, ge=1, le=5000, alias="maxIssues")


class JiraNode(BaseModel):
    id: str
    type: Literal["jira"]
    position: Position
    data: JiraNodeData


class EmailNodeData(BaseNodeData):
    model_config = ConfigDict(populate_by_name=True)

    provider: Literal["resend"] = Field(default="resend", alias="emailProvider")
    from_email: str = Field(default="", alias="emailFrom")
    to: str = Field(default="", alias="emailTo")
    cc: str | None = Field(default=None, alias="emailCc")
    bcc: str | None = Field(default=None, alias="emailBcc")
    reply_to: str | None = Field(default=None, alias="emailReplyTo")
    subject: str = Field(default="", alias="emailSubject")
    body: str = Field(default="", alias="emailBody")
    body_type: Literal["text", "html"] = Field(default="html", alias="emailBodyType")
    idempotency_key: str | None = Field(default=None, alias="emailIdempotencyKey")


class EmailNode(BaseModel):
    id: str
    type: Literal["email"]
    position: Position
    data: EmailNodeData


class ArcadeNodeData(BaseNodeData):
    # extra="forbid": see the identical comment on HttpNodeData above — the
    # Designer's Arcade panel used to write `toolName`/`args` instead of
    # `arcadeTool`/`arcadeInput`. See P0-0 in
    # docs/claude-improvement-backlog.md.
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    tool: str = Field(alias="arcadeTool")
    input: dict[str, Any] = Field(default_factory=dict, alias="arcadeInput")
    user_id: str = Field(default="workflow-builder", alias="arcadeUserId")


class ArcadeNode(BaseModel):
    id: str
    type: Literal["arcade"]
    position: Position
    data: ArcadeNodeData


# ─── join-chunks (Phase 6) ───────────────────────────────────────────────


class JoinChunksNodeData(BaseNodeData):
    # extra="forbid": see the identical comment on HttpNodeData. The
    # Designer's Join Chunks panel used to write inputVariable/separator/
    # prefix/suffix instead of the canonical joinChunks*-prefixed
    # aliases. See P0-0 in docs/claude-improvement-backlog.md.
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    variable: str = Field(alias="joinChunksVariable")
    separator: str = Field(default="\n\n", alias="joinChunksSeparator")
    prefix: str = Field(default="", alias="joinChunksPrefix")
    suffix: str = Field(default="", alias="joinChunksSuffix")
    include_metadata: bool = Field(default=False, alias="joinChunksIncludeMetadata")


class JoinChunksNode(BaseModel):
    id: str
    type: Literal["join-chunks"]
    position: Position
    data: JoinChunksNodeData


# ─── confluence (foundation) ─────────────────────────────────────────────


class ConfluenceNodeData(BaseNodeData):
    domain: str | None = None
    email: str | None = None
    api_token: str | None = Field(default=None, alias="apiToken")
    operation: Literal["create_or_update_page", "get_page", "get_property", "set_property"] = (
        "create_or_update_page"
    )
    # create_or_update_page / get_page
    space_key: str | None = Field(default=None, alias="spaceKey")
    parent_page_id: str | None = Field(default=None, alias="parentPageId")
    title: str | None = None
    body_storage_html: str | None = Field(default=None, alias="bodyStorageHtml")
    labels: list[str] | None = None
    # get_property / set_property
    page_id: str | None = Field(default=None, alias="pageId")
    property_key: str | None = Field(default=None, alias="propertyKey")
    property_value: Any | None = Field(default=None, alias="propertyValue")


class ConfluenceNode(BaseModel):
    id: str
    type: Literal["confluence"]
    position: Position
    data: ConfluenceNodeData


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
    | FileTriggerNode
    | FileWriteNode
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
    | EmailNode
    | ArcadeNode
    | JoinChunksNode
    | ConfluenceNode
    | JiraNode,
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
    "ConfluenceNode",
    "ConfluenceNodeData",
    "DataTransformNode",
    "DataTransformNodeData",
    "EmailNode",
    "EmailNodeData",
    "EmbeddingProvider",
    "EndNode",
    "EndNodeData",
    "ExtractNode",
    "ExtractNodeData",
    "FileTriggerNode",
    "FileTriggerNodeData",
    "FileWriteNode",
    "FileWriteNodeData",
    "GammaAiNode",
    "GammaAiNodeData",
    "GuardrailsNode",
    "GuardrailsNodeData",
    "HttpNode",
    "HttpNodeData",
    "IfElseNode",
    "IfElseNodeData",
    "JiraNode",
    "JiraNodeData",
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
    "VectorDbProvider",
    "WhileNode",
    "WhileNodeData",
    "Workflow",
    "WorkflowEdge",
    "WorkflowNode",
]
