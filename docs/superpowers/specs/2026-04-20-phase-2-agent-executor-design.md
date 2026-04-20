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
├── tools/               # NEW package
│   ├── __init__.py
│   ├── base.py          # ToolRegistry protocol + registration pattern
│   └── tavily.py        # Tavily web-search tool (the one Phase-2 standard tool)
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
│   │   ├── test_base.py          # NEW: ToolRegistry contract tests
│   │   └── test_tavily.py        # NEW: Tavily tool wrapper with mocked httpx
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

## 8. `src/tools/base.py` — ToolRegistry protocol

### 8.1 Design

The Agent executor needs to know **which LangChain `BaseTool` instances to bind** for a given node. Phase 2 supports `selectedTools` (list of string tool names, OAB convention). Each name resolves through the registry.

```python
from typing import Protocol
from langchain_core.tools import BaseTool

class ToolFactory(Protocol):
    """A callable that produces a BaseTool for a given node + state."""

    name: str

    def build(self, node_data: AgentNodeData, state: WorkflowStateDict) -> BaseTool: ...


_REGISTRY: dict[str, ToolFactory] = {}

def register_tool_factory(factory: ToolFactory) -> None: ...
def get_tools_for_node(node: AgentNode, state: WorkflowStateDict) -> list[BaseTool]: ...
```

### 8.2 Phase 2 registrations

- `tavily` — registered by `src/tools/tavily.py`.
- **Phase 3 MCP resolution hooks here.** When an Agent node has `mcp_server_ids`, Phase 3's `src/mcp/resolver.py` registers dynamically-built `BaseTool` instances. Phase 2 leaves `mcp_server_ids` and `mcp_tools` fields **unused** — the registry's `get_tools_for_node` inspects only `selectedTools`. Phase 3 extends.

### 8.3 Contract

- `selectedTools` contains name strings like `"tavily"`. For each name, `get_tools_for_node` calls the registered factory's `.build(node.data, state)` to produce a `BaseTool`.
- Unknown names raise `UnknownToolError` — the Agent executor catches and records it as a node-execution error (`status: "failed"`).

---

## 9. `src/tools/tavily.py` — Tavily web search tool

### 9.1 Tool behavior

Simple wrapper around Tavily's `/search` endpoint:

```python
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field
import httpx
from src.config import get_settings

class TavilySearchInput(BaseModel):
    query: str = Field(description="Web search query")
    max_results: int = Field(default=5, ge=1, le=20)

class TavilySearchTool(BaseTool):
    name: str = "tavily_search"
    description: str = "Search the web via Tavily. Use for current events and fact-lookup."
    args_schema: type[BaseModel] = TavilySearchInput

    async def _arun(self, query: str, max_results: int = 5) -> str: ...
```

### 9.2 API call

`POST https://api.tavily.com/search` with body `{"api_key": settings.tavily_api_key, "query": ..., "max_results": ..., "search_depth": "basic"}`. Response: `{"results": [{"title", "url", "content", "score"}, ...]}`. Return a markdown-formatted string of top results.

### 9.3 Auth + error handling

- If `settings.tavily_api_key == ""`, raise `MissingApiKeyError` at tool-build time.
- 4xx/5xx from Tavily → raise `ToolExecutionError` with the HTTP status + response body snippet.
- Timeout (httpx default 30s) → raise `ToolExecutionError("Tavily search timed out after 30s")`.

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

        # 4. Build tools from selectedTools
        tools = get_tools_for_node(self.node, state)

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
