# Phase 3a — MCP Infrastructure (Static Auth): Design Spec

**Author:** Balaji Rajan
**Date:** 2026-04-20
**Status:** Approved → ready for implementation plan
**Phase ref:** First half of Phase 3 of [`docs/design/2026-04-20-composer-python-port-design.md`](../../design/2026-04-20-composer-python-port-design.md). Phase 3 was split into 3a (this spec, static-auth MCPs) + 3b (OAuth) based on risk isolation and verification cadence.
**Governing ADRs:** [ADR-0009 (Tool Provider Framework)](../../design/decisions.md#adr-0009-tool-provider-framework--unified-toolprovider-abstraction-for-standard-tools-and-mcp-servers), [ADR-0010 (MCP integration pattern — new in this phase)](../../design/decisions.md#adr-0010-mcp-tool-provider--resolver-side-instantiation)

---

## 1. Goal

Ship an MCP (Model Context Protocol) client that lets a user connect an MCP server with static authentication (none / api-key / bearer header), then use that server's tools from either:
- The **Agent executor** (Phase 2) — MCP tools bound to a chat model via `mcp_server_ids` on the node
- The **`mcp` node executor** (new in 3a) — a standalone node that calls one tool on one server without an LLM in the loop

Firecrawl's hosted MCP server and DeepWiki (public, no-auth) are the canonical acceptance tests. OAuth (Highspot, Notion, Atlassian, etc.) is deferred to Phase 3b and lands as an `McpToolProvider` subclass variant, not a rebuild.

**Phase 3a is done when:**

1. Prisma schema has `McpServer` table with **all** columns OAB has (including an `oauthConfig` JSON column, empty in 3a, filled in 3b). This stability means Phase 3b adds no migrations.
2. `src/mcp/client.py` implements an HTTP JSON-RPC MCP client with `initialize`, `tools/list`, `tools/call` methods. Supports both plain JSON responses and `text/event-stream` (SSE) responses (Highspot, DeepWiki both use SSE).
3. `src/mcp/schema_adapter.py` normalizes `inputSchema` / `schema` / `input_schema` and URL placeholder substitution for Firecrawl-style api-key-in-URL (`{FIRECRAWL_API_KEY}`).
4. `src/mcp/base.py` defines `McpToolProvider(ToolProvider)` — implements Phase 2's ABC. Constructed at runtime from an `McpServer` Prisma row. Implements `tools()` (JSON-RPC `tools/list`), `build_tool(name, ctx)` (returns a BaseTool wrapping `tools/call`), and `health_check()` (real `initialize` ping).
5. `src/mcp/resolver.py` dispatches `node.data.mcp_server_ids` to McpToolProvider instances. Replaces the `raise NotImplementedError("Phase 3")` stub at `src/tools/registry.py`'s `resolve_tools_for_node`.
6. `src/security/encryption.py` — AES-256-GCM helpers (`encrypt`, `decrypt`) using `ENCRYPTION_KEY` from Settings. Used by MCP for server-side secret storage (api-key values for authenticated MCPs); Phase 3b uses the same primitive for OAuth tokens.
7. `src/executors/mcp.py` — the `mcp` node executor; `@register_executor("mcp")` replaces the Phase-3 NotImplementedError from Phase 1's registry.
8. `src/api/mcp_servers.py` — REST surface for the MCP lifecycle:
   - `POST /mcp-servers` — create an MCP server row (user must be authenticated; 3a uses `user_id="dev"` anonymous like other endpoints)
   - `GET /mcp-servers` — list the user's own + shared servers
   - `POST /mcp-servers/{id}/test-connection` — calls `McpToolProvider.health_check()`, updates `connection_status` / `last_error` / `last_tested`
   - `DELETE /mcp-servers/{id}` — remove
9. Unit tests: client, schema adapter, resolver, MCP provider, MCP executor, REST endpoints, encryption primitive. Mock httpx.
10. Integration tests (`@pytest.mark.integration`, skip on missing keys):
    - Start → Agent(with DeepWiki MCP `ask_question`) → End (real DeepWiki, no auth)
    - Start → Agent(with Firecrawl MCP `firecrawl_scrape`) → End (real Firecrawl MCP with api key)
    - Start → Mcp(DeepWiki, `ask_question`) → End (mcp node executor, no LLM in loop)
11. One OAB regression test ported (MCP lifecycle: add server → list tools → call tool → assert output shape).
12. `uv run ruff check && ruff format --check && pyright && pytest -m "not integration"` green. Integration tests green when keys present.
13. CHANGELOG + CLAUDE.md phase table updated (Phase 3a ✅, Phase 3b ⏭).

**Non-goals for Phase 3a** (all land in Phase 3b):
- OAuth 2.1 authorize / callback / token exchange / refresh flows
- The six hard-won MCP OAuth fixes (RFC 8707 resource param, service-account token fallback for shared-server OAuth, etc.)
- `McpOAuthToken` and `McpOAuthState` Prisma tables
- Highspot acceptance test

---

## 2. Context

Phase 2 shipped the Tool Provider Framework (`src/tools/base.py` + `src/tools/registry.py`) with `ToolProvider` ABC, `AuthRequirement` hierarchy, and `resolve_tools_for_node`. `src/mcp/__init__.py` was a Phase-2 placeholder docstring; 3a fills it in.

**The Phase 2 framework already has an `OAuthAuth` dataclass** (with `include_rfc8707_resource` flag). That was forward-looking for Phase 3b; 3a doesn't instantiate it but proves the ABC can carry it.

**The Phase 1 `McpNode` Pydantic model already exists** (per ADR-0002 — all 18 node types modeled in Phase 1). Its `data` class (`McpNodeData`) has `mcp_server_id`, `tool_name`, `arguments`. 3a wires the executor for it.

**Current registry state:** `resolve_tools_for_node` raises `NotImplementedError` when `node.data.mcp_server_ids` is non-empty. 3a replaces that with real resolution. Phase 2's test `test_resolve_tools_for_node_mcp_ids_raise_until_phase_3` will flip — updating that test is part of 3a.

---

## 3. Behavioral reference from OAB

All runtime behavior mirrors `D:/GitHub/open-agent-builder` (read-only per CLAUDE.md Rule 2). Specific references for 3a:

- **MCP client** (HTTP JSON-RPC + SSE): `lib/workflow/executors/mcp-utils.ts` — `executeMcpTool` at lines 109-221; `fetchMcpTools` at lines 226-314.
- **Schema adapter / inputSchema handling**: `lib/workflow/executors/mcp-utils.ts:33-35` (three-way fallback: `mcp.inputSchema || mcp.schema || mcp.input_schema`).
- **URL placeholder substitution**: `lib/workflow/executors/mcp-utils.ts` uses `{FIRECRAWL_API_KEY}` template; search for `FIRECRAWL_API_KEY`. Composer's Settings already has `firecrawl_api_key`.
- **McpServer schema**: `convex/schema.ts` — `mcpServers` table columns.
- **Resolver**: `lib/mcp/resolver.ts` — 3a implements a subset (no OAuth); the shared-server fallback mechanism is Phase 3b scope.
- **Firecrawl-specific tool schema hardcoding**: `mcp-utils.ts` lines 68-87 — OAB hardcodes Firecrawl tool schemas because Firecrawl's `tools/list` has historically had quirks. Composer does NOT hardcode; it trusts the server's schema. If Firecrawl's schema is problematic during integration testing, we'll add a narrowly-scoped fix then, not preemptively.
- **Manual tool calling fallback from Anthropic native-MCP**: `lib/workflow/executors/agent.ts:533-548`. **3a does NOT need this because 3a never uses native-MCP** — Phase 2's Agent executor already uses `bind_tools()` exclusively (manual tool calling is the Composer default by design). One of the six OAB fixes is already baked into Phase 2 architecture.

---

## 4. Module layout

```
src/
├── mcp/                  # WAS a placeholder in Phase 2; 3a populates
│   ├── __init__.py       # updated to import submodules for any side-effect needs
│   ├── client.py         # NEW: MCPClient — initialize, tools/list, tools/call
│   ├── schema_adapter.py # NEW: normalize inputSchema variants + URL template substitution
│   ├── base.py           # NEW: McpToolProvider(ToolProvider)
│   └── resolver.py       # NEW: resolve_mcp_tools_for_node — called by Phase 2 registry
├── security/
│   ├── __init__.py       # (Phase 1)
│   ├── jwt.py            # (Phase 1)
│   └── encryption.py     # NEW: AES-256-GCM encrypt/decrypt helpers
├── executors/
│   ├── ...               # (Phase 1-2)
│   └── mcp.py            # NEW: McpExecutor for the 'mcp' node type
├── api/
│   ├── ...               # (Phase 1-2)
│   └── mcp_servers.py    # NEW: /mcp-servers REST endpoints
├── tools/
│   └── registry.py       # MODIFIED: resolve_tools_for_node now delegates mcp_server_ids to src.mcp.resolver

prisma/
└── schema.prisma         # MODIFIED: add McpServer model

tests/
├── unit/
│   ├── mcp/
│   │   ├── __init__.py
│   │   ├── test_client.py          # NEW
│   │   ├── test_schema_adapter.py  # NEW
│   │   ├── test_base.py            # NEW (McpToolProvider contract)
│   │   └── test_resolver.py        # NEW
│   ├── security/
│   │   └── test_encryption.py      # NEW
│   ├── executors/
│   │   └── test_mcp_executor.py    # NEW
│   ├── api/
│   │   └── test_mcp_servers.py     # NEW
│   └── tools/
│       └── test_registry.py        # MODIFIED: remove the "Phase 3" NotImplementedError test, add MCP resolution test
├── integration/
│   ├── test_mcp_deepwiki.py        # NEW — real DeepWiki MCP, no auth
│   └── test_mcp_firecrawl.py       # NEW — real Firecrawl MCP, api-key
└── regression/
    └── test_oab_mcp_lifecycle.py   # NEW — OAB regression port
```

---

## 5. Prisma schema — `McpServer`

```prisma
model McpServer {
  id                 String   @id @default(cuid())
  userId             String   @map("user_id")              // owner (creator); shared servers still have an owner
  name               String
  url                String                                  // MCP server URL; may contain {VAR} placeholders (e.g., {FIRECRAWL_API_KEY})
  description        String?
  category           String?                                 // "web" | "ai" | "data" | "custom"
  authType           String   @map("auth_type")             // "none" | "api-key" | "bearer" | "oauth"
  encryptedAccessToken String? @map("encrypted_access_token") // for api-key / bearer — AES-256-GCM; null for none / oauth
  headerName         String?  @map("header_name")            // custom header name when authType=api-key and provider requires non-Authorization header
  oauthConfig        Json?    @map("oauth_config")          // Phase 3b fills this; 3a stores null
  tools              Json?                                   // cached tools/list result (tool names + metadata); refreshed by test-connection
  connectionStatus   String   @default("untested") @map("connection_status") // "connected" | "error" | "untested"
  lastTested         DateTime? @map("last_tested")
  lastError          String?  @map("last_error")
  enabled            Boolean  @default(true)
  isOfficial         Boolean  @default(false) @map("is_official")  // true for built-in providers (Firecrawl template)
  isShared           Boolean  @default(false) @map("is_shared")    // Phase 3b uses for service-account fallback
  headers            Json?                                   // custom headers beyond auth
  createdAt          DateTime @default(now()) @map("created_at")
  updatedAt          DateTime @updatedAt      @map("updated_at")

  @@map("mcp_servers")
  @@index([userId])
  @@index([isShared])
}
```

**Rationale for full schema in 3a:**
- `oauthConfig` and `isShared` are in the schema from day one; Phase 3b only adds behavior and two more tables (`McpOAuthToken`, `McpOAuthState`).
- `encryptedAccessToken` holds the static api-key value for `authType="api-key"` / `"bearer"` servers. 3a uses `src/security/encryption.py`. Phase 3b reuses the same column for nothing (it uses `McpOAuthToken` table instead), so this field is static-auth-only.
- `tools` Json column caches the `tools/list` response. Filled by `test-connection`. Not strictly necessary for runtime (tool resolution always calls live `tools/list`), but useful for UI + health debugging.

**Migration:** one Prisma migration, `phase_3a_mcp_server_table`.

---

## 6. MCP client (`src/mcp/client.py`)

```python
class MCPClient:
    """HTTP JSON-RPC client for MCP servers.

    Supports both plain JSON responses and text/event-stream (SSE) responses.
    DeepWiki and Highspot use SSE; most api-key servers use plain JSON.
    """

    def __init__(self, url: str, auth_header: dict[str, str] | None = None) -> None: ...

    async def initialize(self) -> InitializeResult:
        """JSON-RPC `initialize` — handshake + server capabilities."""

    async def tools_list(self) -> list[dict]:
        """JSON-RPC `tools/list` — returns list of tool dicts with inputSchema."""

    async def tools_call(self, name: str, arguments: dict) -> ToolCallResult:
        """JSON-RPC `tools/call` — returns content blocks."""
```

**Internal design:**
- Single `httpx.AsyncClient` reused across methods. 60s timeout (tool calls can be slow).
- Request body: `{jsonrpc: "2.0", id: <int>, method: <str>, params: <dict>}`
- Response parser: checks `Content-Type` header.
  - `application/json` → `resp.json()`
  - `text/event-stream` → iterate SSE frames, extract `data:` line that's JSON, take the one with matching `id`.
- Error handling:
  - HTTP 4xx/5xx → `MCPHTTPError` with status + body excerpt
  - JSON-RPC error object → `MCPRpcError` with code + message
  - Timeout → `MCPTimeoutError`
- Tool-call failures (HTTP/RPC-level) surface as exceptions; **tool result errors** (`result.isError=true`) are returned as-is in the `ToolCallResult` — the caller (Agent loop or Mcp executor) decides how to frame them.

**Lifecycle:** no persistent session — each method does an independent HTTP request. This matches OAB. MCP's `initialize` is required per-session per spec, but many servers accept subsequent `tools/list` / `tools/call` calls without a prior `initialize`. We call `initialize` only in `test-connection` and in `McpToolProvider.health_check()`, not before every tool call.

---

## 7. Schema adapter (`src/mcp/schema_adapter.py`)

Two responsibilities:

### 7.1 Normalize `inputSchema` / `schema` / `input_schema`

```python
def normalize_input_schema(mcp_tool: dict[str, Any]) -> dict[str, Any]:
    """Returns the tool's input JSON Schema, checking three keys in priority order.

    MCP spec uses 'inputSchema' (camelCase); some servers use 'schema' or
    'input_schema'. Check all three, prefer spec-compliant.
    """
    return mcp_tool.get("inputSchema") or mcp_tool.get("schema") or mcp_tool.get("input_schema") or {}
```

### 7.2 URL template substitution

```python
_URL_TEMPLATE_PATTERN = re.compile(r"\{([A-Z_][A-Z0-9_]*)\}")

def substitute_url_placeholders(url_template: str) -> str:
    """Replace {ENV_VAR} placeholders in the MCP server URL from Settings.

    Example: 'https://mcp.firecrawl.dev/{FIRECRAWL_API_KEY}/v2/sse' →
             'https://mcp.firecrawl.dev/fc-abc123/v2/sse'

    Unknown placeholders raise ValueError (fail fast; the server won't work
    without them).
    """
```

Looks up the settings field `<env_var>.lower()` (e.g., `FIRECRAWL_API_KEY` → `settings.firecrawl_api_key`). Raises `ValueError` if a placeholder is unresolvable.

**Integration with `McpToolProvider`:** When building the HTTP client, `McpToolProvider` calls `substitute_url_placeholders(server.url)` to resolve any templates. This keeps the template logic in one place, not duplicated in client.py.

---

## 8. Encryption helpers (`src/security/encryption.py`)

AES-256-GCM using `cryptography` (already in `pyproject.toml`):

```python
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

def encrypt(plaintext: str) -> str:
    """Encrypt a string; returns base64-encoded (nonce || ciphertext || tag)."""

def decrypt(ciphertext_b64: str) -> str:
    """Reverse of encrypt()."""

class EncryptionKeyMissingError(RuntimeError): ...
class EncryptionError(RuntimeError): ...
```

**Key sourcing:** `settings.encryption_key` — base64-decoded to 32 bytes. If empty, `encrypt`/`decrypt` raise `EncryptionKeyMissingError`. This matches OAB's behavior.

**Wire format:** `base64(nonce(12) || ciphertext || tag(16))` as a single base64 string. Simpler than OAB's `salt:iv:authTag:ciphertext` multi-field format; no salt because we're not deriving a key (we use the raw 32-byte key directly). This is a deliberate simplification from OAB — documented inline.

**Usage in 3a:**
- `McpServer.encryptedAccessToken` is set via `encrypt(api_key)` at server create/update time
- `McpToolProvider._build_auth_header` calls `decrypt` on demand

**Usage in 3b:** `McpOAuthToken.encryptedAccessToken` + `encryptedRefreshToken` + `McpServer.oauthConfig.encryptedClientSecret` all use the same primitive.

Phase 3a unit tests cover: round-trip, empty key fails, tampered ciphertext raises, missing key raises.

---

## 9. `McpToolProvider` (`src/mcp/base.py`)

```python
class McpToolProvider(ToolProvider):
    """Runtime-constructed provider; one instance per McpServer row.

    NOT registered via @register_tool_provider (see ADR-0010). The resolver
    instantiates one of these per invocation when a node's
    `mcp_server_ids` is non-empty.
    """

    def __init__(self, server: McpServerRow) -> None:
        self._server = server
        self._client = MCPClient(
            url=substitute_url_placeholders(server.url),
            auth_header=self._build_auth_header(),
        )

    # ToolProvider ABC ------------------------------------------------------

    @property
    def name(self) -> str:
        return f"mcp:{self._server.id}"  # unique per-server; not registry-keyed

    @property
    def description(self) -> str:
        return self._server.description or f"MCP server {self._server.name!r}"

    category = "mcp"

    @property
    def auth(self) -> AuthRequirement:
        if self._server.authType == "none":
            return NoAuth()
        if self._server.authType in {"api-key", "bearer"}:
            return ApiKeyAuth(env_var="<server-scoped>", settings_field="")
        # oauth — Phase 3b
        raise NotImplementedError(f"authType {self._server.authType!r} lands in Phase 3b")

    async def tools(self) -> list[ToolDefinition]:
        """Call tools/list on the server, normalize inputSchema variants."""
        raw_tools = await self._client.tools_list()
        return [
            ToolDefinition(
                name=t["name"],
                description=t.get("description", ""),
                args_schema=_json_schema_to_pydantic(normalize_input_schema(t)),
            )
            for t in raw_tools
        ]

    async def build_tool(self, tool_name: str, context: BuildContext) -> BaseTool:
        """Return a BaseTool that, when invoked, calls tools/call on this server."""
        return _McpBoundTool(self._client, tool_name)

    async def health_check(self) -> HealthStatus:
        """Real ping: initialize + tools/list."""
        try:
            await self._client.initialize()
            tools = await self._client.tools_list()
            return HealthStatus(ok=True, message=f"connected; {len(tools)} tool(s) available")
        except Exception as exc:
            return HealthStatus(ok=False, message=f"{type(exc).__name__}: {exc}")

    # Private ---------------------------------------------------------------

    def _build_auth_header(self) -> dict[str, str] | None:
        if self._server.authType == "none":
            return None
        if self._server.authType in {"api-key", "bearer"}:
            token = decrypt(self._server.encryptedAccessToken)
            header_name = self._server.headerName or "Authorization"
            prefix = "Bearer " if self._server.authType == "bearer" else ""
            return {header_name: f"{prefix}{token}"}
        # oauth — Phase 3b
        raise NotImplementedError()


class _McpBoundTool(BaseTool):
    """LangChain BaseTool that dispatches to an MCPClient.tools_call."""

    def __init__(self, client: MCPClient, tool_name: str) -> None: ...

    async def _arun(self, **kwargs: Any) -> str:
        result = await self._client.tools_call(self._tool_name, kwargs)
        # Concatenate text content blocks; image/binary blocks render as a placeholder
        return _render_content_blocks(result.content)
```

**`_json_schema_to_pydantic`:** Accept an OpenAI-compatible JSON Schema dict, return a dynamic Pydantic model. LangChain has utilities for this (`create_model_from_json_schema` or `pydantic.create_model`); implementation picks whichever is most stable. Tests exercise: string/int/bool fields, required vs optional, nested objects, arrays.

---

## 10. Resolver (`src/mcp/resolver.py`)

Two entry points:

```python
async def resolve_mcp_tools_for_node(
    node: AgentNode, context: BuildContext, db: Prisma
) -> list[BaseTool]:
    """Fetch each McpServer row by ID, instantiate McpToolProvider, aggregate tools."""
    if not node.data.mcp_server_ids:
        return []
    out: list[BaseTool] = []
    for server_id in node.data.mcp_server_ids:
        server = await db.mcpserver.find_unique(where={"id": server_id})
        if server is None:
            raise McpServerNotFound(f"MCP server {server_id!r} not found")
        if not _user_can_use(context.user_id, server):
            raise McpPermissionError(f"User {context.user_id!r} cannot use MCP server {server_id!r}")
        provider = McpToolProvider(server)
        for tool_def in await provider.tools():
            out.append(await provider.build_tool(tool_def.name, context))
    return out


async def resolve_single_mcp_tool(
    mcp_server_id: str, tool_name: str, user_id: str | None, db: Prisma
) -> tuple[McpToolProvider, BaseTool]:
    """For the `mcp` node executor — one server, one named tool."""
```

**Permission check (`_user_can_use`) in 3a:**
- Owner (`server.user_id == user_id`) → allowed
- `server.is_shared == True` → allowed for any authenticated user
- Otherwise → denied (`McpPermissionError`)

This is the simpler half of the Phase 3b service-account fallback (3a doesn't yet need to substitute the creator's OAuth token when a different user uses a shared server — there's no OAuth in 3a).

**Integration with Phase 2 `resolve_tools_for_node`:** Update `src/tools/registry.py` — the `raise NotImplementedError("Phase 3")` for `node.data.mcp_server_ids` is replaced with `out.extend(await resolve_mcp_tools_for_node(node, context, _get_db_from_context(context)))`. The db client comes from `context` (BuildContext grows a `db: Prisma` field in this phase).

---

## 11. MCP node executor (`src/executors/mcp.py`)

The `mcp` node type (Pydantic model already exists from Phase 1) is a standalone node that calls one tool on one server, **without** an LLM in the loop. Input comes from `state.variables.lastOutput` or explicit `arguments` on the node.

```python
@register_executor("mcp")
class McpExecutor:
    def __init__(self, node: McpNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        if not self.node.data.mcp_server_id:
            raise ValueError(f"mcp node {self.node.id!r} requires mcp_server_id")
        if not self.node.data.tool_name:
            raise ValueError(f"mcp node {self.node.id!r} requires tool_name")
        # substitute {{...}} in arguments
        args = {k: substitute(v, state) if isinstance(v, str) else v
                for k, v in (self.node.data.arguments or {}).items()}
        # resolve + call
        provider, tool = await resolve_single_mcp_tool(
            self.node.data.mcp_server_id, self.node.data.tool_name,
            user_id=state.get("user_id"), db=_get_db(),
        )
        result = await tool.ainvoke(args)
        return {
            "variables": {"lastOutput": result},
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "input": args,
                    "output": result,
                }
            },
        }
```

**Registry update:** replaces Phase 1's NotImplementedError for `"mcp"` type. The graph_builder test for "unshipped executor" moves from `mcp` to some other unshipped type (probably `http` — Phase 4).

---

## 12. REST API (`src/api/mcp_servers.py`)

Four endpoints. All use the same `user_id="dev"` anonymous pattern as Phase 1/2 (auth middleware is Phase 7).

### 12.1 `POST /mcp-servers`

**Request:**
```json
{
  "name": "DeepWiki",
  "url": "https://mcp.deepwiki.com/sse",
  "description": "Wikipedia-powered MCP",
  "category": "data",
  "authType": "none",
  "isShared": false
}
```

Or with api-key:
```json
{
  "name": "Firecrawl MCP",
  "url": "https://mcp.firecrawl.dev/{FIRECRAWL_API_KEY}/v2/sse",
  "authType": "api-key",
  "accessToken": "fc-abc123",
  "headerName": "Authorization"
}
```

**Behavior:**
- Validates that url templating variables exist in Settings (fails at create time, not at first call)
- Encrypts `accessToken` → `encryptedAccessToken` via `src/security/encryption.encrypt`
- Creates Prisma row with `connection_status="untested"`
- Returns 201 with the row (encrypted token redacted — response has `hasAccessToken: bool`, not the actual value)

### 12.2 `GET /mcp-servers`

Returns: all servers where `user_id == current_user` OR `is_shared == True`. Encrypted fields redacted to booleans.

### 12.3 `POST /mcp-servers/{id}/test-connection`

- Fetches the McpServer row
- Permission check (same rules as resolver)
- Instantiates `McpToolProvider(server)`
- Calls `provider.health_check()`
- Updates the row: `connection_status`, `last_tested`, `last_error`, `tools` (if successful — cached from `tools/list`)
- Returns the HealthStatus body

### 12.4 `DELETE /mcp-servers/{id}`

- Permission check: owner only (even shared servers can only be deleted by creator)
- `await db.mcpserver.delete(where={"id": id})` — Prisma cascades ensure Phase 3b's `McpOAuthToken` rows go with it when they exist.

**No GET by ID, no PATCH, no LIST-by-something-else** — Phase 7 adds those when it formalizes the full API. 3a ships the minimum.

---

## 13. Tests

### 13.1 Unit tests (all mock httpx / Prisma)

- `tests/unit/security/test_encryption.py` — 5 tests (round-trip, empty key, tampered ciphertext, missing key, long payload).
- `tests/unit/mcp/test_schema_adapter.py` — 6 tests (three-way fallback, URL substitution with known + unknown placeholders).
- `tests/unit/mcp/test_client.py` — 6 tests (JSON response, SSE response, HTTP error, RPC error, timeout, initialize handshake).
- `tests/unit/mcp/test_base.py` — 6 tests (McpToolProvider contract: tools/list → ToolDefinitions, build_tool, health_check ok/fail, auth header for api-key/bearer, OAuth raises NotImplementedError).
- `tests/unit/mcp/test_resolver.py` — 6 tests (not-found, permission owner, permission shared, permission denied, multiple server IDs, empty list).
- `tests/unit/executors/test_mcp_executor.py` — 5 tests (happy path, missing server_id raises, missing tool_name raises, variable substitution in arguments, registry integration).
- `tests/unit/api/test_mcp_servers.py` — 8 tests (POST 201, GET lists own + shared, POST test-connection success/failure, DELETE owner, DELETE permission-denied, etc.).
- `tests/unit/tools/test_registry.py` — **update** existing `test_resolve_tools_for_node_mcp_ids_raise_until_phase_3` test: it was asserting a Phase-3 NotImplementedError; now it should mock the resolver and assert the MCP tools flow through.

### 13.2 Integration tests (real MCP, `@pytest.mark.integration`)

- `tests/integration/test_mcp_deepwiki.py` — public MCP, no auth:
  1. POST a workflow: Start → Agent(mcp_server_ids=[deepwiki_id]) → End, instruct agent to "use the ask_question tool to find out when the Eiffel Tower was built"
  2. Poll, assert completed, assert output contains a year
  - Test creates the McpServer row via POST /mcp-servers, runs the workflow, deletes the row as teardown.
- `tests/integration/test_mcp_firecrawl.py` — Firecrawl MCP with FIRECRAWL_API_KEY:
  1. Create McpServer with url `https://mcp.firecrawl.dev/{FIRECRAWL_API_KEY}/v2/sse`
  2. Workflow: Start → Agent(mcp_server_ids=[firecrawl_id]) → End, instruct agent to scrape a URL
  3. Assert agent output includes scrape text
- `tests/integration/test_mcp_executor_node.py` — standalone `mcp` node type:
  1. Workflow: Start → Mcp(server_id=deepwiki_id, tool_name=ask_question, arguments={"question": "{{input}}"}) → End
  2. Executes without any LLM in the loop
  3. Assert state.variables.lastOutput is the tool's text content

### 13.3 Regression test

- `tests/regression/test_oab_mcp_lifecycle.py` — port of OAB's `tests/mcp-lifecycle.spec.ts`. Covers: add MCP server → list tools → call one tool → assert output shape.

---

## 14. Sequencing

~14 tasks, one commit each (same pattern as Phases 1+2). Rough order:

1. Prisma schema + migration (`McpServer` table)
2. `src/security/encryption.py` + tests (foundational, used by 3a static auth AND 3b OAuth)
3. `src/mcp/schema_adapter.py` + tests
4. `src/mcp/client.py` + unit tests (mocked httpx, both JSON + SSE response shapes)
5. `src/mcp/base.py` — `McpToolProvider` + unit tests
6. `src/mcp/resolver.py` + unit tests
7. Update `src/tools/registry.py` → `resolve_tools_for_node` dispatches to MCP resolver; update `tests/unit/tools/test_registry.py`
8. `src/executors/mcp.py` + unit tests; update `graph_builder.py` for `mcp` executor registration; update the "unshipped executor" sentinel test to use a different node type (probably `http`, Phase 4)
9. `src/api/mcp_servers.py` + unit tests
10. Wire router into `src/main.py`
11. Integration test: DeepWiki Agent-with-MCP
12. Integration test: Firecrawl Agent-with-MCP
13. Integration test: `mcp` executor standalone node
14. OAB regression port
15. CI: no changes needed — FIRECRAWL_API_KEY already plumbed in Phase 2's CI update
16. Phase-exit: CHANGELOG + CLAUDE.md + ADR-0010 backfill

---

## 15. Risks and open questions

| Risk | Mitigation |
|---|---|
| JSON Schema → Pydantic model conversion for complex MCP schemas (nested arrays, oneOf, etc.) | Use LangChain's established utility first; fall back to `dict[str, Any]` for the `args_schema` if conversion fails. Tests cover the common cases; real-API integration will expose the edge cases. |
| MCP `tools/list` response > 73K chars (Highspot incident) | Phase 2's agentic loop uses LangChain `bind_tools` + manual execution by design — OAB's Anthropic native-MCP fallback isn't needed because we never try native MCP in the first place. |
| Firecrawl MCP's actual URL format differs from the `{FIRECRAWL_API_KEY}` guess | Check live during integration test writing; spec says the pattern is `https://mcp.firecrawl.dev/{FIRECRAWL_API_KEY}/v2/sse` based on OAB evidence; if different, adjust in the test itself. |
| DeepWiki URL / protocol shift | Integration test skips if DeepWiki returns 404 / 503. Real users don't depend on DeepWiki; it's just a test oracle. |
| SSE parsing edge cases (chunked responses, keep-alive, retry events) | MCP servers use a narrow SSE subset (one `message` event per RPC response). Client parses only the `data:` line. If a server does something weirder, we'll see it in integration testing and add parsing. |

**Open questions**: none blocking.

---

## 16. Phase-exit checklist

- [ ] All 13 items from §1 "Phase 3a is done when" green.
- [ ] CHANGELOG Phase 3a section with `Added` / `Changed` / `Fixed`.
- [ ] CLAUDE.md phase status: Phase 3a ✅, Phase 3b ⏭.
- [ ] ADR-0010 `Implemented by` populated.
- [ ] Integration tests green against real DeepWiki + real Firecrawl MCP.
- [ ] OAB regression test ported.
- [ ] `uv run ruff check && ruff format --check && pyright && pytest -m "not integration"` green.
