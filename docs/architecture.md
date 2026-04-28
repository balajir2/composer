# Architecture

How Composer is built today. This is the document to read before adding a node type, an integration, or a new executor.

## Big picture

```
┌────────────────────────────────────────────────────────────────────────┐
│                       Browser (Next.js 14 frontend)                    │
│  Designer canvas (React Flow)  •  Runs page  •  Admin console          │
└──────────┬───────────────────────┬───────────────────────┬─────────────┘
           │ JWT (NextAuth)        │ WebSocket             │ JWT
           ▼                       ▼                       ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                 FastAPI backend (src/main.py routers)                   │
│   /workflows /executions /api/run/{slug} /uploads /admin/* /events …   │
└──────────┬───────────────┬───────────────┬───────────────┬──────────────┘
           │               │               │               │
           ▼               ▼               ▼               ▼
   ┌───────────────┐  ┌──────────┐  ┌─────────────┐  ┌────────────────┐
   │  LangGraph    │  │ Postgres │  │ MCP servers │  │  LLM providers │
   │  executor     │◄─┤ (Prisma) │  │ (HTTP/SSE)  │  │  (Anthropic /  │
   │  + executors  │  │          │  │             │  │   OpenAI / …)  │
   └───────────────┘  └──────────┘  └─────────────┘  └────────────────┘
```

- Frontend authenticates the user via NextAuth (Azure AD or username/password). Every API call carries a Composer JWT (HS256) the backend mints from the auth provider's claims.
- API routes are FastAPI `APIRouter`s. Each major surface (workflows, executions, MCP, admin, uploads, run) lives in its own module under [`src/api/`](../src/api/).
- LangGraph drives execution. The graph is built fresh per run from the workflow's `nodes`/`edges` JSON; node executors are registered via a small `register_executor` decorator in [`src/executors/base.py`](../src/executors/base.py).
- Postgres (Prisma) is the single source of truth: workflows, executions, checkpoints, users, API keys, MCP servers + OAuth tokens, LLM API keys, deployment settings, LLM model catalog, approvals.

## Repository layout

```
composer/
├── src/
│   ├── main.py                  # FastAPI app + router wiring
│   ├── config.py                # Pydantic Settings (env vars)
│   ├── api/                     # REST + WebSocket endpoints
│   │   ├── workflows.py            CRUD + list + search + admin overrides
│   │   ├── executions.py           POST/GET, approval resume, history
│   │   ├── run.py                  POST /api/run/{slug} external invoke
│   │   ├── uploads.py              POST /uploads/extract-text
│   │   ├── api_keys.py             per-user API key management
│   │   ├── auth_*.py               standalone + common auth flows
│   │   ├── admin_*.py              users, LLM keys, models, MCP settings
│   │   ├── mcp_servers.py          MCP CRUD + OAuth callback
│   │   ├── llm_models*.py          live + DB-curated model catalog
│   │   └── events_ws.py            /executions/{id}/ws WebSocket
│   ├── engine/
│   │   ├── langgraph_executor.py   build_graph() + run() + interrupts
│   │   ├── workflow.py             Pydantic node + edge schemas
│   │   ├── state.py                WorkflowStateDict, reducers
│   │   ├── events_wrapper.py       per-node event emission + alias spread
│   │   ├── events.py               ExecutionEventBus (in-process pub/sub)
│   │   └── graph_builder.py        validation + conditional routing
│   ├── executors/                  # 18 node-type implementations
│   │   ├── base.py                    register_executor + dispatch
│   │   ├── _eval.py                   simpleeval wrapper (Mustache aware)
│   │   ├── start.py / end.py / agent.py / http.py / …
│   │   └── vector_db.py               query + upsert dispatch
│   ├── llm/
│   │   ├── providers.py            Anthropic/OpenAI/Google/Groq dispatch
│   │   └── structured_output.py    JSON-mode invocation helpers
│   ├── mcp/
│   │   ├── client.py               streamable-HTTP MCP transport
│   │   ├── oauth.py                Authorization Code flow + PKCE + RFC 8707
│   │   └── base.py                 manual tools/list + binding to LangChain Tool
│   ├── tools/
│   │   ├── base.py                 BuildContext + provider framework
│   │   ├── registry.py             selectedTools resolution
│   │   └── providers/              tavily / firecrawl / serper / …
│   ├── vectordb/
│   │   ├── embedding.py            OpenAI text-embedding-3-small
│   │   └── providers/              pinecone / qdrant / chroma / weaviate / milvus
│   ├── security/
│   │   ├── auth.py                 dependency injection for user_id + role
│   │   ├── jwt.py                  Composer JWT mint + verify
│   │   ├── api_key_auth.py         per-user API key auth
│   │   ├── encryption.py           AES-256-GCM
│   │   ├── rate_limit.py           in-memory token bucket
│   │   ├── passwords.py            bcrypt
│   │   └── sso_azure.py            Azure AD JWKS validation
│   ├── storage/
│   │   ├── db.py                   Prisma client lifecycle
│   │   └── checkpointer.py         PrismaCheckpointSaver
│   ├── migration/                  OAB → Composer one-shot importer
│   └── integrations/               LangSmith config threading
├── frontend/
│   ├── app/                        Next.js App Router
│   │   ├── designer/                  canvas + templates + settings
│   │   ├── runs/                      list / details / approval / API keys
│   │   ├── admin/                     users / LLM keys / models / MCP / tools
│   │   └── (auth)/                    login / register / sso-callback
│   ├── components/composer/        canvas, panels, role-nav, etc.
│   ├── lib/api/                    typed REST clients (OpenAPI-generated schema)
│   └── e2e/                        Playwright suite
├── prisma/schema.prisma            Single source of truth for the data model
├── tests/                          ~683 unit + integration tests
├── scripts/seed_templates.py       Seeds the 17 reference templates
└── docs/                           ← you are here
```

## Execution model

A workflow is a JSON pair of `nodes[]` + `edges[]`. The executor:

1. **Loads the workflow row**, validates against `WorkflowSchema` (Pydantic discriminated union over node `type`).
2. **Compiles a LangGraph `StateGraph`** via [`graph_builder.build_graph`](../src/engine/graph_builder.py). Every node becomes a node in the StateGraph; every edge becomes either a plain edge or a conditional edge (for `if-else` / `while` / `user-approval`).
3. **Runs `compiled.ainvoke(initial_state, config={"configurable": {"thread_id": …}})`**. Each node receives the current state and returns a delta; LangGraph's reducers (`merge_dict` for variables, `add` for chat history) merge deltas back.
4. **Persists checkpoints** via the `PrismaCheckpointSaver` after every node — this is what makes interrupts (`user-approval`) and resumes work.
5. **Emits events** to a shared `ExecutionEventBus` (in-process pub/sub). The WebSocket endpoint at `/executions/{id}/ws` subscribes and streams `node_started` / `node_completed` / `node_failed` / `execution_completed` events to the client.

### State shape

```python
class WorkflowStateDict(TypedDict):
    variables:       Annotated[dict[str, Any], merge_dict]   # the eval / substitution scope
    chat_history:    Annotated[list[ChatMessage], add]
    current_node_id: Annotated[str, last_wins]
    node_results:    Annotated[dict[str, NodeExecutionResult], merge_dict]
    pending_auth:    Annotated[dict[str, Any] | None, last_wins]
    loop_results:    Annotated[list[Any], add]
    user_id:         NotRequired[str]
```

`variables` is the central dict — every executor reads from and writes to it. `lastOutput` and `input` are seeded by Start; the engine adds `_while_iterations` and `_guardrails_result` as it runs.

### Variable substitution + eval

Two engines, two scopes:

- **`{{path.to.field}}` substitution** ([`src/variable_substitution.py`](../src/variable_substitution.py)) — used everywhere prompts/URLs/values get rendered. Walks `state["variables"]` by dotted path; non-string values are JSON-serialised. Unresolved paths render as the literal `{{...}}` text (matches OAB).
- **simpleeval expressions** ([`src/executors/_eval.py`](../src/executors/_eval.py)) — used by `if-else.condition`, `while.condition`, `transform.transformScript`, `data-transform.collection` + `expression`. Sandboxed: no `eval`/`exec`/`import`/dunder access. Scope includes `variables`, `lastOutput`, `node_results`, plus every top-level `state.variables` key spread as a direct name. Expressions also accept `{{path}}` syntax — the wrapper rewrites `{{a.b}}` to `a["b"]` before parse so designers can use Mustache in eval contexts (the syntax everywhere else in the canvas).

### Conditional routing

`if-else`, `while`, and `user-approval` nodes have **two outgoing handles each**:

| Source type | Branches |
|---|---|
| `if-else` | `true` / `false` |
| `while` | `body` / `exit` |
| `user-approval` | `approved` / `rejected` |

The frontend's `BranchingNode` component renders two coloured handles per branching type; each connection's `sourceHandle` is the branch label (`true`, `body`, `approved`, etc.). The serialiser copies `sourceHandle` → `branch` on save. Backend validation rejects edges that leave a conditional source without a `branch` and rejects edges that have a `branch` set but originate from a non-conditional source.

### Events wrapper

Every executor is wrapped by [`events_wrapper`](../src/engine/events_wrapper.py) which:

1. Emits `node_started` before invoking.
2. Calls the executor.
3. Reads `result["node_results"][node.id].output`.
4. Aliases the output under both `state.variables[<sanitized_node_id>]` and `state.variables[<sanitized_node_name>]` (snake_case) so designers can reference `{{place_picker.city}}` in downstream prompts.
5. Emits `node_completed` (or `node_failed` on exception).

This auto-aliasing is why "give the node a Name and reference it as `{{name.field}}`" works without any explicit configuration.

## Auth model

Three layers stack:

1. **NextAuth (frontend)** — Azure AD or Credentials provider, mints a session JWT.
2. **Composer JWT (backend session)** — HS256, mirrors IE's `DES-004`. Used by all interactive UI calls.
3. **Per-user API keys (backend)** — bcrypt-hashed, stored in `api_keys`. Used by external invokes (`POST /api/run/{slug}`); the published workflow's owner controls who can call it.

Admin role is set on the `User.role` column; admin-only routes use `Depends(ensure_admin)`. Per-execution authz: workflow owners + admins can read/edit; public workflows are world-readable; private workflows return 404 (not 403) to non-owners to avoid leaking existence.

## Frontend

Next.js 14 App Router, three role-aware audiences:

- **`/designer/*`** — workflow authors. Canvas, templates gallery, settings, publish dialog.
- **`/runs/*`** — end users. Run list, run details, approval dialog, API key management.
- **`/admin/*`** — admins. Users, LLM keys, model catalog, MCP server toggles, deployment settings, all-workflows view.

A single `RoleNav` component reads the user's role and renders the appropriate hierarchical menu. Every panel's edits flow through React Hook Form + Zod for client-side validation; Save calls `PUT /workflows/{id}` with the full envelope.

The OpenAPI schema is generated from FastAPI at `/openapi.json` and consumed by `frontend/lib/api/generated/schema.ts` for end-to-end typing.

## Adding a new node type

1. **Schema** — add a `*NodeData` Pydantic class in [`src/engine/workflow.py`](../src/engine/workflow.py) and add the type to the discriminated union.
2. **Executor** — create `src/executors/<your_node>.py` with a class decorated `@register_executor("<your-type>")`. Implement `async def arun(self, state) -> dict[str, Any]` that returns a delta.
3. **Tests** — add `tests/unit/executors/test_<your_node>.py`. Cover happy path, validation failures, and any state interactions.
4. **Frontend panel** — `frontend/components/composer/canvas/node-panels/<your-node>.tsx`. Read/write the schema fields by their canonical aliases. Register in `PANEL_MAP` in `property-panel.tsx`.
5. **Canvas chip** — add an entry to `NODE_VISUALS` in [`frontend/components/composer/canvas/node-visuals.ts`](../frontend/components/composer/canvas/node-visuals.ts).
6. **Run the gates**: `uv run ruff check src tests && uv run ruff format src tests && uv run pyright src tests && uv run pytest`. Frontend: `cd frontend && tsc --noEmit -p tsconfig.json`.
7. **Document** — add a section to [`designer-guide.md`](designer-guide.md) explaining what the node does, when to reach for it, and the key fields.

If your node is conditional (multiple outgoing handles), also extend `CONDITIONAL_SOURCE_TYPES` in [`src/engine/graph_builder.py`](../src/engine/graph_builder.py), add a branch spec to `BRANCH_SPECS` in [`workflow-canvas.tsx`](../frontend/components/composer/canvas/workflow-canvas.tsx), and add the type to `CONDITIONAL_SOURCE_TYPES` in [`workflow-to-rf.ts`](../frontend/lib/workflow-to-rf.ts).
