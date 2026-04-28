# Open Agent Builder — Complete Knowledge Dump for Agent Consumption

**Audience:** A Claude Code (or equivalent) agent working inside the Intelligent Engineering monorepo, tasked with implementing Composer based on Open Agent Builder's design
**Purpose:** Self-contained, drop-in knowledge base. An agent reading this document alongside access to the Open Agent Builder source code should have everything needed to port the system to Python inside IE.
**Date:** 2026-04-15
**Companion docs:** All five `composer-0*.md` specs in this directory

---

## How to Use This Document

Read this in order. It's long, but each section has a clear purpose:

1. **Sections 1-3** explain what OAB is at a conceptual level
2. **Sections 4-7** describe the architecture and data model
3. **Sections 8-14** are deep-dives into each subsystem
4. **Section 15** is a complete list of known bugs and their fixes (the April 2026 learnings)
5. **Section 16** has the full porting checklist
6. **Appendices** have code snippets and reference material

When implementing Composer:
- Read this document fully once
- Read the specific OAB source files referenced when porting each subsystem
- Consult the April 2026 commit history for context on the 6 critical fixes
- Use this document's code snippets as reference implementations, not as literal copy-paste

---

## 1. Product Summary

Open Agent Builder (OAB) is a low-code platform for building AI agent workflows visually. Users build workflows by dragging nodes onto a canvas in a web UI. Each node represents one step: an LLM call, an API call, a tool invocation, a data transformation, a conditional branch, a human approval point. The workflow is stored as a JSON graph (`nodes[]` and `edges[]`). At execution time, the graph is converted to a LangGraph StateGraph and executed, streaming real-time events back to the UI.

**Key metaphor:** Think of it as a visual programming IDE where the "program" is an AI agent's reasoning/action loop, and the "runtime" is LangGraph.

**Supported providers:**
- **LLMs:** Anthropic Claude, OpenAI GPT, Google Gemini, Groq
- **Tool integrations (MCP):** Any MCP server; OAB has built-in support for Firecrawl and user-registered servers
- **Direct tools:** Firecrawl, Tavily, Serper, SerpAPI, E2B (code exec), Arcade, Gamma AI, ScraperAPI, Browserless

---

## 2. Technology Stack Summary

| Layer | Technology |
|---|---|
| Frontend | Next.js 16 (canary), React 19, TypeScript |
| Visual builder | React Flow |
| API routes | Next.js App Router API routes |
| Execution engine | LangGraph JS (`@langchain/langgraph`) |
| Database | Convex (TypeScript-native, real-time) |
| Auth | NextAuth.js + Azure AD (Microsoft Entra ID) |
| Encryption | AES-256-GCM via `crypto` + Convex env keys |
| LLM SDKs | `@anthropic-ai/sdk`, `openai`, `@google/generative-ai`, `groq-sdk` |
| Code sandbox | E2B (`@e2b/code-interpreter`) |
| Testing | Playwright |

**Composer target stack:**
| Layer | Technology |
|---|---|
| Frontend | Next.js 14+, React 18+, TypeScript (ported from OAB) |
| Visual builder | React Flow (same) |
| API routes | FastAPI |
| Execution engine | LangGraph Python |
| Database | Postgres via SQLAlchemy (IE-shared) |
| Auth | IE JWT middleware |
| Encryption | IE secret management service (AES-256-GCM) |
| LLM SDKs | `langchain-anthropic`, `langchain-openai`, `langchain-google-genai`, `langchain-groq` |
| Code sandbox | E2B Python SDK |
| Testing | pytest + Playwright (IE's stack) |

---

## 3. The Workflow Data Model

A workflow is a plain JSON document with two arrays. Here's a minimal example:

```json
{
  "id": "wf_abc123",
  "name": "My Simple Workflow",
  "nodes": [
    {
      "id": "node_0",
      "type": "start",
      "position": { "x": 100, "y": 100 },
      "data": {
        "nodeType": "start",
        "nodeName": "Start",
        "inputVariables": [
          { "name": "userQuery", "type": "string", "required": true, "description": "User's question" }
        ]
      }
    },
    {
      "id": "node_1",
      "type": "agent",
      "position": { "x": 400, "y": 100 },
      "data": {
        "nodeType": "agent",
        "nodeName": "Answer",
        "instructions": "Answer the user's question: {{input.userQuery}}",
        "model": "anthropic/claude-sonnet-4-5-20250929",
        "mcpServerIds": [],
        "mcpTools": []
      }
    },
    {
      "id": "node_2",
      "type": "end",
      "position": { "x": 700, "y": 100 },
      "data": {
        "nodeType": "end",
        "nodeName": "End"
      }
    }
  ],
  "edges": [
    { "id": "e1", "source": "node_0", "target": "node_1" },
    { "id": "e2", "source": "node_1", "target": "node_2" }
  ]
}
```

### Node shape (TypeScript, from `lib/workflow/types.ts`)

```typescript
interface WorkflowNode {
  id: string;
  type: NodeType;
  position: { x: number; y: number };
  data: NodeData;
}

type NodeType =
  | 'start' | 'end'
  | 'agent' | 'mcp'
  | 'http' | 'transform' | 'extract'
  | 'if-else' | 'while' | 'user-approval'
  | 'set-state' | 'guardrails' | 'note'
  | 'arcade' | 'gamma-ai' | 'vector-db';

interface NodeData {
  nodeType: NodeType;
  nodeName: string;
  // ... type-specific fields (see Section 8 for each node type)
}
```

### Edge shape

```typescript
interface WorkflowEdge {
  id: string;
  source: string;          // Source node id
  target: string;          // Target node id
  sourceHandle?: string;   // For multi-output nodes (e.g., if-else)
  targetHandle?: string;
  type?: string;           // 'smoothstep' (default)
}
```

---

## 4. The State Model

At execution time, the workflow runs on a shared state object. This is LangGraph's "state" in the StateGraph sense.

### WorkflowState shape

```typescript
interface WorkflowState {
  workflowId: string;
  executionId: string;
  userId?: string;
  tenantId?: string;

  // The variables reducer — merged via spread
  variables: {
    input: Record<string, any>;           // Initial input
    lastOutput?: any;                      // Most recent node's output
    [key: string]: any;                    // Named outputs from nodes
  };

  // Per-node results
  nodeResults: {
    [nodeId: string]: {
      status: 'pending' | 'running' | 'completed' | 'failed' | 'paused';
      startedAt?: string;
      completedAt?: string;
      output?: any;
      error?: string;
      toolCalls?: any[];
      usage?: { input_tokens: number; output_tokens: number; total_tokens: number };
    };
  };

  // Current node being executed
  currentNodeId?: string;

  // Pending auth (for Arcade and other auth flows)
  pendingAuth?: { authUrl: string; authId: string };
}
```

### Reducer pattern

LangGraph state is never mutated directly. Each node returns a partial state update:

```typescript
return {
  variables: { ...state.variables, myOutput: result },
  nodeResults: { ...state.nodeResults, [nodeId]: { status: 'completed', output: result } }
};
```

LangGraph's reducer merges this into the global state. Critical for checkpoint/resume: mutation breaks replay.

---

## 5. The Execution Engine

Located in `lib/workflow/langgraph.ts` (~1400 lines). The `LangGraphExecutor` class:

### Constructor
```typescript
constructor(
  workflow: Workflow,
  onNodeUpdate?: (nodeId: string, result: NodeExecutionResult) => void,
  apiKeys?: ApiKeys,
  langSmithConfig?: LangSmithRuntimeConfig
)
```

### Build phase
1. Parse `workflow.nodes` and `workflow.edges`
2. Create a `StateGraph` with channels for `variables`, `nodeResults`, `currentNodeId`, etc.
3. For each node:
   - Create a function that calls the appropriate executor (`executeAgentNode`, `executeMCPNode`, etc.)
   - Add the function as a graph node
4. For each edge:
   - If source has a single output: `graph.addEdge(source, target)`
   - If source is `if-else` or `while`: `graph.addConditionalEdges(source, routerFn)`
5. Attach the checkpointer (`ConvexCheckpointSaver` in OAB, Postgres-based in Composer)
6. `graph.compile()` returns the executable graph

### Execution phase
```typescript
async executeStream(input: any) {
  const initialState = { /* ... */ };
  const threadId = `thread_${this.workflow.id}_${Date.now()}`;

  const stream = await this.graph.stream(initialState, getLangGraphConfig({
    configurable: { thread_id: threadId },
    streamMode: "values",
    recursionLimit: 100,
  }, this.langSmithConfig));

  for await (const state of stream) {
    // Emit events for each state transition
    yield { type: 'state_update', state };
  }
}
```

### Resume phase (for human-in-the-loop)

When a `user-approval` node interrupts execution, state is checkpointed to Convex. Later:

```typescript
async resume(threadId: string, resumeValue: any) {
  const command = new Command({ resume: resumeValue });
  const stream = await this.graph.stream(command, getLangGraphConfig({
    configurable: { thread_id: threadId },
    streamMode: "values",
    recursionLimit: 100,
  }, this.langSmithConfig));
  // ... emit events
}
```

---

## 6. Data Storage (Convex Schema)

From `convex/schema.ts`. Port each table to Postgres for Composer:

| Table | Purpose |
|---|---|
| `workflows` | Workflow definitions (nodes, edges, metadata) |
| `executions` | Workflow execution records (status, input, output, nodeResults) |
| `checkpoints` | LangGraph checkpoints for resume |
| `checkpoint_writes` | LangGraph checkpoint writes (concurrent task state) |
| `mcpServers` | User-registered MCP servers |
| `mcpOAuthTokens` | Encrypted OAuth access/refresh tokens |
| `mcpOAuthStates` | Ephemeral OAuth state (PKCE, CSRF); 10-min TTL, single-use |
| `approvals` | Human-in-the-loop approval records |
| `userLLMKeys` | User's encrypted LLM API keys |
| `userToolKeys` | User's encrypted tool API keys |
| `apiKeys` | User-generated API keys for programmatic access |
| `uiBuilderConfigurations` | UI Builder configs (deferred for Composer) |
| `rateLimits` | Distributed rate limiter records |
| `cache` | Distributed cache entries |
| `arcadeAuth` | Arcade OAuth records |
| `userMCPs` | Cursor-style MCP server imports |

---

## 7. Auth Architecture (OAB, for Reference)

OAB uses NextAuth.js with Azure AD provider. Every API route pattern:

```typescript
import { getServerSession } from 'next-auth';
import { authOptions } from '@/auth';

export async function POST(req: NextRequest) {
  const session = await getServerSession(authOptions);
  if (!session?.user?.id) {
    return NextResponse.json({ error: 'Auth required' }, { status: 401 });
  }
  const userId = session.user.id;
  // ... handle request
}
```

**In Composer**, replace with IE's JWT middleware. Equivalent FastAPI:

```python
from fastapi import Depends, HTTPException
from .security.auth import verify_jwt

async def get_current_user(token: str = Depends(verify_jwt)) -> AuthContext:
    # Returns { userId, tenantId, roles[] }
    ...

@router.post("/workflows")
async def create_workflow(
    payload: WorkflowCreate,
    user: AuthContext = Depends(get_current_user),
):
    # user.userId, user.tenantId, user.roles are available
    ...
```

---

## 8. Node Type Deep-Dives

Each executor is in `lib/workflow/executors/`. Here's what each one does and how to port it.

### 8.1 Start Node (`start`)

- **Purpose:** Entry point. Declares input variables.
- **No executor needed;** handled inline in the graph builder. Inputs become `state.variables.input`.
- **Port:** Trivial. In Python, validate input against declared schema using Pydantic.

### 8.2 End Node (`end`)

- **Purpose:** Terminal node. Marks the workflow output.
- **Executor:** minimal; formats final output.
- **Port:** Trivial.

### 8.3 Agent Node (`agent`)

- **Executor file:** `lib/workflow/executors/agent.ts` (~900 lines — the most complex)
- **Purpose:** Call an LLM with optional tools/MCP servers. Supports provider-specific gotchas, agentic loops, tool-result handling.
- **Key logic:**
  1. Resolve MCP server IDs to full configs (with OAuth tokens injected) via `resolveMCPServers(ids, userId)`
  2. Fetch tool definitions from each MCP server (`fetchMcpTools`)
  3. Flatten and deduplicate tools
  4. Instantiate standard tools via `ToolFactory` (Firecrawl, Tavily, etc.)
  5. Send LLM request with tools + messages
  6. If LLM requests tool calls, execute each:
     - Standard tools: `tool.invoke(args)`
     - MCP tools: `executeMcpTool(server, name, args, apiKeys)`
  7. Loop: append tool results → re-send → until LLM stops calling tools (max 10 iterations)
- **Python port strategy:**
  - Use LangChain's `ChatAnthropic.bind_tools()` pattern
  - LangGraph Python has a `create_react_agent` helper that handles the tool loop
  - Custom MCP tool wrapper: LangChain `Tool` class wrapping `execute_mcp_tool` function

### 8.4 MCP Node (`mcp`)

- **Executor file:** `lib/workflow/executors/mcp.ts`
- **Purpose:** Direct MCP tool invocation — no LLM reasoning. Used when the workflow author already knows which tool to call with what arguments.
- **Key logic:** Resolve server → select tool → substitute variables in args → `executeMcpTool`
- **Port:** Direct translation to Python. Has hardcoded Firecrawl SDK path for backwards compat; in Composer, route all servers through the generic MCP HTTP client.

### 8.5 HTTP Node (`http`)

- **Executor file:** `lib/workflow/executors/http.ts`
- **Purpose:** Arbitrary HTTP request. Supports headers, body, variable substitution.
- **Security:** SSRF protection — blocks private IPs and metadata endpoints (`169.254.169.254` AWS metadata, `127.0.0.1`, `10.*`, `172.16-31.*`, `192.168.*`).
- **Port:** Use Python `httpx` + `ipaddress` module for SSRF checks.

### 8.6 Transform Node (`transform`)

- **Executor file:** `lib/workflow/executors/data.ts`
- **Purpose:** Execute user-provided JavaScript/Python code on workflow variables.
- **Security:** Uses E2B sandbox. **Never** use `Function()` constructor or `eval`.
- **Port:** E2B Python SDK. Same sandbox model.

### 8.7 Extract Node (`extract`)

- **Executor file:** `lib/workflow/executors/extract.ts`
- **Purpose:** LLM-powered structured data extraction using a schema.
- **Key logic:** Given text + schema → LLM returns JSON matching the schema (using provider's JSON mode or structured output features).
- **Port:** LangChain's `with_structured_output()` on chat models. Schema via Pydantic.

### 8.8 If/Else Node (`if-else`)

- **Executor file:** `lib/workflow/executors/logic.ts`
- **Purpose:** Conditional branching based on an expression.
- **Security:** Uses `mathjs` for expression evaluation (never `eval()`).
- **Graph integration:** Uses LangGraph `addConditionalEdges` with a router function that returns either `'if'` or `'else'`.
- **Port:** `simpleeval` or `asteval` for Python expression evaluation. Whitelist operators and functions.

### 8.9 While Node (`while`)

- **Executor file:** `lib/workflow/executors/logic.ts` (shared with if-else)
- **Purpose:** Loop until condition is false or max iterations reached.
- **Implementation:** LangGraph conditional edge that either loops back to a specific node or exits.
- **Port:** Same pattern in LangGraph Python. Iteration counter stored in state.

### 8.10 User-Approval Node (`user-approval`)

- **Executor file:** (handled in `langgraph.ts` as a special node)
- **Purpose:** Pause execution for human approval. Critical for governance.
- **Implementation:** Uses LangGraph's `interrupt()` primitive.
  1. Node calls `interrupt({ type: 'approval', message, approvalId })`
  2. LangGraph checkpoints state and returns control to caller
  3. API endpoint persists the checkpoint to Convex; creates an approval record
  4. User later approves/rejects via UI; triggers `resume()` with the decision
- **Port:** LangGraph Python has `interrupt()` too. Same pattern.

### 8.11 Set-State Node (`set-state`)

- **Executor file:** Inline in `langgraph.ts`
- **Purpose:** Directly manipulate state variables with expressions.
- **Port:** Straightforward; uses the expression evaluator.

### 8.12 Guardrails Node (`guardrails`)

- **Executor file:** `lib/workflow/executors/guardrails.ts`
- **Purpose:** Content moderation / safety checks on text.
- **Implementation:** Calls a moderation API (OpenAI Moderation, Anthropic, or a custom LLM call)
- **Port:** Direct. LangChain has built-in moderation chains.

### 8.13 Note Node (`note`)

- **Purpose:** Documentation-only. No execution; just a visual annotation on the canvas.
- **Port:** Trivial.

### 8.14 Vector-DB Node (`vector-db`)

- **Executor file:** `lib/workflow/executors/vector-db.ts`
- **Purpose:** Query vector databases (Pinecone, Qdrant, Weaviate, Chroma, Milvus)
- **Port:** All these DBs have Python SDKs. LangChain has integrations for each.

### 8.15 Arcade / Gamma (skip for Phase 1)

Both are proprietary 3rd-party integrations. Skip unless there's demand.

---

## 9. MCP Subsystem (Critical Port Target)

### 9.1 Architecture

OAB supports 4 MCP authentication types:

- `none` — no auth
- `api-key` — static header (URL-embedded or Authorization header)
- `bearer` — static Bearer token
- `oauth` — OAuth 2.0 with Authorization Code + PKCE or Client Credentials

### 9.2 OAuth 2.0 flow

```
User clicks "Connect" in Settings UI
 ↓
Frontend POSTs to /api/mcp/oauth/authorize
 ↓
Backend generates:
  - codeVerifier (PKCE, base64url 32 bytes)
  - codeChallenge (SHA-256 of verifier, base64url)
  - state (CSRF, base64url 32 bytes)
 ↓
Backend stores {state, userId, mcpServerId, codeVerifier, oauthConfig, mcpUrl} in mcpOAuthStates (10-min TTL)
 ↓
Backend returns authorization URL with params:
  ?response_type=code
  &client_id=...
  &redirect_uri=.../api/mcp/oauth/callback
  &state=...
  &code_challenge=...
  &code_challenge_method=S256
  &scope=...
  &resource=... ← RFC 8707; REQUIRED by Highspot
 ↓
Frontend opens popup to this URL
 ↓
User authorizes on MCP provider's site
 ↓
Provider redirects to /api/mcp/oauth/callback?code=...&state=...
 ↓
Backend consumes the state record; retrieves codeVerifier, oauthConfig, mcpUrl
 ↓
Backend POSTs to oauthConfig.tokenUrl with:
  grant_type=authorization_code
  code=...
  redirect_uri=...
  client_id=...
  code_verifier=...
  client_secret=... (if non-public client)
  resource=mcpUrl ← RFC 8707; REQUIRED; must match authorize request
 ↓
Provider returns {access_token, refresh_token, expires_in, token_type, scope}
 ↓
Backend encrypts tokens with AES-256-GCM; stores in mcpOAuthTokens
 ↓
Backend returns success; popup closes; parent window receives postMessage
```

### 9.3 Auto-refresh

During execution, `getValidAccessToken(userId, mcpServerId)`:
1. Queries `mcpOAuthTokens` for user + server
2. If `Date.now() < expiresAt - 5*60*1000` → decrypt and return
3. Else if `encryptedRefreshToken` exists:
   - POST to tokenUrl with `grant_type=refresh_token`, refresh_token, client_id, client_secret (decrypted), `resource=server.url`
   - Store new tokens; return new access token
4. Else return null (user must re-authenticate)

### 9.4 Manual MCP tool calling

**Do NOT** use Anthropic's native `mcp_servers` connector. **Do NOT** use `betas: ['mcp-client-2025-04-04']`. Both fail on large tool definitions.

Instead, in the agent executor:

```typescript
// Build mcp_servers as empty (skip Anthropic's native connector)
const mcpServers = [];

// Route ALL MCP tools through manual calling
const manualMcpTools = flattenedMcpTools;

// Each manual tool is: { name, description, inputSchema, serverName, serverUrl, serverAuthToken }
// In the finalTools array passed to the LLM, include these as regular tools
const finalTools = flattenedMcpTools.map(t => ({
  name: t.name,
  description: t.description,
  input_schema: t.inputSchema || t.schema || t.input_schema || {}  // ⚠️ Support all 3 keys
}));

// When LLM returns tool_use, handle it ourselves:
if (tu.serverUrl) {
  const result = await executeMcpTool(
    { name: tu.serverName, url: tu.serverUrl, authToken: tu.serverAuthToken },
    tu.name,
    tu.input,
    apiKeys
  );
}
```

### 9.5 executeMcpTool (HTTP JSON-RPC)

```typescript
const response = await fetch(mcpServer.url, {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'Accept': 'application/json, text/event-stream',
    ...(authToken ? { 'Authorization': `Bearer ${authToken}` } : {}),
  },
  body: JSON.stringify({
    jsonrpc: '2.0',
    id: Date.now(),
    method: 'tools/call',
    params: { name: toolName, arguments: args }
  })
});

// Handle both JSON and SSE responses
const contentType = response.headers.get('content-type');
let result;
if (contentType?.includes('text/event-stream')) {
  // Parse SSE lines looking for `data: {json}`
  const text = await response.text();
  result = parseSseForResult(text);
} else {
  result = await response.json();
}

if (result.error) throw new Error(`MCP error ${result.error.code}: ${result.error.message}`);
return unwrapMCPResponse(result);
```

### 9.6 fetchMcpTools (tools/list)

```typescript
const response = await fetch(mcpServer.url, {
  method: 'POST',
  headers: { /* ... */ },
  body: JSON.stringify({
    jsonrpc: '2.0',
    id: Date.now(),
    method: 'tools/list',
    params: {}
  })
});

// Parse response (JSON or SSE)
const data = await parseResponse(response);
return data.result.tools || [];  // Array of { name, description, inputSchema }
```

---

## 10. Variable Substitution Engine

File: `lib/workflow/variable-substitution.ts`

Syntax: `{{path.to.variable}}`

Supported paths:
- `{{input.userQuery}}` — input variables
- `{{lastOutput}}` — most recent node's output
- `{{nodeResults.node_3.output}}` — specific node's output
- `{{variables.customKey}}` — arbitrary state variable
- Nested: `{{input.RFP_Document.content}}` (file extraction)

### File content injection

If a variable reference resolves to an object with `{ storageId, content, text }`, the engine auto-injects `content` or `text` (extracted text of uploaded PDF/DOCX/MD).

### Implementation sketch (Python port)

```python
import re
from typing import Any

VAR_PATTERN = re.compile(r'\{\{([^}]+)\}\}')

def substitute_variables(text: str, state: WorkflowState) -> str:
    def replace(match: re.Match) -> str:
        path = match.group(1).strip()
        value = resolve_path(state, path)
        if isinstance(value, dict) and ('content' in value or 'text' in value):
            value = value.get('content') or value.get('text')
        if value is None:
            return match.group(0)  # Leave unresolved
        if not isinstance(value, str):
            return json.dumps(value)
        return value

    return VAR_PATTERN.sub(replace, text)
```

---

## 11. Security (Non-Negotiable Patterns)

### 11.1 Expression evaluator

**Never use `eval()` or `exec()`.** Use `simpleeval`:

```python
from simpleeval import SimpleEval, DEFAULT_FUNCTIONS, DEFAULT_OPERATORS

def safe_evaluate(expression: str, context: dict) -> Any:
    evaluator = SimpleEval(
        operators=DEFAULT_OPERATORS,
        functions=DEFAULT_FUNCTIONS,
        names=context,
    )
    return evaluator.eval(expression)
```

### 11.2 SSRF protection

Block these in HTTP node:
- Private IP ranges: 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 127.0.0.0/8
- Link-local: 169.254.0.0/16 (includes AWS metadata 169.254.169.254)
- IPv6 equivalents: ::1, fc00::/7, fe80::/10
- Localhost: `localhost`, `*.localhost`

```python
import ipaddress
from urllib.parse import urlparse

def is_safe_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        host = parsed.hostname
        if not host:
            return False
        if host.lower() in ('localhost',) or host.endswith('.localhost'):
            return False
        # Resolve and check IP
        ip = ipaddress.ip_address(host) if _is_ip(host) else None
        if ip and (ip.is_private or ip.is_loopback or ip.is_link_local):
            return False
        # For hostnames, DNS resolve and check all addresses
        # ... (implementation depends on your DNS library)
        return True
    except ValueError:
        return False
```

### 11.3 Secret encryption

All tokens, API keys, client secrets are encrypted before DB storage using AES-256-GCM. Never log decrypted secrets. Never send decrypted secrets to the client.

### 11.4 Fail-closed auth

When uncertain about auth, **deny**. Never fall back to unauthenticated. The April 2026 MCP resolver bug was caused by a Convex query returning `[]` when unauth'd — silent failure that looked like "no servers configured." Log the denial explicitly.

### 11.5 Rate limiting

Execution endpoints must be rate-limited. Rate limiter must be fail-closed on critical endpoints (return 503 if backend is down) and fail-open on non-critical ones.

---

## 12. LLM Provider Response Formats (Reference)

### Anthropic

```json
{
  "id": "msg_xxx",
  "content": [
    { "type": "text", "text": "..." },
    { "type": "tool_use", "id": "toolu_xxx", "name": "...", "input": {...} }
  ],
  "stop_reason": "end_turn" | "tool_use",
  "usage": { "input_tokens": 10, "output_tokens": 5 }
}
```

### OpenAI/Groq

```json
{
  "choices": [{
    "message": {
      "content": "...",
      "tool_calls": [{
        "id": "call_xxx",
        "function": { "name": "...", "arguments": "{...}" }  // ⚠️ JSON string
      }]
    }
  }]
}
```

### Google Gemini

```json
{
  "candidates": [{
    "content": {
      "parts": [
        { "text": "..." },
        { "functionCall": { "name": "...", "args": {...} } }
      ]
    }
  }]
}
```

**Use LangChain's chat model abstractions in Composer** — they handle all these variations.

---

## 13. Execution Streaming (SSE)

OAB's execute-stream route emits these events:

```
event: workflow_started
data: { "executionId": "...", "timestamp": "..." }

event: node_started
data: { "nodeId": "...", "nodeName": "...", "nodeType": "..." }

event: node_completed
data: { "nodeId": "...", "result": {...} }

event: node_failed
data: { "nodeId": "...", "error": "..." }

event: node_paused
data: { "nodeId": "...", "status": "pending-approval" | "pending-authorization" }

event: workflow_completed
data: { "executionId": "...", "output": {...} }

event: error
data: { "error": "..." }
```

Port these event shapes directly to Composer. If IE uses WebSocket, map each SSE event to a WebSocket message with `type` field.

---

## 14. Testing Strategy

Port the test philosophy from OAB:

- **Unit tests per executor:** Mock LLM + MCP. Assert input/output behavior.
- **Integration tests per workflow type:** End-to-end with mocked externals.
- **Model regression tests:** Test all LLM providers with provider-specific mocks.
- **Security tests:** Expression sandbox escape attempts, SSRF attempts, ownership violations, fail-closed auth.
- **OAuth flow tests:** Full PKCE flow, token refresh, RFC 8707 resource param.

---

## 15. The April 2026 Critical Fixes

These six fixes took a long debugging session to find. Each is a trap an agent implementing Composer might fall into. Implement all six from day one.

### Fix 1: RFC 8707 `resource` parameter
Some MCP servers (Highspot) require the `resource` parameter on:
- Authorization request
- Token exchange request
- Token refresh request
- Client credentials grant

The value must match in all places — typically the MCP server URL.

### Fix 2: Server-side OAuth token retrieval for test-connection
The test-connection endpoint must fetch OAuth tokens server-side for OAuth-type servers. Don't rely on the client to send the token.

### Fix 3: Authenticated Convex client in the resolver
This is OAB-specific but the principle transfers: when the execution engine calls the DB for MCP server configs, it must be authenticated. Unauthenticated queries return empty results silently. In Composer with Postgres, this translates to: always pass `userId` + `tenantId` through the execution path.

### Fix 4: Do not use Anthropic's native `mcp_servers` connector
Anthropic's `mcp_servers` + `betas: ['mcp-client-2025-04-04']` fails on large tool definitions (JSON parse error at column 73579+ for Highspot). Always use manual HTTP JSON-RPC calling.

### Fix 5: `inputSchema` (camelCase) is the MCP spec key
When parsing `tools/list` responses, check `inputSchema` first (MCP spec), then fall back to `schema` and `input_schema` for quirky servers.

### Fix 6: LangSmith config must be explicitly threaded
Do not rely on `process.env` for LangSmith config in production (env vars may come from DB). Pass the config object explicitly through the execution path.

---

## 16. Porting Checklist

Use this as a concrete implementation checklist.

### Phase 1.0 — Scaffolding
- [ ] Create `apps/composer/composer-ui/` and `apps/composer/composer-service/` in IE monorepo
- [ ] FastAPI app skeleton with `/health` endpoint
- [ ] Prisma migrations for the 8 Composer-specific tables
- [ ] IE JWT middleware integrated into FastAPI
- [ ] Next.js UI skeleton with IE auth cookie reading
- [ ] `npx turbo build` + `npx turbo test` passes

### Phase 1.1 — Execution Engine
- [ ] Port `WorkflowState`, `WorkflowNode`, `WorkflowEdge` types to Pydantic
- [ ] LangGraph executor that takes a workflow JSON and builds a StateGraph
- [ ] Postgres-based `Checkpointer` class implementing `BaseCheckpointSaver`
- [ ] Start and End node handlers
- [ ] Anthropic-via-LangChain agent executor (no tools yet)
- [ ] HTTP node executor with SSRF protection
- [ ] SSE streaming endpoint `POST /workflows/{id}/execute-stream`
- [ ] Minimal E2E test: Start → Agent → End executes

### Phase 1.2 — MCP Integration
- [ ] MCP HTTP JSON-RPC client (`tools/list`, `tools/call`)
- [ ] OAuth 2.0 authorize endpoint with PKCE and RFC 8707 resource
- [ ] OAuth callback endpoint with token exchange and encrypted storage
- [ ] Token refresh logic with RFC 8707 resource
- [ ] MCP node executor
- [ ] Agent node with MCP tool attachment (manual tool calling)
- [ ] Test connection endpoint with server-side OAuth token retrieval
- [ ] Integration test: complete Highspot (or similar) OAuth flow + workflow execution

### Phase 1.3 — Visual Builder UI
- [ ] Port React Flow canvas from OAB
- [ ] Node palette with 12 types (drag to canvas)
- [ ] Per-type node panels (ported from OAB's `*NodePanel.tsx` components)
- [ ] Workflow save/load via FastAPI REST
- [ ] Execution panel with live SSE consumption

### Phase 1.4 — Governance & Tenancy
- [ ] All DB queries scope by `tenantId`
- [ ] RBAC: `composer.read`, `composer.write`, `composer.execute`, `composer.admin`
- [ ] Audit logging for workflow create/edit/delete/execute
- [ ] Quota/rate limit integration

### Phase 1.5 — Remaining Nodes
- [ ] Transform (E2B sandbox, Python SDK)
- [ ] Extract (LangChain structured output)
- [ ] If/Else and While (simpleeval expression evaluator)
- [ ] User-Approval (LangGraph interrupt + Postgres checkpoint)
- [ ] Set-State
- [ ] Guardrails
- [ ] Note

### Phase 1.6 — Integration with IE
- [ ] Agent node dropdown populated from Maestro's agent catalog
- [ ] MCP server list populated from IE's MCP orchestrator
- [ ] LangSmith tracing enabled via IE secret management
- [ ] WebSocket streaming in addition to SSE
- [ ] Feature flag for per-tenant enablement

### Phase 1.7 — Production Readiness
- [ ] Full security audit
- [ ] Load testing
- [ ] Error monitoring integration
- [ ] Runbook / on-call docs
- [ ] First customer pilot

---

## 17. Useful File References from OAB

When in doubt, look at these files:

| Subsystem | OAB File |
|---|---|
| Workflow types | `lib/workflow/types.ts` |
| Executor (core) | `lib/workflow/langgraph.ts` |
| Agent executor | `lib/workflow/executors/agent.ts` |
| MCP client utilities | `lib/workflow/executors/mcp-utils.ts` |
| MCP node executor | `lib/workflow/executors/mcp.ts` |
| Variable substitution | `lib/workflow/variable-substitution.ts` |
| Edge validation | `lib/workflow/edge-cleanup.ts` |
| Safe expression evaluator | `lib/workflow/safe-expression-evaluator.ts` |
| File extraction | `lib/workflow/file-utils.ts` |
| Workflow templates | `lib/workflow/templates.ts` |
| LLM config | `lib/config/llm-config.ts` |
| Tool factory | `lib/workflow/executors/tool-factory.ts` |
| Tool utils | `lib/workflow/executors/tool-utils.ts` |
| MCP resolver | `lib/mcp/resolver.ts` |
| MCP registry | `lib/mcp/mcp-registry.ts` |
| OAuth authorize | `app/api/mcp/oauth/authorize/route.ts` |
| OAuth callback | `app/api/mcp/oauth/callback/route.ts` |
| OAuth token actions | `convex/mcpOAuthTokensActions.ts` |
| Schema | `convex/schema.ts` |
| Test connection | `app/api/test-mcp-connection/route.ts` |
| Execute stream | `app/api/workflows/[workflowId]/execute-stream/route.ts` |
| LangSmith config | `lib/langsmith/config.ts` |
| Execution service | `lib/api/execution-service.ts` |
| Checkpointer (Convex) | `convex/checkpoints.ts` |

---

## 18. Appendix: Prisma Schema for Composer

See `2026-04-15-composer-03-engineering-blueprint.md` Section 6 for the full Prisma schema of all 8 Composer-specific tables.

---

## 19. Appendix: OAuth Flow as a Sequence Diagram

```
User         Composer-UI      Composer-Service    MCP Provider     Postgres
 │                │                 │                  │              │
 │  Click         │                 │                  │              │
 │  "Connect"     │                 │                  │              │
 ├───────────────▶│                 │                  │              │
 │                │ POST /mcp/oauth/│                  │              │
 │                │   authorize     │                  │              │
 │                ├────────────────▶│                  │              │
 │                │                 │ Generate PKCE,   │              │
 │                │                 │ state, resource  │              │
 │                │                 ├──────────────────┼─────────────▶│
 │                │                 │                  │  INSERT state │
 │                │                 │  Build auth URL  │              │
 │                │                 │  (with resource) │              │
 │                │◀────────────────┤                  │              │
 │                │ {authUrl,state} │                  │              │
 │                │                 │                  │              │
 │                │ Open popup      │                  │              │
 │                ├─────────────────┼─────────────────▶│              │
 │                │                 │                  │              │
 │  Authorize     │                 │                  │              │
 ├────────────────┼─────────────────┼─────────────────▶│              │
 │                │                 │                  │              │
 │                │                 │                  │  Redirect    │
 │                │                 │                  │  with code   │
 │                │                 │◀─────────────────┤              │
 │                │                 │                  │              │
 │                │                 │ GET /mcp/oauth/  │              │
 │                │                 │   callback       │              │
 │                │                 │                  │              │
 │                │                 │ Consume state    │              │
 │                │                 ├──────────────────┼─────────────▶│
 │                │                 │                  │  SELECT + DELETE
 │                │                 │◀─────────────────┼──────────────┤
 │                │                 │                  │              │
 │                │                 │ POST /token      │              │
 │                │                 │ (with code,      │              │
 │                │                 │  verifier,       │              │
 │                │                 │  resource ★)     │              │
 │                │                 ├─────────────────▶│              │
 │                │                 │                  │ Validate     │
 │                │                 │                  │ (resource    │
 │                │                 │                  │  must match) │
 │                │                 │◀─────────────────┤              │
 │                │                 │ {tokens}         │              │
 │                │                 │                  │              │
 │                │                 │ Encrypt tokens   │              │
 │                │                 ├──────────────────┼─────────────▶│
 │                │                 │                  │  INSERT token │
 │                │                 │                  │              │
 │                │                 │ Render popup     │              │
 │                │                 │ success HTML     │              │
 │                │◀────────────────┤                  │              │
 │                │                 │                  │              │
 │                │ postMessage     │                  │              │
 │                │ success         │                  │              │
 │◀───────────────┤                 │                  │              │
 │                │                 │                  │              │
```

★ RFC 8707 critical: `resource` parameter must match between authorization and token exchange.

---

## 20. Final Note to the Implementing Agent

When porting any subsystem, follow this principle: **reproduce behavior, not code.** Python has better idioms for many things — use them. But the semantics (what the system does when given input X) must match OAB's. Use OAB as a behavioral oracle:

1. Pick a subsystem (e.g., variable substitution)
2. Write your Python implementation
3. Create a test case: given the same input (workflow JSON, state), does your Python produce the same output as OAB's TypeScript?
4. If no: investigate why. Usually it's a corner case in the original code.
5. If yes: move on.

When in doubt about a design decision, refer to `2026-04-15-composer-04-personal-memo.md` which captures the author's working thoughts — including decisions that are intentional vs. decisions that are "we did it this way and no one questioned it."

Good luck. This is a buildable project. The hard problems are already solved in OAB; Composer is disciplined re-implementation.
