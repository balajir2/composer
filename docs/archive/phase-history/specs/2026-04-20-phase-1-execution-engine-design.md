# Phase 1 — Execution Engine Core: Design Spec

**Author:** Balaji Rajan
**Date:** 2026-04-20
**Status:** Approved (this spec) → ready for implementation plan
**Phase ref:** Phase 1 of [`docs/design/2026-04-20-composer-python-port-design.md`](../../design/2026-04-20-composer-python-port-design.md)
**Governing ADRs:** [ADR-0001](../../design/decisions.md#adr-0001-langgraph-checkpointer--prisma-owns-schema-thin-custom-saver), [ADR-0002](../../design/decisions.md#adr-0002-workflow-json-schema--full-oab-fidelity-in-phase-1), [ADR-0004](../../design/decisions.md#adr-0004-local-dev-postgres--neon-only), [ADR-0005](../../design/decisions.md#adr-0005-phase-1-api-surface--authentication)

---

## 1. Goal

Stand up the Composer execution engine end-to-end for the trivial case: a user POSTs a workflow JSON whose graph is `start → end`, POSTs an execution against it, and retrieves the completed execution record. The scaffolding put in place (Prisma schema, LangGraph-backed executor, Prisma-backed checkpointer, Pydantic models for all 18 node types) is the foundation every subsequent phase plugs into.

**Phase 1 is done when:**

1. All four Prisma tables — `Workflow`, `WorkflowExecution`, `LangGraphCheckpoint`, `LangGraphCheckpointWrite` — are declared, migrated, and exercised against a real Postgres (Neon or CI-service container).
2. Pydantic models exist for `Workflow`, `WorkflowEdge`, and all 18 `WorkflowNode` variants as a discriminated union.
3. `graph_builder` compiles a `Workflow` into a LangGraph `StateGraph` for the supported subset (linear `start → end`). Unsupported node types (all 16 others) and unsupported edge patterns (conditional edges) raise precise `NotImplementedError`s naming the owning phase.
4. `Start` and `End` executors are implemented per OAB's behavior at `lib/workflow/langgraph.ts:569-654`.
5. `PrismaCheckpointSaver` implements LangGraph's `BaseCheckpointSaver` interface (`aget_tuple`, `alist`, `aput`, `aput_writes`) against the Prisma tables.
6. Three REST endpoints — `POST /workflows`, `POST /executions`, `GET /executions/{id}` — are wired to the engine.
7. JWT primitives exist in `src/security/jwt.py` (not wired to routes yet per ADR-0005).
8. An integration test runs a minimal `start → end` workflow end-to-end via HTTP against a real Postgres and asserts the execution completes with `status="completed"` and the expected `output.finalOutput`.
9. At least one OAB regression test covering `start → end` behavior is ported to pytest and passing.
10. `uv run ruff check && uv run ruff format --check && uv run pyright && uv run pytest` all pass.
11. `CHANGELOG.md` entry for Phase 1 reflects the work; [`CLAUDE.md`](../../../CLAUDE.md)'s phase table shows Phase 1 ✅.

**Non-goals for Phase 1** (each has its own later phase):
- Executors for any node type other than `start`/`end`/`note`-skip
- Conditional edges, parallel routing, loops
- Variable substitution / templating (Phase 2)
- Authentication middleware on routes (Phase 7)
- SSE streaming of events (Phase 5)
- Workflow UPDATE / DELETE / LIST endpoints (Phase 7)
- MCP, OAuth, approval, guardrails, HTTP, transform, extract, vector-db, Gamma, Arcade — all their own phases

---

## 2. Context

Phase 0 shipped the FastAPI skeleton, an empty Prisma schema with a `ComposerBootstrap` placeholder model, `/health`, CI, and dependencies. This spec begins from that baseline.

Phase 1 is referenced from the master design at [`docs/design/2026-04-20-composer-python-port-design.md`](../../design/2026-04-20-composer-python-port-design.md#phase-1--execution-engine-core-week-15-1-week). ADRs 0001–0005 ship alongside this spec (in the same commit) and resolve the questions the master design left open.

---

## 3. Behavioral reference from OAB

All runtime behavior in this phase mirrors OAB's TypeScript implementation at `D:/GitHub/open-agent-builder` (read-only per [CLAUDE.md](../../../CLAUDE.md) Rule 2). Specific references used by this spec:

- **Workflow row:** `convex/schema.ts:24-57` (Convex) and `lib/workflow/types.ts:160-172` (TS types)
- **Node types + data shapes:** `lib/workflow/types.ts:3-138`
- **Edge shape:** `lib/workflow/types.ts:151-158`
- **Execution row:** `convex/schema.ts:60-85` (Convex) and `lib/workflow/types.ts:174-210` (TS)
- **WorkflowState annotation (reducers):** `lib/workflow/langgraph.ts:52-88`
- **Start executor:** `lib/workflow/langgraph.ts:569-588`
- **End executor:** `lib/workflow/langgraph.ts:653-654`
- **Graph builder:** `lib/workflow/langgraph.ts:169-427`
- **Checkpointer:** `lib/workflow/convex-checkpointer.ts` (reference only — our impl is Prisma-backed, not Convex)

Composer does **not** port the Convex checkpointer's size-truncation logic in Phase 1; Postgres BYTEA can hold the full blob. If truncation becomes necessary (unlikely until real workflows run), it lands as a follow-on decision with its own ADR.

---

## 4. Prisma schema

The placeholder `ComposerBootstrap` model is removed. The following four models are added.

### 4.1 `Workflow`

Stores authored graphs. Columns mirror OAB's Convex workflow row.

```prisma
model Workflow {
  id            String   @id @default(cuid())
  userId        String?  // Phase 7: tied to authenticated identity; null in Phase 1 = "dev"
  name          String
  description   String?
  category      String?
  tags          String[]
  difficulty    String?
  estimatedTime String?  @map("estimated_time")
  nodes         Json     // WorkflowNode[] — validated by Pydantic on create
  edges         Json     // WorkflowEdge[]
  version       String?
  isTemplate    Boolean  @default(false) @map("is_template")
  isPublic      Boolean  @default(false) @map("is_public")
  createdAt     DateTime @default(now()) @map("created_at")
  updatedAt     DateTime @updatedAt      @map("updated_at")

  executions    WorkflowExecution[]

  @@map("workflows")
  @@index([userId])
}
```

Design notes:
- `nodes` and `edges` are stored as `Json` rather than normalized into separate tables. Two reasons: (1) this mirrors OAB's storage shape exactly, preserving round-trip fidelity for workflow JSON; (2) the graph is always read/written atomically — there's no query pattern that benefits from normalization.
- `@map` on column names converts Pydantic/Python camelCase to Postgres snake_case; model/relation names stay PascalCase in generated client.
- `customId` and `assignedTo` from OAB's Convex schema are omitted in Phase 1 — they're used only by OAB's template-seeding flow, which Composer addresses in Phase 7 (bootstrap script).

### 4.2 `WorkflowExecution`

One row per execution attempt. Columns mirror OAB plus LangGraph's `thread_id`.

```prisma
model WorkflowExecution {
  id            String    @id @default(cuid())
  workflowId    String    @map("workflow_id")
  userId        String?   @map("user_id")
  status        String    // running | completed | failed | paused | waiting_auth
  currentNodeId String?   @map("current_node_id")
  nodeResults   Json      @map("node_results")    // {[nodeId]: NodeExecutionResult}
  variables     Json                              // state variables snapshot
  input         Json?
  output        Json?
  error         String?
  startedAt     DateTime  @default(now()) @map("started_at")
  completedAt   DateTime? @map("completed_at")
  threadId      String    @unique         @map("thread_id")  // LangGraph thread_id

  workflow      Workflow  @relation(fields: [workflowId], references: [id], onDelete: Cascade)

  @@map("workflow_executions")
  @@index([workflowId])
  @@index([userId])
}
```

Design notes:
- `threadId` is the authoritative link to LangGraph checkpoints. It is generated server-side at creation time (UUIDv4) and never changes for the lifetime of the execution.
- `status` is a string, not an enum type, matching OAB's shape. We define a Pydantic `Literal` type on the read side for type safety.
- `status` values: `"running"`, `"completed"`, `"failed"`. `"paused"` and `"waiting_auth"` are used from Phase 5 onward and added to the Literal then.

### 4.3 `LangGraphCheckpoint`

Stores LangGraph checkpoints. Shape is dictated by LangGraph's `BaseCheckpointSaver` contract.

```prisma
model LangGraphCheckpoint {
  threadId           String   @map("thread_id")
  checkpointNs       String   @default("") @map("checkpoint_ns")
  checkpointId       String   @map("checkpoint_id")
  parentCheckpointId String?  @map("parent_checkpoint_id")
  checkpoint         Bytes    // JsonPlusSerializer output
  metadata           Bytes
  createdAt          DateTime @default(now()) @map("created_at")

  @@id([threadId, checkpointNs, checkpointId])
  @@map("langgraph_checkpoints")
  @@index([threadId])
}
```

### 4.4 `LangGraphCheckpointWrite`

Stores intermediate writes produced during a checkpoint.

```prisma
model LangGraphCheckpointWrite {
  threadId     String   @map("thread_id")
  checkpointNs String   @default("") @map("checkpoint_ns")
  checkpointId String   @map("checkpoint_id")
  taskId       String   @map("task_id")
  idx          Int
  channel      String
  value        Bytes
  createdAt    DateTime @default(now()) @map("created_at")

  @@id([threadId, checkpointNs, checkpointId, taskId, idx])
  @@map("langgraph_checkpoint_writes")
  @@index([threadId, checkpointId])
}
```

### 4.5 Migration

One Prisma migration, named `phase_1_execution_engine`, containing all four tables and dropping `ComposerBootstrap`. Reviewed before apply.

---

## 5. Pydantic models

### 5.1 Module layout

```
src/
├── engine/
│   ├── __init__.py
│   ├── state.py              # WorkflowState TypedDict + reducers
│   ├── workflow.py           # Workflow, WorkflowEdge, and 18 node-type models
│   ├── graph_builder.py      # Workflow JSON → LangGraph StateGraph
│   └── langgraph_executor.py # Orchestrator: create execution, run graph, update row
├── executors/
│   ├── __init__.py
│   ├── base.py               # Executor protocol + registry
│   ├── start.py
│   └── end.py
├── storage/
│   ├── __init__.py
│   ├── db.py                 # Prisma client lifecycle (attached to FastAPI lifespan)
│   └── checkpointer.py       # PrismaCheckpointSaver
├── security/
│   ├── __init__.py
│   └── jwt.py                # create/verify access tokens (not wired to routes in P1)
└── api/
    ├── __init__.py
    ├── workflows.py          # POST /workflows
    └── executions.py         # POST /executions, GET /executions/{id}
```

`src/main.py` is updated to register the two routers, attach the Prisma client to the app lifespan, and wire the checkpointer into a module-level engine instance.

### 5.2 `engine/workflow.py` — discriminated-union node models

Each node type has a Pydantic data class. The 18 discriminator strings (verbatim on the wire) are:

```
start, end, note,
agent, mcp, extract, arcade, gamma-ai,
if-else, while, user-approval,
transform, data-transform, set-state, http,
guardrails, vector-db, join-chunks
```

Sketch (illustrative — not the final code, but the shape):

```python
from typing import Annotated, Any, Literal, Union
from pydantic import BaseModel, Field

class Position(BaseModel):
    x: float
    y: float

class BaseNodeData(BaseModel):
    label: str
    node_type: str | None = Field(default=None, alias="nodeType")
    node_name: str | None = Field(default=None, alias="nodeName")

class StartInputVariable(BaseModel):
    name: str
    type: str
    required: bool
    description: str
    default_value: Any | None = Field(default=None, alias="defaultValue")

class StartNodeData(BaseNodeData):
    input_variables: list[StartInputVariable] = Field(
        default_factory=list, alias="inputVariables"
    )

class StartNode(BaseModel):
    id: str
    type: Literal["start"]
    position: Position
    data: StartNodeData

class EndNodeData(BaseNodeData):
    pass

class EndNode(BaseModel):
    id: str
    type: Literal["end"]
    position: Position
    data: EndNodeData

# ... 16 more analogously: NoteNode, AgentNode, McpNode, IfElseNode, WhileNode,
# UserApprovalNode, TransformNode, DataTransformNode, SetStateNode, ExtractNode,
# HttpNode, GuardrailsNode, VectorDbNode, GammaAiNode, ArcadeNode, JoinChunksNode

WorkflowNode = Annotated[
    Union[
        StartNode, EndNode, NoteNode, AgentNode, McpNode,
        IfElseNode, WhileNode, UserApprovalNode,
        TransformNode, DataTransformNode, SetStateNode,
        ExtractNode, HttpNode, GuardrailsNode,
        VectorDbNode, GammaAiNode, ArcadeNode, JoinChunksNode,
    ],
    Field(discriminator="type"),
]

class WorkflowEdge(BaseModel):
    id: str
    source: str
    target: str
    type: str | None = None
    label: str | None = None
    source_handle: str | None = Field(default=None, alias="sourceHandle")

class Workflow(BaseModel):
    id: str | None = None
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

    model_config = {"populate_by_name": True}
```

Fidelity rules:
- Each `*NodeData` class is populated with fields read directly from `lib/workflow/types.ts` during implementation. Where a field's type is ambiguous (TypeScript `any`, loose shapes), we default to `dict[str, Any]` for that specific field with a `# TODO(phase-N)` comment naming the phase that tightens it.
- Aliases preserve OAB's camelCase on the wire while we use snake_case in Python. `populate_by_name=True` allows either.
- The models enforce `type` exhaustiveness via `Literal` — an unknown `type` value fails validation at schema level, before any executor code runs.

### 5.3 `engine/state.py` — LangGraph state with reducers

LangGraph requires a `TypedDict` with `Annotated[T, reducer]` fields to define state merging behavior. Mirrors OAB's `WorkflowStateAnnotation` (`lib/workflow/langgraph.ts:52-88`).

```python
from operator import add
from typing import Annotated, Any, TypedDict

def merge_dict(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    return {**left, **right}

def last_wins(_left: Any, right: Any) -> Any:
    return right

class ChatMessage(TypedDict):
    role: str
    content: str

class NodeExecutionResult(TypedDict, total=False):
    node_id: str
    status: str
    input: Any
    output: Any
    error: str | None
    started_at: str
    completed_at: str
    usage: dict[str, int]

class WorkflowStateDict(TypedDict):
    variables: Annotated[dict[str, Any], merge_dict]
    chat_history: Annotated[list[ChatMessage], add]
    current_node_id: Annotated[str, last_wins]
    node_results: Annotated[dict[str, NodeExecutionResult], merge_dict]
    pending_auth: Annotated[dict[str, Any] | None, last_wins]
    loop_results: Annotated[list[Any], add]
```

Initial state defaults match OAB: `variables = {"input": "", "lastOutput": ""}`, all lists empty, `current_node_id = ""`, `pending_auth = None`.

---

## 6. Graph builder

`src/engine/graph_builder.py` compiles a `Workflow` (Pydantic) into a compiled LangGraph `CompiledStateGraph`. Faithful port of OAB's algorithm at `lib/workflow/langgraph.ts:169-427`, scoped to Phase 1 behavior.

### 6.1 Algorithm

```
def build_graph(workflow: Workflow, checkpointer: BaseCheckpointSaver) -> CompiledStateGraph:
    1. validate_workflow_shape(workflow)
        - exactly one start node
        - at least one end node
        - all edge source/target IDs exist in nodes[]
        - all node IDs are unique
    2. reachability_check(workflow)
        - BFS from start node following edges
        - every non-note node must be reachable; else raise WorkflowValidationError
    3. builder = StateGraph(WorkflowStateDict)
    4. for node in workflow.nodes:
        - if type == "note": skip (visual only)
        - executor = executor_registry.build(node)    # may raise NotImplementedError
        - builder.add_node(node.id, executor.arun)
    5. for edge in workflow.edges:
        - source_node = nodes_by_id[edge.source]
        - if source_node.type in {"if-else", "while", "user-approval"}:
            raise NotImplementedError("Conditional edges land in Phase 4/5 ...")
        - else:
            builder.add_edge(edge.source, edge.target)
    6. start_node_id = single start node id
    7. end_node_ids = [n.id for n in nodes if n.type == "end"]
    8. builder.add_edge(START, start_node_id)
    9. for end_id in end_node_ids:
           builder.add_edge(end_id, END)
    10. return builder.compile(checkpointer=checkpointer)
```

### 6.2 Executor registry

`src/executors/base.py` defines:

```python
from typing import Protocol, ClassVar
from engine.workflow import WorkflowNode

class Executor(Protocol):
    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]: ...

_EXECUTORS: dict[str, type[Executor]] = {}

def register(node_type: str):
    def _wrap(cls):
        _EXECUTORS[node_type] = cls
        return cls
    return _wrap

_PHASE_FOR_TYPE = {
    # Phase 1 — implemented
    "start": 1, "end": 1,
    # Phase 2
    "agent": 2,
    # Phase 3
    "mcp": 3,
    # Phase 4
    "http": 4, "transform": 4, "data-transform": 4,
    "extract": 4, "if-else": 4, "while": 4, "set-state": 4,
    # Phase 5
    "user-approval": 5,
    # Phase 6
    "guardrails": 6, "note": 6, "vector-db": 6,
    "gamma-ai": 6, "arcade": 6, "join-chunks": 6,
}

def build(node: WorkflowNode) -> Executor:
    cls = _EXECUTORS.get(node.type)
    if cls is None:
        phase = _PHASE_FOR_TYPE.get(node.type, "a later phase")
        raise NotImplementedError(
            f"Executor for node type {node.type!r} lands in Phase {phase}."
        )
    return cls(node)
```

Note nodes are visual-only (matching OAB's `langgraph.ts:215-218` skip behavior). `graph_builder` skips them at step 4 of §6.1, so the executor registry is never consulted for `"note"` — Phase 1 supports notes in workflows out of the gate. Phase 6 formalizes a `NoteExecutor` class for symmetry and in case future behavior is added (e.g., documentation generation from notes); until then the skip-at-build-time path is sufficient.

### 6.3 Validation errors

Custom exception type in `engine/graph_builder.py`:

```python
class WorkflowValidationError(ValueError):
    """Raised when a workflow's shape is invalid before execution."""
```

Specific error messages:
- `"Workflow must contain exactly one start node (found: 0)."`
- `"Workflow must contain exactly one start node (found: 3)."`
- `"Workflow must contain at least one end node."`
- `"Edge {edge.id!r} references unknown source node id {edge.source!r}."`
- `"Edge {edge.id!r} references unknown target node id {edge.target!r}."`
- `"Node id {node.id!r} is duplicated."`
- `"Node {node.id!r} ({node.type!r}) is unreachable from the start node."`

These messages are part of the Phase 1 contract and are asserted by tests.

---

## 7. Start and End executors

### 7.1 `src/executors/start.py`

Port of OAB `lib/workflow/langgraph.ts:569-588`.

```python
import json
from typing import Any
from engine.state import WorkflowStateDict
from engine.workflow import StartNode
from executors.base import register

@register("start")
class StartExecutor:
    def __init__(self, node: StartNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        raw_input = state["variables"].get("input")
        parsed = raw_input
        if isinstance(raw_input, str):
            try:
                parsed = json.loads(raw_input)
            except (json.JSONDecodeError, ValueError):
                parsed = raw_input

        if isinstance(parsed, dict):
            merged_vars = {**parsed, "lastOutput": parsed}
        else:
            merged_vars = {"input": parsed, "lastOutput": parsed}

        return {
            "variables": merged_vars,
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "input": raw_input,
                    "output": parsed,
                }
            },
        }
```

**Deliberate clarification vs OAB.** The OAB TS Start executor returns `{ message: 'Workflow started', ...parsedInput }` — the `message` key and any spread keys don't match state-annotation fields, so they're dropped by LangGraph's reducer machinery. Observable end-to-end behavior is: initial input becomes available under `state.variables`, and downstream nodes read `state.variables.lastOutput` (set by each executor as it runs). For a literal `start → end` workflow with no intermediate node to set `lastOutput`, OAB's End emits `finalOutput: ""` (empty default).

Composer is explicit: Start writes both the parsed input fields to `variables` **and** sets `variables.lastOutput = parsed`. This means a direct `start → end` workflow produces `finalOutput = parsed_input` rather than empty string. This is a **deliberate deviation** — more useful, same behavior for non-trivial workflows where an intermediate node would set `lastOutput` anyway. Logged as an OAB-vs-Composer delta in [CHANGELOG.md](../../../CHANGELOG.md) once Phase 1 lands. Regression tests that assert empty `finalOutput` on direct start→end workflows are updated when ported.

### 7.2 `src/executors/end.py`

Port of OAB `lib/workflow/langgraph.ts:653-654`.

```python
from typing import Any
from engine.state import WorkflowStateDict
from engine.workflow import EndNode
from executors.base import register

@register("end")
class EndExecutor:
    def __init__(self, node: EndNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        last_output = state["variables"].get("lastOutput")
        return {
            "variables": {"finalOutput": last_output},
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "output": last_output,
                }
            },
        }
```

---

## 8. Prisma-backed checkpointer

`src/storage/checkpointer.py`. Implements LangGraph's `BaseCheckpointSaver` (async variant) against the Prisma client.

### 8.1 Serialization

Uses LangGraph's `JsonPlusSerializer` unchanged. It produces `bytes` which map directly to Prisma `Bytes` columns.

### 8.2 Interface

```python
from collections.abc import AsyncIterator
from typing import Any
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver, Checkpoint, CheckpointMetadata, CheckpointTuple,
)
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from prisma import Prisma

class PrismaCheckpointSaver(BaseCheckpointSaver):
    def __init__(self, db: Prisma) -> None:
        super().__init__(serde=JsonPlusSerializer())
        self.db = db

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None: ...
    async def alist(
        self, config: RunnableConfig, *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]: ...
    async def aput(
        self, config: RunnableConfig,
        checkpoint: Checkpoint, metadata: CheckpointMetadata,
        new_versions: dict[str, str | int | float],
    ) -> RunnableConfig: ...
    async def aput_writes(
        self, config: RunnableConfig,
        writes: list[tuple[str, Any]], task_id: str,
    ) -> None: ...

    # Sync shims required by BaseCheckpointSaver: raise NotImplementedError.
    # All our call sites use the async API.
    def get_tuple(self, config): raise NotImplementedError("Use aget_tuple")
    def list(self, config, **kw): raise NotImplementedError("Use alist")
    def put(self, *a, **kw): raise NotImplementedError("Use aput")
    def put_writes(self, *a, **kw): raise NotImplementedError("Use aput_writes")
```

### 8.3 Implementation notes

- `aput` performs an upsert keyed by `(thread_id, checkpoint_ns, checkpoint_id)`. The `serde` serializes `checkpoint` and `metadata` to bytes before write.
- `aput_writes` upserts per `(thread_id, checkpoint_ns, checkpoint_id, task_id, idx)`.
- `aget_tuple` with no `checkpoint_id` returns the most recent checkpoint for the thread (order by `created_at DESC` limit 1). With a specific `checkpoint_id`, returns that exact row. It also eagerly loads the matching writes.
- `alist` supports the `before` config (return checkpoints strictly before the given `checkpoint_id`'s `created_at`) and `limit`.
- Errors during checkpointing are raised (not swallowed). Composer is not bandwidth-constrained the way Convex was — a checkpoint write failure is a real failure and should be visible, not silenced.

### 8.4 Lifecycle integration

The Prisma client is a singleton attached to the FastAPI app's lifespan:

```python
# src/main.py (extended)
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    db = Prisma()
    await db.connect()
    app.state.db = db
    app.state.checkpointer = PrismaCheckpointSaver(db)
    try:
        yield
    finally:
        await db.disconnect()
```

Routes and the engine read `app.state.db` / `app.state.checkpointer` via FastAPI `Depends()`.

---

## 9. Orchestrator — `langgraph_executor.py`

`src/engine/langgraph_executor.py` is the glue between HTTP endpoints and the compiled LangGraph. Responsibilities:

- Create a `WorkflowExecution` Prisma row (`status="running"`, generated `thread_id`).
- Compile the workflow's graph (via `graph_builder.build_graph`).
- Invoke `compiled.ainvoke(initial_state, config={"configurable": {"thread_id": ...}})`.
- On completion, update the row: `status="completed"`, `output=final_state["variables"].get("finalOutput")`, `variables=final_state["variables"]`, `node_results=final_state["node_results"]`, `completed_at=now()`.
- On exception, update: `status="failed"`, `error=str(exc)`, `completed_at=now()`, and log the traceback.

Phase 1 runs executions synchronously inside a FastAPI `BackgroundTasks` handler. The API returns `status="running"` immediately; the caller polls `GET /executions/{id}` for completion. SSE/streaming lands in Phase 5.

Pseudocode:

```python
class LangGraphExecutor:
    def __init__(self, db: Prisma, checkpointer: BaseCheckpointSaver) -> None:
        self.db = db
        self.checkpointer = checkpointer

    async def start_execution(
        self, workflow_id: str, input: Any, user_id: str | None,
    ) -> WorkflowExecutionRead:
        thread_id = str(uuid.uuid4())
        execution = await self.db.workflowexecution.create(
            data={
                "workflowId": workflow_id,
                "userId": user_id,
                "status": "running",
                "threadId": thread_id,
                "input": input,
                "nodeResults": {},
                "variables": {},
            },
        )
        return execution

    async def run(self, execution_id: str) -> None:
        execution = await self.db.workflowexecution.find_unique(where={"id": execution_id})
        workflow_row = await self.db.workflow.find_unique(where={"id": execution.workflow_id})
        workflow = Workflow.model_validate({**workflow_row.model_dump(), "nodes": workflow_row.nodes, "edges": workflow_row.edges})
        try:
            compiled = build_graph(workflow, self.checkpointer)
            initial_state = {"variables": {"input": execution.input or "", "lastOutput": ""}, ...}
            final_state = await compiled.ainvoke(
                initial_state,
                config={"configurable": {"thread_id": execution.thread_id}},
            )
            await self.db.workflowexecution.update(
                where={"id": execution_id},
                data={
                    "status": "completed",
                    "output": final_state["variables"].get("finalOutput"),
                    "variables": final_state["variables"],
                    "nodeResults": final_state["node_results"],
                    "completedAt": datetime.now(timezone.utc),
                },
            )
        except Exception as exc:
            logger.exception("Execution %s failed", execution_id)
            await self.db.workflowexecution.update(
                where={"id": execution_id},
                data={
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                    "completedAt": datetime.now(timezone.utc),
                },
            )
```

---

## 10. REST API

### 10.1 `POST /workflows`

**Request:**

```json
{
  "name": "Start-to-End Smoke Test",
  "description": "Minimal workflow for Phase 1 validation",
  "nodes": [
    {"id": "n1", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "Start"}},
    {"id": "n2", "type": "end",   "position": {"x": 200, "y": 0}, "data": {"label": "End"}}
  ],
  "edges": [
    {"id": "e1", "source": "n1", "target": "n2"}
  ]
}
```

**Response 201:** full workflow row (id + timestamps + echo of request fields).

**Errors:**
- 422 if Pydantic validation fails (unknown node type, missing required field, etc.) — FastAPI returns its standard validation payload.
- 422 if `graph_builder.validate_workflow_shape()` fails (zero start nodes, unreachable node, etc.) — we run this validation at create time so broken workflows never land in the DB.

### 10.2 `POST /executions`

**Request:**

```json
{
  "workflowId": "cuid_abc123",
  "input": {"user_message": "hello"}
}
```

**Response 202 (Accepted):** execution row with `status: "running"` and a generated `id` + `threadId`. The actual run happens in a background task; poll `GET /executions/{id}`.

**Errors:**
- 404 if `workflowId` not found.
- 422 if request body malformed.
- 422 if the referenced workflow has a Phase-not-yet-reached node type (returns the specific `NotImplementedError` message from the executor registry as the 422 detail).

### 10.3 `GET /executions/{execution_id}`

**Response 200:** full execution row.

**Errors:**
- 404 if not found.

### 10.4 Response model shapes

Defined in `src/api/workflows.py` and `src/api/executions.py` as Pydantic `WorkflowRead` / `ExecutionRead` classes with `from_attributes=True` to accept Prisma row models directly.

### 10.5 No auth middleware

Per ADR-0005. All endpoints are anonymous in Phase 1. `user_id` is stamped as `"dev"` on created rows. Phase 7 wires JWT via FastAPI `Depends()`.

---

## 11. JWT primitives (`src/security/jwt.py`)

Standalone, tested, **not** wired to routes. Phase 7 uses these without further invention.

```python
from datetime import datetime, timedelta, timezone
from typing import Any
from jose import JWTError, jwt
from pydantic import BaseModel
from src.config import get_settings

class AccessTokenPayload(BaseModel):
    sub: str           # user id
    iat: int
    exp: int
    type: str = "access"

class RefreshTokenPayload(BaseModel):
    sub: str
    iat: int
    exp: int
    type: str = "refresh"

def create_access_token(user_id: str) -> str: ...
def create_refresh_token(user_id: str) -> str: ...
def verify_access_token(token: str) -> AccessTokenPayload: ...
def verify_refresh_token(token: str) -> RefreshTokenPayload: ...

class TokenVerificationError(ValueError): ...
class TokenExpiredError(TokenVerificationError): ...
```

Unit tests cover: round-trip encode/decode, expiry enforcement, type enforcement (access token verified as refresh raises), tampering (signature mismatch raises), and malformed token input.

---

## 12. Testing

### 12.1 Test matrix

| Layer | Path | Scope |
|---|---|---|
| Unit — state | `tests/unit/engine/test_state.py` | Reducer behavior (`merge_dict`, `last_wins`, list `add`) |
| Unit — workflow models | `tests/unit/engine/test_workflow_models.py` | Discriminator on `type`; alias round-trip; each of 18 types parses a valid example; unknown type fails |
| Unit — graph builder | `tests/unit/engine/test_graph_builder.py` | Shape validation errors (each specific error message); reachability; unsupported type raises; conditional edge source raises |
| Unit — executors | `tests/unit/executors/test_start.py`, `test_end.py` | OAB-behavioral: string input, JSON-string input, dict input, non-JSON string input |
| Unit — JWT | `tests/unit/security/test_jwt.py` | Round-trip, expiry, type, tampering |
| Unit — checkpointer | `tests/unit/storage/test_checkpointer.py` | Uses a `Prisma` test double; verifies serde round-trip and key composition |
| Unit — API | `tests/unit/api/test_workflows.py`, `test_executions.py` | Route happy paths + error codes, with engine + db mocked |
| Integration | `tests/integration/test_start_to_end.py` | Real Postgres (CI service), real engine: POST workflow → POST execution → poll until complete → assert final output |
| Regression | `tests/regression/test_oab_start_end.py` | Port of OAB's smoke-level start→end test(s) |

### 12.2 Integration test infrastructure

- Integration tests are marked `@pytest.mark.integration`.
- CI (`.github/workflows/ci.yml`) adds a Postgres `services:` block and sets `TEST_DATABASE_URL`. A fixture in `tests/conftest.py` runs `prisma migrate deploy` against the service before the integration suite runs.
- Developers can run the integration suite locally by pointing `TEST_DATABASE_URL` at a scratch Neon branch. Unit-only runs (`pytest -m "not integration"`) need nothing.

### 12.3 OAB regression test to port in Phase 1

OAB's repo has tests under `tests/*.spec.ts` covering each node type. For Phase 1 we port exactly the smoke test(s) that cover a `start → end` workflow. The agent investigating OAB identified the relevant TS tests — the specific file(s) to port are chosen during implementation (one or two tests, enough to establish the regression pattern).

### 12.4 Coverage target

`pytest-cov` reports ≥80% line coverage on Phase 1 modules. Exempt: `src/main.py` (integration-covered), `prisma/generated/` (auto-generated).

---

## 13. Dev + CI updates

### 13.1 Repo changes outside `src/`

- **Remove** `docker-compose.yml` (per ADR-0004).
- **Update** `.env.example`: remove Docker fallback URL, keep only Neon-shaped template; add `TEST_DATABASE_URL` template for integration tests.
- **Update** `README.md`: remove "Offline fallback: local Postgres" section; replace the setup step 3 prerequisite with "a Neon account (free tier is enough)".
- **Update** `README.md` repo layout: remove `docker-compose.yml` line; annotate that `src/engine/`, `src/executors/`, `src/storage/`, `src/security/`, `src/api/` are populated by Phase 1.
- **Update** `CHANGELOG.md`: Phase 0 retroactive line removing docker-compose mention; Phase 1 section itemizing the above.
- **Update** `CLAUDE.md` phase status table: Phase 1 goes from ⏭ to ✅ in the phase-exit commit (not the spec commit).

### 13.2 CI workflow

`.github/workflows/ci.yml` additions:

```yaml
services:
  postgres:
    image: postgres:15
    env:
      POSTGRES_USER: composer_test
      POSTGRES_PASSWORD: composer_test
      POSTGRES_DB: composer_test
    ports: ["5432:5432"]
    options: >-
      --health-cmd pg_isready
      --health-interval 10s
      --health-timeout 5s
      --health-retries 5

env:
  TEST_DATABASE_URL: postgresql://composer_test:composer_test@localhost:5432/composer_test
```

Plus a step that runs `uv run prisma migrate deploy` against `TEST_DATABASE_URL` before pytest.

---

## 14. Sequencing within Phase 1

Rough order, expected to land as ~6 commits:

1. **Docs + policy commit (this spec + ADRs + docker-compose removal).** Ships this spec, `decisions.md`, CHANGELOG update, `.env.example`, `README.md`, and deletes `docker-compose.yml`. No source changes.
2. **Prisma schema + migration.** Adds the four tables, removes `ComposerBootstrap`, generates client, commits generated artifacts per the existing CI pattern.
3. **Pydantic models.** `engine/workflow.py` (18 node types + edge + workflow), `engine/state.py`, unit tests.
4. **Checkpointer + DB lifecycle.** `storage/db.py`, `storage/checkpointer.py`, lifecycle wiring in `main.py`, unit tests.
5. **Engine + executors.** `engine/graph_builder.py`, `engine/langgraph_executor.py`, `executors/base.py`, `executors/start.py`, `executors/end.py`, unit tests.
6. **API + JWT + integration test + regression test.** `api/workflows.py`, `api/executions.py`, `security/jwt.py`, unit + integration + regression tests, CI workflow update.

Each commit ends with `uv run ruff check && uv run ruff format --check && uv run pyright && uv run pytest` passing.

---

## 15. Risks and open questions

| Risk | Mitigation |
|---|---|
| Prisma Python's `Bytes` column behavior with large BYTEA values | Checkpointer unit tests exercise blobs up to 1 MB; if pain appears, escape hatch is ADR-0001's Option A. |
| Prisma Python's async session + FastAPI BackgroundTasks concurrency semantics | BackgroundTasks run in the same event loop as the request; a single Prisma client instance is thread-safe for async use. Integration test will validate. |
| OAB regression tests assume Convex-specific APIs and may not port cleanly | For Phase 1 we port only behavioral assertions; Convex test harness code is replaced with FastAPI TestClient. Expected friction but bounded to 1-2 tests in this phase. |
| Pydantic discriminated union performance with 18 variants | Measured-not-guessed: if validation of large workflow JSONs becomes a hot path, revisit. Phase 1 workflows are tiny. |
| OAB `start → end` regression tests may assert empty `finalOutput` (see §7.1 deliberate deviation) | When porting, adapt those assertions to the Composer behavior and log the delta in CHANGELOG. The deviation is intentional and its rationale is in §7.1. |

**No open questions** blocking implementation. All scope decisions are locked in ADRs 0001–0005.

---

## 16. Phase-exit checklist

Before claiming Phase 1 complete, verify:

- [ ] All 11 items from §1 "Phase 1 is done when" are green.
- [ ] CHANGELOG.md has a Phase 1 section enumerating what landed.
- [ ] CLAUDE.md phase status table shows Phase 1 ✅, Phase 2 ⏭.
- [ ] ADRs 0001–0005 all have `Implemented by` populated with commit hashes.
- [ ] No `TODO(phase-1)` comments remain in the code.
- [ ] `uv run pytest --cov` shows ≥80% coverage on Phase 1 modules.
- [ ] Integration test passes in CI against real Postgres.
