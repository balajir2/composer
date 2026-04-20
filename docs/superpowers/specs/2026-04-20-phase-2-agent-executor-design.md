# Phase 2 — Agent Executor + LLM Providers: Design Spec

**Author:** Balaji Rajan
**Date:** 2026-04-20
**Status:** Approved → ready for implementation plan
**Phase ref:** Phase 2 of [`docs/design/2026-04-20-composer-python-port-design.md`](../../design/2026-04-20-composer-python-port-design.md)
**Governing ADRs:** [ADR-0006](../../design/decisions.md#adr-0006-agent-executor--langchain-chat-models-custom-10-iteration-loop-pull-phase-36-hooks-forward), [ADR-0007](../../design/decisions.md#adr-0007-variable-substitution--full-oab-parity), [ADR-0008](../../design/decisions.md#adr-0008-structured-output--langchain-with_structured_output-shared-primitive-for-agent--extract)

---

## 1. Goal

Ship the Agent executor end-to-end: a workflow with `start → agent → end` runs for any of four LLM providers (Anthropic, OpenAI, Google, Groq), supports **Tavily** web-search as a bound tool, renders `{{...}}`-templated instructions with the full OAB substitution engine, and parses structured JSON output when `outputFormat: "JSON"`. All four providers verified via both mocked unit tests and real-provider integration tests.

**Phase 2 is done when:**

1. `src/executors/agent.py` implements the full agentic loop architecture: provider-agnostic chat model invocation, `bind_tools` for cross-provider tool format, `while iter < MAX_ITERATIONS=10`, parallel tool execution, usage accumulation. Works with `0` tools (no-loop-body) or `n` tools.
2. `src/llm/providers.py` maps `model: "provider/model-name"` strings to concrete `BaseChatModel` instances via `langchain-anthropic`, `langchain-openai`, `langchain-google-genai`, `langchain-groq`. Reasoning-model detection (`o1/o3/gpt-5` → `max_completion_tokens` via LangChain's native handling).
3. `src/llm/structured_output.py` exports a single primitive `structured_invoke(chat_model, messages, schema=None, json_mode=False)`. Used by Agent (Phase 2) and reserved for Extract (Phase 4).
4. `src/variable_substitution.py` implements the OAB-parity `{{...}}` engine: flat keys, dotted paths, `state.variables.<path>` / `state.nodeResults.<path>` explicit prefixes, prototype-pollution guard.
5. `src/tools/` package with `base.py` (ToolRegistry protocol) and `tavily.py` (search tool). Phase 3 MCP tools and Phase 6 remaining standard tools register through the same protocol.
6. Agent executor registered via `@register_executor("agent")` in the Phase 1 registry.
7. Unit tests: Start→Agent→End for each of 4 providers using LangChain's `FakeListChatModel`. Variable substitution tests. Structured-output tests. Tavily tool-wrapper tests.
8. Integration tests (`@pytest.mark.integration`): Start→Agent→End against each real LLM provider when its key is set (gracefully skip providers without keys). Start→Agent(with Tavily)→End hitting the real Tavily API.
9. One OAB regression test ported: Agent with `outputFormat: "Text"` and no tools.
10. CHANGELOG + CLAUDE.md phase table updated (Phase 2 ✅, Phase 3 ⏭).
11. `uv run ruff check && ruff format --check && pyright && pytest -m "not integration"` green. Integration tests green against real keys.

**Non-goals for Phase 2** (each has its own later phase):
- MCP tool resolution / OAuth — Phase 3.
- Conditional routing, loops, HTTP / Transform / Extract / SetState / If-Else / While executors — Phase 4.
- User-approval interrupt — Phase 5.
- Standard tools other than Tavily (Serper, Firecrawl, Browserless, Gamma, Arcade) — Phase 6.
- SSE streaming — Phase 5.
- Workflow CRUD beyond what Phase 1 shipped — Phase 7.

---

## 2. Context

Phase 1 (complete, pushed at `016b15d` on `main`) delivered the execution engine: Prisma schema, 18 node Pydantic models, graph builder, Prisma checkpointer, orchestrator, three REST endpoints, and verified end-to-end start→end execution against real Postgres (83/83 tests pass). Phase 2 adds the first real executor — agents are 70% of what Composer exists to run.

**Stack additions (none; everything is already in `pyproject.toml` from Phase 0):**
- `langchain-anthropic`, `langchain-openai`, `langchain-google-genai`, `langchain-groq`
- `langchain-core` (for `BaseChatModel`, `BaseTool`, `ToolMessage`, etc.)
- `httpx` (for the Tavily tool's API calls)

**New env vars (Phase 2 consumes; already placeholders in `.env.example` per ADR updates):**
- `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY`, `GROQ_API_KEY`
- `TAVILY_API_KEY`
- `LANGCHAIN_TRACING_V2`, `LANGCHAIN_API_KEY`, `LANGCHAIN_PROJECT`, `LANGCHAIN_ENDPOINT` (LangSmith tracing, already in config from Phase 0)

---

## 3. Behavioral reference from OAB

All runtime behavior mirrors OAB's TypeScript implementation at `D:/GitHub/open-agent-builder` (read-only per CLAUDE.md Rule 2). Specific references:

- **Agent executor:** `lib/workflow/executors/agent.ts` (1289 lines — we compress to ~400 lines Python via LangChain abstractions).
- **Agent switch arm in graph builder:** `lib/workflow/langgraph.ts:590–599`.
- **Variable substitution engine:** `lib/workflow/variable-substitution.ts`.
- **Tavily tool:** `lib/tools/tavily.ts`.
- **Provider detection + API key routing:** `lib/workflow/executors/agent.ts:184–196`.
- **Agentic loop (tools path):** `lib/workflow/executors/agent.ts:366–506` (Anthropic), `893–1015` (OpenAI/Groq), `1111–1223` (Google).
- **Return shape from executor:** `lib/workflow/executors/agent.ts:1269–1279` (the `__agentValue / __agentToolCalls / __chatHistoryUpdates / __variableUpdates / __usage` fields).

Deliberate deviations from OAB, documented as we go:
- **LangChain abstraction layer.** OAB uses raw provider SDKs with manual format dispatch. Composer uses `langchain-{provider}` across all four providers + `chat_model.bind_tools()` for unified tool format. Net effect: ~900 fewer lines of code, same observable behavior.
- **Structured output via `with_structured_output`** instead of naive `JSON.parse`. Per ADR-0008. Flagged in CHANGELOG.
- **MAX_ITERATIONS = 10 retained** (match OAB). Configurable via `AgentNodeData` could be added later but not in Phase 2.

---

## 4. Module layout

```
src/
├── engine/              # unchanged (Phase 1)
├── executors/
│   ├── base.py          # (Phase 1, unchanged)
│   ├── start.py         # (Phase 1)
│   ├── end.py           # (Phase 1)
│   └── agent.py         # NEW: Agent executor, registered @register_executor("agent")
├── llm/                 # NEW package
│   ├── __init__.py
│   ├── providers.py     # model-string → BaseChatModel dispatch
│   └── structured_output.py  # with_structured_output wrapper (shared Agent + Extract)
├── tools/               # NEW package — the Tool Provider Framework
│   ├── __init__.py      # imports providers/* to trigger registration at package load
│   ├── base.py          # ToolProvider ABC, AuthRequirement hierarchy, ToolDefinition, BuildContext
│   ├── registry.py      # register_tool_provider + list_providers + resolve_tools_for_node
│   └── providers/
│       ├── __init__.py  # auto-imports every provider module so registration side-effects run
│       └── tavily.py    # Phase 2's one ToolProvider; Phase 6 adds siblings here
├── mcp/                 # NEW (skeleton only, populated in Phase 3)
│   └── __init__.py      # placeholder — McpToolProvider subclass lands Phase 3
├── variable_substitution.py  # NEW module, top-level per OAB
├── api/                 # unchanged (Phase 1)
├── security/            # unchanged (Phase 1)
├── storage/             # unchanged (Phase 1)
└── main.py              # unchanged (Phase 1)

tests/
├── unit/
│   ├── executors/
│   │   └── test_agent.py         # NEW: per-provider mocked tests
│   ├── llm/
│   │   ├── test_providers.py     # NEW: model-string dispatch, reasoning model detection
│   │   └── test_structured_output.py  # NEW: with_structured_output unit tests
│   ├── tools/
│   │   ├── test_base.py          # NEW: ToolProvider ABC contract tests, AuthRequirement tests
│   │   ├── test_registry.py      # NEW: registration decorator, list_providers, resolve_tools_for_node
│   │   └── providers/
│   │       └── test_tavily.py    # NEW: TavilyProvider contract + Tavily tool call with mocked httpx
│   └── test_variable_substitution.py  # NEW: OAB-parity substitution tests
├── integration/
│   └── test_agent_providers.py   # NEW: Start→Agent→End per provider (real keys)
└── regression/
    └── test_oab_agent_text_output.py  # NEW: one OAB Agent test ported
```

---

## 5. `src/llm/providers.py` — model-string → BaseChatModel dispatch

### 5.1 Interface

```python
from typing import Any
from langchain_core.language_models import BaseChatModel

class UnsupportedProviderError(ValueError): ...
class MissingApiKeyError(RuntimeError): ...

def build_chat_model(
    model_string: str,
    *,
    token_limit: int | None = None,
    temperature: float | None = None,
    extra: dict[str, Any] | None = None,
) -> BaseChatModel:
    """Parse `model_string` as `provider/modelname`, return a LangChain chat model.

    Providers: 'anthropic', 'openai', 'google', 'groq'.
    Raises UnsupportedProviderError for unknown providers.
    Raises MissingApiKeyError if the provider's key is empty in settings.
    """
```

### 5.2 Provider detection

Match OAB's approach (`agent.ts:184–196`): parse at the first `/`. If no `/`, default to `openai` (matching OAB).

### 5.3 Provider → LangChain class

| Provider | Class | Auth |
|---|---|---|
| `anthropic` | `langchain_anthropic.ChatAnthropic` | `settings.anthropic_api_key` |
| `openai` | `langchain_openai.ChatOpenAI` | `settings.openai_api_key` |
| `google` | `langchain_google_genai.ChatGoogleGenerativeAI` | `settings.google_api_key` |
| `groq` | `langchain_groq.ChatGroq` | `settings.groq_api_key` |

### 5.4 Reasoning-model detection

OpenAI `o1-*`, `o3-*`, `gpt-5-*` require `max_completion_tokens` not `max_tokens`. LangChain's `ChatOpenAI` handles this natively when the `model` field is set — no manual dispatch needed on our side. A unit test verifies `build_chat_model("openai/o3-mini", token_limit=100)` produces a client that successfully invokes without the `max_tokens`-vs-`max_completion_tokens` error.

### 5.5 LangSmith tracing

When `settings.langchain_tracing_v2` is true and `settings.langchain_api_key` is set, the LangChain SDK auto-detects (it reads the env vars). No explicit callback plumbing in `build_chat_model`. The Phase 1 `Settings` class exports the four LangSmith settings already; they're picked up by LangChain as long as pydantic-settings propagates them (via `os.environ` set during `Settings.__init__` — a small change we validate in testing).

---

## 6. `src/llm/structured_output.py` — shared structured-output primitive

### 6.1 Interface

```python
from typing import Any
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage

async def structured_invoke(
    chat_model: BaseChatModel,
    messages: list[BaseMessage],
    *,
    schema: dict[str, Any] | None = None,
    json_mode: bool = False,
) -> Any:
    """Invoke `chat_model` with `messages` and return a structured result.

    Three modes:
      - schema=None, json_mode=False: plain text response (just returns .content).
      - schema=None, json_mode=True:  use chat_model.with_structured_output(method="json_mode").
                                      Fallback for Google: prompt-based JSON ask + json.loads.
      - schema=<JSONSchema dict>:     use chat_model.with_structured_output(schema).
                                      Provider-native schema enforcement.
    """
```

### 6.2 Behavior by provider

- **Anthropic + OpenAI + Groq:** `with_structured_output(schema=...)` and `with_structured_output(method="json_mode")` both work natively.
- **Google:** `with_structured_output(schema=...)` works (Google's structured output mode). Schema-less `json_mode` requires a prompt-based workaround: prepend "Respond with JSON only" and `json.loads` the output, returning raw string on parse failure (matching OAB's fallback behavior for the schema-less case).

### 6.3 Provider detection for json_mode fallback

`isinstance(chat_model, ChatGoogleGenerativeAI)` triggers the prompt-based fallback. A small helper `_is_google(chat_model)` for testability.

---

## 7. `src/variable_substitution.py` — OAB-parity template engine

### 7.1 Grammar

- Pattern: `re.compile(r"\{\{([^}]+)\}\}")` (matches OAB's JS regex `/\{\{([^}]+)\}\}/g`).
- Path inside braces: split on `.`, walk `state.variables` (or `state.nodeResults` with explicit `state.variables.` / `state.nodeResults.` prefix).

### 7.2 Algorithm

```python
def substitute(template: str, state: WorkflowStateDict) -> str:
    return _PATTERN.sub(lambda m: _resolve(m.group(1).strip(), state), template)

def _resolve(path: str, state: WorkflowStateDict) -> str:
    segments = path.split(".")
    # Explicit prefix
    if segments[0] == "state":
        if len(segments) < 3:
            return f"{{{{{path}}}}}"  # literal fall-through
        root_name, *segments = segments[1:]
        root = state["variables"] if root_name == "variables" else (
            state["node_results"] if root_name == "nodeResults" else None
        )
        if root is None:
            return f"{{{{{path}}}}}"
    else:
        root = state["variables"]
    value = _walk(root, segments)
    if value is None:
        return f"{{{{{path}}}}}"  # unresolved → literal
    return value if isinstance(value, str) else json.dumps(value)


_UNSAFE_SEGMENTS: frozenset[str] = frozenset({
    "__proto__", "constructor", "prototype",  # JS-side OAB blocks these
    "__class__", "__dict__", "__globals__", "__builtins__",  # Python equivalents
})

def _walk(root: Any, segments: list[str]) -> Any:
    current = root
    for seg in segments:
        if seg in _UNSAFE_SEGMENTS:
            return None  # prototype-pollution guard
        if isinstance(current, dict):
            current = current.get(seg)
        else:
            return None
        if current is None:
            return None
    return current
```

### 7.3 Unresolved behavior

Matching OAB: unresolved placeholders render as literal `{{path}}` in the output. This lets a template reference a not-yet-populated field without raising. Tests cover: `{{missing}}` → `{{missing}}`, `{{variables.bad.path}}` → `{{variables.bad.path}}`.

---

## 8. The Tool Provider Framework — `src/tools/base.py` + `src/tools/registry.py`

### 8.1 Goal

A single, well-defined abstraction that both **standard integrations** (Tavily, Serper, Firecrawl, Browserless, Gamma, Arcade, …) and **MCP servers** (Highspot, Notion, any SSE MCP) implement. Adding a new tool or a new MCP server is a one-file operation. The Agent executor, workflow editor (Phase 10), and health-check UI all consume the same abstraction.

Design goals (in priority order):
1. **Extensibility.** New providers drop into `src/tools/providers/` (standard) or `src/mcp/providers/` (MCP) as a single file. No changes to the Agent executor or registry.
2. **Uniformity.** Standard and MCP paths share 90% of their surface area. MCP-specific behaviour (OAuth, `tools/list` RPC, token refresh) is handled by an `McpToolProvider(ToolProvider)` subclass — the base abstraction doesn't know about MCP.
3. **Discoverability.** `list_providers()` and `provider.tools()` let the UI enumerate what's available. `provider.health_check()` lets the UI show a "connection OK / auth required / server down" indicator.
4. **Testability.** Mocking a whole provider is trivial; mocking individual tools is trivial. Each provider has its own test file.

### 8.2 Core abstractions (`src/tools/base.py`)

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal
from pydantic import BaseModel
from langchain_core.tools import BaseTool

# ─── Auth declarations ─────────────────────────────────────────

@dataclass(frozen=True, kw_only=True)
class AuthRequirement:
    """Base: a provider declares what kind of auth it needs."""
    required: bool = True

@dataclass(frozen=True, kw_only=True)
class NoAuth(AuthRequirement):
    required: bool = False

@dataclass(frozen=True, kw_only=True)
class ApiKeyAuth(AuthRequirement):
    """Single API key lives in settings (centrally maintained, per memory)."""
    env_var: str   # e.g. "TAVILY_API_KEY"
    settings_field: str  # e.g. "tavily_api_key" (name on the Settings class)

@dataclass(frozen=True, kw_only=True)
class OAuthAuth(AuthRequirement):
    """Per-user OAuth (Phase 3 MCP). Envelope here; details in Phase 3 spec."""
    authorize_url: str
    token_url: str
    scopes: list[str] = field(default_factory=list)
    include_rfc8707_resource: bool = True  # Lesson 1 of the six MCP fixes

# ─── Tool descriptor ───────────────────────────────────────────

@dataclass(frozen=True, kw_only=True)
class ToolDefinition:
    """Enumerable metadata about a single tool offered by a provider."""
    name: str                        # e.g. "tavily_search"
    description: str
    args_schema: type[BaseModel]     # Pydantic input schema (LLM sees this)

# ─── Build context ─────────────────────────────────────────────

@dataclass(kw_only=True)
class BuildContext:
    """Everything a provider might need to build a tool for a specific invocation."""
    node: "AgentNode"                # the node requesting the tool
    state: "WorkflowStateDict"       # current workflow state
    user_id: str | None              # for per-user OAuth in Phase 3+

# ─── Health status ─────────────────────────────────────────────

@dataclass(frozen=True, kw_only=True)
class HealthStatus:
    ok: bool
    message: str                     # user-visible status line

# ─── Provider ABC ──────────────────────────────────────────────

class ToolProvider(ABC):
    """A service offering one-or-more tools for LLM consumption.

    Standard tools (Tavily, Serper, …) are providers. MCP servers are
    also providers, via the McpToolProvider subclass that lands in
    Phase 3. Register at module import time via @register_tool_provider.
    """

    name: str                         # unique slug, "tavily", "highspot-mcp"
    description: str                  # human-readable, shown in UI
    category: Literal["standard", "mcp"]  # set by ABC leaf; drives UI grouping
    auth: AuthRequirement

    @abstractmethod
    async def tools(self) -> list[ToolDefinition]:
        """Enumerate offered tools.

        Standard: typically a static list.
        MCP: a `tools/list` JSON-RPC call to the server (async, cached).
        """

    @abstractmethod
    async def build_tool(self, tool_name: str, context: BuildContext) -> BaseTool:
        """Construct a LangChain BaseTool ready for chat_model.bind_tools()."""

    async def health_check(self) -> HealthStatus:
        """Default impl: verify the auth credential is present. Override to
        do a real ping (HTTP GET to /health, MCP initialize, etc.)."""
        if isinstance(self.auth, ApiKeyAuth):
            ok = bool(getattr(get_settings(), self.auth.settings_field, ""))
            return HealthStatus(
                ok=ok,
                message=f"{self.auth.env_var} {'present' if ok else 'missing'} in settings",
            )
        return HealthStatus(ok=True, message="no auth required")
```

### 8.3 Registry (`src/tools/registry.py`)

```python
from typing import TypeVar
from src.tools.base import ToolProvider, BuildContext

T = TypeVar("T", bound=type[ToolProvider])
_PROVIDERS: dict[str, ToolProvider] = {}

def register_tool_provider(cls: T) -> T:
    """Class decorator — instantiates and stores the provider."""
    instance = cls()
    if instance.name in _PROVIDERS:
        raise ValueError(f"Duplicate provider name: {instance.name!r}")
    _PROVIDERS[instance.name] = instance
    return cls

def get_provider(name: str) -> ToolProvider:
    try:
        return _PROVIDERS[name]
    except KeyError as exc:
        raise UnknownProviderError(
            f"No provider registered for {name!r}. "
            f"Registered: {sorted(_PROVIDERS)}"
        ) from exc

def list_providers(*, category: Literal["standard", "mcp"] | None = None) -> list[ToolProvider]:
    out = list(_PROVIDERS.values())
    if category is not None:
        out = [p for p in out if p.category == category]
    return sorted(out, key=lambda p: p.name)

async def resolve_tools_for_node(node: AgentNode, context: BuildContext) -> list[BaseTool]:
    """Given an Agent node's config, return a list of BaseTool instances
    ready for chat_model.bind_tools()."""
    out: list[BaseTool] = []

    # Standard tools from selectedTools (a list of provider.tool_name, e.g. "tavily_search")
    # Phase 2 convention: selectedTools carries fully-qualified names, one provider per name.
    for qualified_name in node.data.selected_tools:
        provider_name, tool_name = _split_qualified(qualified_name)
        provider = get_provider(provider_name)
        out.append(await provider.build_tool(tool_name, context))

    # MCP tools from mcpServerIds (Phase 3 — Phase 2 leaves this path empty)
    for mcp_id in node.data.mcp_server_ids:
        # Phase 3: look up the McpToolProvider registered under this ID,
        # call provider.tools(), build each.
        raise NotImplementedError(f"MCP tool resolution lands in Phase 3 — server_id={mcp_id!r}")

    return out

class UnknownProviderError(ValueError): ...
class UnknownToolError(ValueError): ...
```

### 8.4 Auto-registration on package import

`src/tools/__init__.py` imports `src/tools/providers/__init__.py` which imports each provider module. The import triggers the `@register_tool_provider` decorator side-effect. Phase 3 adds `src/mcp/providers/__init__.py` that does the same for MCP providers.

This pattern — "import for side effects" — is ugly by default but fine here because (a) it's a well-known Python convention, (b) the import graph is tiny, (c) there's a single entry point. If we outgrow it in Phase 10+ (external plugin developers), we switch to `importlib.metadata` entry points — a non-breaking change because provider authors only see the `ToolProvider` ABC and the decorator.

### 8.5 Phase 2 registrations

- `src/tools/providers/tavily.py` registers `TavilyProvider`.
- MCP path in `resolve_tools_for_node` raises `NotImplementedError` with a Phase 3 hint. The graph_builder's workflow-level validator continues to accept Agent nodes with empty `mcp_server_ids` in Phase 2; non-empty fails fast at execution time.

### 8.6 What Phase 3 adds (for context)

`src/mcp/providers/__init__.py` + `src/mcp/base.py` defines `McpToolProvider(ToolProvider)` that:
- Takes an `McpServer` DB row at `__init__`.
- Implements `async tools()` by calling the MCP server's `tools/list` JSON-RPC.
- Implements `async build_tool()` by wrapping each MCP tool in a LangChain `BaseTool` subclass that calls the MCP server's `tools/call` with the user's OAuth token threaded through `BuildContext.user_id`.
- Implements `async health_check()` by doing a real MCP `initialize` + `tools/list` round-trip.

Phase 3's scope is then "implement `McpToolProvider` + OAuth flow + Prisma tables for `McpServer` / `McpOAuthToken` / `McpOAuthState`" — not "rewrite the Agent executor's tool handling".

---

## 9. `src/tools/providers/tavily.py` — `TavilyProvider` (the Phase 2 reference provider)

### 9.1 Provider

```python
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field
import httpx

from src.config import get_settings
from src.tools.base import (
    ApiKeyAuth, BuildContext, HealthStatus, ToolDefinition, ToolProvider,
)
from src.tools.registry import register_tool_provider


class TavilySearchInput(BaseModel):
    query: str = Field(description="Web search query")
    max_results: int = Field(default=5, ge=1, le=20)


class _TavilySearchTool(BaseTool):
    name: str = "tavily_search"
    description: str = (
        "Search the web via Tavily. Use for current events, fact lookups, and "
        "questions that need fresh information."
    )
    args_schema: type[BaseModel] = TavilySearchInput

    async def _arun(self, query: str, max_results: int = 5) -> str: ...


@register_tool_provider
class TavilyProvider(ToolProvider):
    name = "tavily"
    description = "Tavily web search — general-purpose web search with content extraction."
    category = "standard"
    auth = ApiKeyAuth(env_var="TAVILY_API_KEY", settings_field="tavily_api_key")

    async def tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="tavily_search",
                description=_TavilySearchTool.description,
                args_schema=TavilySearchInput,
            ),
        ]

    async def build_tool(self, tool_name: str, context: BuildContext) -> BaseTool:
        if tool_name != "tavily_search":
            raise UnknownToolError(f"TavilyProvider has no tool named {tool_name!r}")
        if not get_settings().tavily_api_key:
            raise MissingApiKeyError("TAVILY_API_KEY not configured")
        return _TavilySearchTool()

    async def health_check(self) -> HealthStatus:
        # Override default to actually ping Tavily (optional — the default
        # settings-presence check is a reasonable fallback).
        key = get_settings().tavily_api_key
        if not key:
            return HealthStatus(ok=False, message="TAVILY_API_KEY missing")
        async with httpx.AsyncClient(timeout=5.0) as client:
            try:
                resp = await client.post(
                    "https://api.tavily.com/search",
                    json={"api_key": key, "query": "ping", "max_results": 1, "search_depth": "basic"},
                )
                if resp.status_code == 200:
                    return HealthStatus(ok=True, message="Tavily reachable + key valid")
                return HealthStatus(ok=False, message=f"Tavily returned {resp.status_code}: {resp.text[:200]}")
            except httpx.HTTPError as exc:
                return HealthStatus(ok=False, message=f"Tavily unreachable: {exc}")
```

### 9.2 API call (inside `_TavilySearchTool._arun`)

`POST https://api.tavily.com/search` with body:
```json
{
  "api_key": "<settings.tavily_api_key>",
  "query": "<user query>",
  "max_results": 5,
  "search_depth": "basic"
}
```
Response shape: `{"results": [{"title", "url", "content", "score"}, ...]}`. Return a markdown-formatted string of top results.

### 9.3 Error handling

- If `settings.tavily_api_key == ""`, `build_tool` raises `MissingApiKeyError` (caught by Agent executor → reported on node_result).
- 4xx/5xx from Tavily in `_arun` → return a string starting with `"Error: Tavily search failed (HTTP <status>): <short message>"`. Tool errors are routed back to the agent loop so the LLM can recover (same as OAB's pattern); they are not propagated as Python exceptions.
- Timeout (httpx default 30s) → same pattern: return `"Error: Tavily search timed out after 30s"`.

### 9.4 What this template shows for Phase 6 providers

Serper, Firecrawl, Browserless, Gamma, and Arcade each land as one file under `src/tools/providers/` following this template. Changes per provider: the class name, the `name/description/auth` fields, and the `_*Tool._arun` body. Registry auto-discovers them when `src/tools/providers/__init__.py` imports the module.

---

## 10. `src/executors/agent.py` — the Agent executor

### 10.1 Structure

```python
@register_executor("agent")
class AgentExecutor:
    def __init__(self, node: AgentNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        # 1. Substitute variables in instructions
        raw_instructions = self.node.data.instructions or ""
        instructions = substitute(raw_instructions, state)

        # 2. Build messages (system/user/chat-history)
        messages = self._build_messages(instructions, state)

        # 3. Build chat model
        chat_model = build_chat_model(
            self.node.data.model or f"anthropic/{DEFAULT_MODELS['anthropic']}",
            token_limit=self.node.data.token_limit,
        )

        # 4. Build tools via the Tool Provider Framework (§8)
        tools = await resolve_tools_for_node(
            self.node,
            BuildContext(node=self.node, state=state, user_id=state.get("user_id")),
        )

        # 5. Run the agentic loop
        result = await self._agentic_loop(chat_model, messages, tools)

        # 6. Parse output per outputFormat
        output = await self._format_output(chat_model, result.final_messages, result.final_text)

        # 7. Return state delta
        return {
            "variables": {"lastOutput": output},
            "chat_history": result.chat_updates,
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "input": raw_instructions,
                    "output": output,
                    "usage": result.usage,
                }
            },
        }
```

### 10.2 The agentic loop

```python
async def _agentic_loop(
    self,
    chat_model: BaseChatModel,
    messages: list[BaseMessage],
    tools: list[BaseTool],
) -> LoopResult:
    bound_model = chat_model.bind_tools(tools) if tools else chat_model
    accumulated_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    iterations = 0
    current_messages = list(messages)

    while iterations < MAX_ITERATIONS:  # 10, matching OAB
        response = await bound_model.ainvoke(current_messages)
        accumulated_usage = _accumulate_usage(accumulated_usage, response)

        tool_calls = getattr(response, "tool_calls", [])
        if not tool_calls:
            # No tool calls → break with final text
            return LoopResult(
                final_text=str(response.content),
                final_messages=current_messages,
                chat_updates=[
                    ChatMessage(role="user", content=messages[-1].content),  # last user msg
                    ChatMessage(role="assistant", content=str(response.content)),
                ] if self.node.data.include_chat_history else [],
                usage=accumulated_usage,
            )

        # Execute tool calls in parallel
        tool_results = await asyncio.gather(*[
            _execute_tool(tools, tc) for tc in tool_calls
        ], return_exceptions=False)

        current_messages = [*current_messages, response, *tool_results]
        iterations += 1

    # MAX_ITERATIONS hit: return whatever we have
    raise MaxIterationsExceededError(
        f"Agent {self.node.id} hit MAX_ITERATIONS={MAX_ITERATIONS} without producing a final response."
    )
```

### 10.3 Message construction (§10 step 2)

Matches OAB `agent.ts:176–181`. If `include_chat_history` is true AND `state.chat_history` is non-empty, prepend those messages (as `HumanMessage` / `AIMessage`). Otherwise just a `HumanMessage(content=instructions)`. No system prompt in Phase 2 (OAB's Agent doesn't use one; system prompts are Phase 3 work when structured agent personas land).

### 10.4 Output formatting (§10 step 6)

Delegates to `structured_invoke` from `src/llm/structured_output.py`:

- `outputFormat == "Text"` or not set: return `result.final_text` as-is.
- `outputFormat == "JSON"` + `jsonSchema` provided: re-invoke `bound_model.with_structured_output(jsonSchema)` on the last user message (NOT the full history — structured output + tool calls don't compose well; re-ask with the agent's gathered context embedded in the user message).
- `outputFormat == "JSON"` + no `jsonSchema`: same pattern with `method="json_mode"`.

**Alternative considered:** wrap the entire agentic loop with `with_structured_output`. Rejected because structured output on intermediate tool-call turns produces garbage and the extra LLM call on the final turn is worth the reliability.

---

## 11. Tests

### 11.1 Unit tests — per-provider via `FakeListChatModel`

```python
from langchain_core.language_models.fake_chat_models import FakeListChatModel

async def test_agent_anthropic_text_output(monkeypatch) -> None:
    fake = FakeListChatModel(responses=["Hello, world!"])
    monkeypatch.setattr(providers, "build_chat_model", lambda **kw: fake)

    node = AgentNode.model_validate({...})
    state = initial_state("who are you?")
    delta = await AgentExecutor(node).arun(state)
    assert delta["variables"]["lastOutput"] == "Hello, world!"
```

Parametrized over 4 providers + 3 output formats (Text, JSON-no-schema, JSON-with-schema). Unit tests fully offline.

### 11.2 Tool binding — unit test with mocked Tavily

```python
async def test_agent_with_tavily_tool_mocked(httpx_mock) -> None:
    httpx_mock.add_response(url="https://api.tavily.com/search", json={"results": [...]})
    node = AgentNode.model_validate({..., "data": {..., "selectedTools": ["tavily"]}})
    # FakeListChatModel that returns a tool_call, then a text response
    ...
```

### 11.3 Integration tests — real providers

```python
# tests/integration/test_agent_providers.py
pytestmark = pytest.mark.integration

@pytest.mark.parametrize("provider,default_model", [
    ("anthropic", "claude-3-5-haiku-latest"),
    ("openai", "gpt-5-nano"),
    ("google", "gemini-2.0-flash"),
    ("groq", "llama-3.3-70b-versatile"),
])
async def test_agent_start_end_per_provider(client, provider, default_model) -> None:
    # Skip providers whose key is empty
    key_name = f"{provider.upper()}_API_KEY"
    if not os.environ.get(key_name):
        pytest.skip(f"{key_name} not set")
    # POST workflow, POST execution, poll, assert status=completed
    ...
```

Default models per provider are the cheapest one that supports tool calling; doesn't commit us to them but gives the tests a sensible default.

### 11.4 Variable substitution tests

A dozen cases covering every grammar branch: flat key, dotted path, `state.variables.x.y`, `state.nodeResults.n.x`, unresolved → literal, prototype-pollution blocked.

### 11.5 OAB regression test to port

From OAB `tests/executors.spec.ts` or `tests/model-regression.spec.ts`, port the smallest Agent-with-text-output test that doesn't involve MCP. Adapt assertions for the two deliberate deviations (structured output method; MAX_ITERATIONS=10 fixed in Composer).

---

## 12. CI + env setup

CI workflow gets three additions (Task 15 in the Phase 2 plan):
1. Env vars for `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY`, `GROQ_API_KEY`, `TAVILY_API_KEY` (pulled from GitHub Actions secrets).
2. No test-DB changes (same as Phase 1).
3. Integration job runs `pytest -m integration` which now includes the per-provider matrix — each provider's test skips cleanly if the secret isn't set.

For local dev: `.env.example` (already updated in this session) documents each key.

---

## 13. Sequencing

14 tasks, one commit each (same pattern as Phase 1):

1. Phase 2 spec + ADRs 0006-0008 (this commit set)
2. `src/variable_substitution.py` (+ tests)
3. `src/llm/providers.py` (+ tests)
4. `src/llm/structured_output.py` (+ tests)
5. `src/tools/base.py` (ToolRegistry protocol + tests)
6. `src/tools/tavily.py` (+ tests with mocked httpx)
7. `src/executors/agent.py` — message construction + non-tool path (+ tests, 1 provider)
8. `src/executors/agent.py` — agentic loop (with tools) (+ tests)
9. `src/executors/agent.py` — structured output integration (+ tests)
10. Wire AgentExecutor registration into `src/executors/__init__.py` + update `src/engine/graph_builder.py` to no longer raise for `agent` nodes
11. Unit-test matrix across all 4 providers (mocked)
12. Integration tests: per-provider real-API (skip on missing key)
13. Integration test: Start→Agent(with Tavily)→End
14. Port one OAB Agent regression test
15. CI workflow: add provider API key secrets, wire into integration job
16. Phase-exit: CHANGELOG + CLAUDE.md phase table + ADR backfills

Estimate: 5–7 working days total. Real-provider integration tests are the longest single item (~1 day).

---

## 14. Risks

| Risk | Mitigation |
|---|---|
| LangChain API shift between 0.3.x releases | Phase 2 pins LangChain minor version in `pyproject.toml` follow-up if needed |
| Provider keys blocked for Composer's GitHub repo / CI | Use GitHub Actions secrets; tests gracefully skip on missing key |
| Tavily free tier (1000 calls/mo) exhausted by integration tests | Tests use `search_depth="basic"`; one integration test call per CI run is 12/month — well under limit |
| `with_structured_output` behavior differs across providers for json_mode fallback | Google fallback explicitly tested; Anthropic + OpenAI + Groq use native json_mode |
| OAB Agent tests assume naive `JSON.parse` fallback | Acceptable deviation per ADR-0008; adapted in ported regression test |

---

## 15. Phase-exit checklist

- [ ] All 11 items from §1 "Phase 2 is done when" green.
- [ ] CHANGELOG.md Phase 2 section: Added, Changed (deviations), Fixed (if any bugs caught during real-provider integration).
- [ ] CLAUDE.md phase status: Phase 2 ✅, Phase 3 ⏭.
- [ ] ADRs 0006-0008 `Implemented by` populated with commit hashes.
- [ ] No `TODO(phase-2)` comments remain.
- [ ] Integration matrix (4 providers + Tavily) green in CI against real keys.
- [ ] One OAB regression test ported and green.
- [ ] `uv run ruff check && ruff format --check && pyright && pytest` all green (unit only; integration gated on keys).
