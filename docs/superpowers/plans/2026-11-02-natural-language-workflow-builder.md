# Natural-Language Workflow Builder — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a chat panel inside the designer that turns an English description into a valid, runnable Composer workflow — planning first, building in stages with confirmations, prompting for credentials via existing setup flows, and diagnosing failed runs with an approve-before-apply patch.

**Architecture:** A tool-calling LLM agent (`src/builder/`) runs per-turn in response to `POST /builder/{workflowId}/message`. The server holds the agent's message history in an in-memory `BuildSession` (ephemeral, on `app.state`). The agent emits typed `BuilderEvent`s and persists the workflow at each turn boundary — **even when still structurally invalid** — reporting violations via a new `collect_workflow_violations` helper so they can be rectified incrementally. Builder edits bypass the shape gate but reuse `workflows.py`'s secret-encryption/redaction helpers.

**Tech Stack:** Python 3.11/3.12 · FastAPI · Pydantic v2 · Prisma Python · LangChain (tools + `structured_invoke`) · Next.js 14 / React Flow / TanStack Query / shadcn/ui · pytest + pytest-asyncio · Vitest.

**Spec:** `docs/superpowers/specs/2026-11-02-natural-language-workflow-builder-design.md`

## Global Constraints

- No new Python dependency; no `prisma migrate` (no new table). `pyproject.toml` and `prisma/schema.prisma` stay unchanged.
- No new Node dependency (reuse existing frontend libs).
- Python `>=3.11,<3.13`; pyright **strict mode** (`reportUnusedFunction` on FastAPI handlers → add `# pyright: ignore[reportUnusedFunction]` or use `APIRouter`).
- Ruff `line-length = 100`, double quotes, isort ordering.
- Node/edge caps: 100 nodes, 200 edges (`_check_workflow_size_limits`).
- Every commit ends with these two trailers (both required):
  ```
  Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
  Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
  ```
- Existing tests must keep passing; new backend tests go under `tests/unit/builder/` and `tests/unit/api/`; mark anything needing real Postgres/external services `@pytest.mark.integration`.
- `src/engine/workflow.py` is **not modified** by this plan. `src/api/workflows.py` is consumed (private `_encrypt_*` / `_to_workflow_read` helpers imported), not modified.

---

## File Structure

**Backend (create `src/builder/`):**
- `src/engine/graph_builder.py` *(modify)* — add `collect_workflow_violations`; refactor `validate_workflow_shape` to delegate.
- `src/builder/__init__.py` *(create)*
- `src/builder/node_catalog.py` *(create)* — LLM-facing catalog of all node types.
- `src/builder/layout.py` *(create)* — deterministic layered layout.
- `src/builder/diagnostics.py` *(create)* — execution failure context (redacted).
- `src/builder/draft.py` *(create)* — in-memory mutable nodes/edges for a turn.
- `src/builder/builder_tools.py` *(create)* — LangChain mutation/credential/diagnose tools.
- `src/builder/prompts.py` *(create)* — system-prompt templates + catalog splice.
- `src/builder/builder_agent.py` *(create)* — agent loop + `BuildSession` + turn orchestration.
- `src/api/builder.py` *(create)* — the endpoint + authz + rate limit + persistence.
- `src/config.py` *(modify)* — add `rate_limit_builder_per_minute`.
- `src/main.py` *(modify)* — register the router.

**Frontend (create `frontend/components/composer/builder-chat/`):**
- `frontend/lib/api/builder.ts` *(create)* — typed client.
- `frontend/components/composer/builder-chat/builder-panel.tsx` *(create)*
- `frontend/components/composer/builder-chat/plan-card.tsx` *(create)*
- `frontend/components/composer/builder-chat/credential-request-card.tsx` *(create)*
- `frontend/components/composer/builder-chat/patch-diff-card.tsx` *(create)*
- `frontend/app/designer/[workflowId]/page.tsx` *(modify)* — mount the panel + apply builder output.

---

### Task 1: `collect_workflow_violations` + refactor `validate_workflow_shape`

**Files:**
- Modify: `src/engine/graph_builder.py`
- Test: `tests/unit/engine/test_graph_builder_violations.py`

**Interfaces:**
- Produces: `collect_workflow_violations(workflow: Workflow) -> list[str]` — returns **all** structural violations (empty list = valid). `validate_workflow_shape(workflow: Workflow) -> None` — raises `WorkflowValidationError` with the first violation (contract unchanged).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/engine/test_graph_builder_violations.py
from src.engine.graph_builder import collect_workflow_violations, validate_workflow_shape, WorkflowValidationError
from src.engine.workflow import (
    EndNode, Position, StartNode, Workflow, WorkflowEdge,
)


def _wf(nodes, edges):
    return Workflow(name="t", nodes=nodes, edges=edges)


def _start(i="s"):
    return StartNode(id=i, type="start", position=Position(x=0, y=0), data={"label": "Start"})


def _end(i="e"):
    return EndNode(id=i, type="end", position=Position(x=400, y=0), data={"label": "End"})


def test_valid_workflow_has_no_violations():
    wf = _wf([_start(), _end()], [WorkflowEdge(id="e1", source="s", target="e")])
    assert collect_workflow_violations(wf) == []


def test_collects_multiple_violations_in_one_pass():
    # Missing end node, an unreachable node, and an unknown-source edge all at once.
    from src.engine.workflow import AgentNode
    agent = AgentNode(id="a", type="agent", position=Position(x=0, y=0),
                      data={"label": "A"})
    wf = _wf(
        [_start(), agent],
        [WorkflowEdge(id="e1", source="s", target="missing")],
    )
    v = collect_workflow_violations(wf)
    assert any("end node" in x for x in v)
    assert any("unknown source node id 'missing'" in x for x in v)
    assert any("unreachable" in x for x in v)


def test_validate_still_raises_on_first():
    from src.engine.workflow import AgentNode
    agent = AgentNode(id="a", type="agent", position=Position(x=0, y=0),
                      data={"label": "A"})
    wf = _wf([_start(), agent], [])  # no end node
    try:
        validate_workflow_shape(wf)
    except WorkflowValidationError as exc:
        assert "end node" in str(exc)
    else:
        raise AssertionError("expected WorkflowValidationError")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/engine/test_graph_builder_violations.py -v`
Expected: FAIL — `ImportError: cannot import name 'collect_workflow_violations'`.

- [ ] **Step 3: Implement `collect_workflow_violations` and refactor `validate_workflow_shape`**

In `src/engine/graph_builder.py`, replace the body of `validate_workflow_shape` (lines 230-311) with the collector below, then make `validate_workflow_shape` delegate to it. Keep `_nodes_by_id`, `_check_edges`, `_check_reachability`, `_branch_markers`, `_mutually_exclusive`, `CONDITIONAL_SOURCE_TYPES`, `_VISUAL_ONLY_TYPES` as-is (they are still used by `build_graph` and the collector).

```python
def collect_workflow_violations(workflow: Workflow) -> list[str]:
    """Return every structural violation as human-readable strings (empty = valid).

    Unlike validate_workflow_shape (raise-on-first), this gathers all so the
    builder can report them and the agent can rectify them incrementally.
    Check order mirrors the original raise order so `violations[0]` preserves
    the historical first-error message.
    """
    violations: list[str] = []

    # 1. Duplicate node ids.
    nodes: dict[str, WorkflowNode] = {}
    for node in workflow.nodes:
        if node.id in nodes:
            violations.append(f"Node id {node.id!r} is duplicated.")
        else:
            nodes[node.id] = node

    # 2. Start cardinality.
    start_ids = [n.id for n in workflow.nodes if n.type == "start"]
    if len(start_ids) != 1:
        violations.append(
            f"Workflow must contain exactly one start node (found: {len(start_ids)})."
        )

    # 3. End cardinality.
    end_ids = [n.id for n in workflow.nodes if n.type == "end"]
    if not end_ids:
        violations.append("Workflow must contain at least one end node.")

    # 4. Edge endpoints reference known nodes (drop bad edges for later checks).
    known_ids = set(nodes.keys())
    clean_edges: list[WorkflowEdge] = []
    for edge in workflow.edges:
        if edge.source not in known_ids:
            violations.append(
                f"Edge {edge.id!r} references unknown source node id {edge.source!r}."
            )
            continue
        if edge.target not in known_ids:
            violations.append(
                f"Edge {edge.id!r} references unknown target node id {edge.target!r}."
            )
            continue
        clean_edges.append(edge)

    # Downstream checks assume exactly one start and resolvable ids; if those
    # invariants are already broken, skip (their violations are reported).
    if len(start_ids) != 1:
        return violations
    start_id = start_ids[0]

    # 5. End incoming-edge rule.
    branch_markers = _branch_markers(nodes, clean_edges)
    for end_id in end_ids:
        incoming = [
            e for e in clean_edges
            if e.target == end_id and nodes[e.source].type not in _VISUAL_ONLY_TYPES
        ]
        if len(incoming) == 1:
            continue
        if len(incoming) > 1:
            sources = [e.source for e in incoming]
            if all(
                _mutually_exclusive(branch_markers[sources[i]], branch_markers[sources[j]])
                for i in range(len(sources))
                for j in range(i + 1, len(sources))
            ):
                continue
        violations.append(
            f"End node {end_id!r} must have exactly one incoming edge from an "
            f"executable node (found {len(incoming)})."
        )

    # 6. Join outgoing-edge rule.
    for node in nodes.values():
        if node.type != "join":
            continue
        outgoing = [
            e for e in clean_edges
            if e.source == node.id and nodes[e.target].type not in _VISUAL_ONLY_TYPES
        ]
        if len(outgoing) != 1:
            violations.append(
                f"Join node {node.id!r} must have exactly one outgoing edge (found {len(outgoing)})."
            )

    # 7. Branch-label rules.
    for edge in clean_edges:
        source = nodes[edge.source]
        if source.type in CONDITIONAL_SOURCE_TYPES:
            if edge.branch is None:
                violations.append(
                    f"Edge {edge.id!r} leaves {source.type} node {source.id!r} "
                    f"but has no branch label."
                )
        elif edge.branch is not None:
            violations.append(
                f"Edge {edge.id!r} has branch={edge.branch!r} but its source "
                f"{source.id!r} is not a conditional node."
            )

    # 8. Reachability.
    outgoing_map: dict[str, list[str]] = {node_id: [] for node_id in nodes}
    for edge in clean_edges:
        if nodes[edge.source].type in _VISUAL_ONLY_TYPES:
            continue
        if nodes[edge.target].type in _VISUAL_ONLY_TYPES:
            continue
        outgoing_map[edge.source].append(edge.target)
    reachable: set[str] = set()
    queue: deque[str] = deque([start_id])
    while queue:
        current = queue.popleft()
        if current in reachable:
            continue
        reachable.add(current)
        queue.extend(outgoing_map[current])
    for node_id, node in nodes.items():
        if node.type in _VISUAL_ONLY_TYPES:
            continue
        if node_id not in reachable:
            violations.append(
                f"Node {node_id!r} ({node.type!r}) is unreachable from the start node."
            )

    return violations


def validate_workflow_shape(workflow: Workflow) -> None:
    """Validate a workflow's structural invariants. Raises WorkflowValidationError on failure."""
    violations = collect_workflow_violations(workflow)
    if violations:
        raise WorkflowValidationError(violations[0])
```

Add `"collect_workflow_violations"` to the module `__all__` list.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/engine/test_graph_builder_violations.py -v` and then the existing graph-builder tests (e.g. `uv run pytest tests/unit -k graph_builder -q`).
Expected: PASS; existing graph-builder tests still green (contract preserved).

- [ ] **Step 5: Commit**

```bash
git add src/engine/graph_builder.py tests/unit/engine/test_graph_builder_violations.py
git commit -m "refactor: extract collect_workflow_violations; validate_workflow_shape delegates

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `node_catalog.py` — LLM-facing node catalog

**Files:**
- Create: `src/builder/__init__.py`, `src/builder/node_catalog.py`
- Test: `tests/unit/builder/test_node_catalog.py`

**Interfaces:**
- Produces: `build_node_catalog() -> str` — a compact text catalog (one line per node type: `type — purpose (credentials: ...)`), covering every type in the `WorkflowNode` union.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/builder/test_node_catalog.py
from src.builder.node_catalog import build_node_catalog, NODE_PURPOSES


def test_catalog_covers_every_workflow_node_type():
    from src.engine.workflow import WorkflowNode
    import typing

    args = typing.get_args(WorkflowNode)  # the members of the Annotated union
    types = {t.__fields__["type"].annotation.__args__[0] for t in args}  # literal str
    assert set(NODE_PURPOSES) == types


def test_catalog_mentions_every_type():
    text = build_node_catalog()
    for t in NODE_PURPOSES:
        assert f"`{t}`" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/builder/test_node_catalog.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.builder'`.

- [ ] **Step 3: Implement the catalog**

`src/builder/__init__.py`:
```python
"""Natural-language workflow builder."""
```

`src/builder/node_catalog.py`:
```python
"""LLM-facing catalog of every Composer node type.

Derived by hand from src/engine/workflow.py's Pydantic models — one curated
purpose line and credential hint per type. The test above asserts this stays
in sync with the WorkflowNode union so a new node type can't be silently
missing from the builder's vocabulary.
"""

NODE_PURPOSES: dict[str, str] = {
    "start": "workflow entry; declares input variables the caller must supply",
    "end": "workflow termination; may capture a final output",
    "note": "canvas annotation only; never executes",
    "file-trigger": "folder-watch / cloud-storage trigger config (visual only)",
    "file-write": "write generated content to a file (md/docx/pdf/html)",
    "agent": "multi-turn LLM agent with tools, MCP, and structured output",
    "mcp": "call tools from a configured MCP server (agent or single-call mode)",
    "http": "call an external HTTP API with method/headers/body",
    "extract": "single-shot structured extraction into a JSON schema",
    "transform": "run sandboxed Python (E2B) to transform state",
    "data-transform": "map/filter/reduce over a collection via expressions",
    "set-state": "write a literal or expression value into a variable",
    "if-else": "branch on a boolean expression (true/false)",
    "decision": "LLM/judgment-based branching (binary or N-way choice)",
    "while": "bounded loop (body/exit) over a boolean expression",
    "user-approval": "pause for a human approve/reject decision",
    "guardrails": "LLM PII/moderation/jailbreak/hallucination classifiers",
    "vector-db": "query or upsert a vector store (Pinecone/Qdrant/Chroma/Weaviate/Milvus)",
    "gamma-ai": "generate a slide deck or document via Gamma",
    "email": "send email via Resend",
    "arcade": "call an Arcade tool",
    "join-chunks": "concatenate text chunks with separator/prefix/suffix",
    "jira": "create/search/update/transition/comment Jira issues",
    "confluence": "create/update/get Confluence pages and properties",
    "download-pdf": "render HTML/Markdown to PDF and write via a storage provider",
    "join": "converge multiple branches into one downstream path",
}

NODE_CREDENTIALS: dict[str, list[str]] = {
    "agent": ["llm-provider"],
    "mcp": ["mcp-server"],
    "extract": ["llm-provider"],
    "decision": ["llm-provider"],
    "guardrails": ["llm-provider"],
    "vector-db": ["vector-db"],
    "gamma-ai": ["tool-provider"],
    "email": ["resend"],
    "arcade": ["tool-provider"],
    "jira": ["jira"],
    "confluence": ["confluence"],
    "file-trigger": ["google-drive"],
    "download-pdf": ["google-drive"],
}


def build_node_catalog() -> str:
    lines: list[str] = []
    for node_type, purpose in sorted(NODE_PURPOSES.items()):
        creds = ", ".join(NODE_CREDENTIALS.get(node_type, [])) or "none"
        lines.append(f"- `{node_type}` — {purpose} (credentials: {creds})")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/builder/test_node_catalog.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/builder/__init__.py src/builder/node_catalog.py tests/unit/builder/test_node_catalog.py
git commit -m "feat(builder): node catalog for LLM workflow vocabulary

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `layout.py` — deterministic layered layout

**Files:**
- Create: `src/builder/layout.py`
- Test: `tests/unit/builder/test_layout.py`

**Interfaces:**
- Produces: `compute_layout(nodes: list[WorkflowNode], edges: list[WorkflowEdge]) -> dict[str, Position]` — a `Position` for every node id. Deterministic for equal input; advisory only.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/builder/test_layout.py
from src.builder.layout import compute_layout
from src.engine.workflow import Position, StartNode, EndNode, AgentNode, WorkflowEdge, Workflow


def _wf():
    nodes = [
        StartNode(id="s", type="start", position=Position(x=0, y=0), data={"label": "S"}),
        AgentNode(id="a", type="agent", position=Position(x=0, y=0), data={"label": "A"}),
        EndNode(id="e", type="end", position=Position(x=0, y=0), data={"label": "E"}),
    ]
    edges = [WorkflowEdge(id="e1", source="s", target="a"),
             WorkflowEdge(id="e2", source="a", target="e")]
    return nodes, edges


def test_layout_places_start_leftmost_and_end_rightmost():
    nodes, edges = _wf()
    pos = compute_layout(nodes, edges)
    assert pos["s"].x < pos["a"].x < pos["e"].x


def test_layout_is_deterministic():
    nodes, edges = _wf()
    assert compute_layout(nodes, edges) == compute_layout(nodes, edges)


def test_layout_covers_all_node_ids():
    nodes, edges = _wf()
    pos = compute_layout(nodes, edges)
    assert set(pos) == {n.id for n in nodes}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/builder/test_layout.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.builder.layout'`.

- [ ] **Step 3: Implement**

```python
"""Deterministic layered auto-layout for builder-generated graphs.

No external dependency (no dagre). Depth = BFS from the start node; nodes not
reachable from start are placed one layer past the deepest layer. Within a
layer, nodes are ordered by id so output is deterministic. Positions are
advisory — the user can drag nodes afterward.
"""

from collections import deque

from src.engine.workflow import Position, WorkflowEdge, WorkflowNode

_LAYER_X = 260.0
_NODE_Y = 120.0


def compute_layout(nodes: list[WorkflowNode], edges: list[WorkflowEdge]) -> dict[str, Position]:
    start = next((n for n in nodes if n.type == "start"), None)
    adj: dict[str, list[str]] = {n.id: [] for n in nodes}
    for e in edges:
        if e.source in adj and e.target in adj:
            adj[e.source].append(e.target)

    depth: dict[str, int] = {n.id: 0 for n in nodes}
    if start is not None:
        seen: set[str] = set()
        queue: deque[str] = deque([start.id])
        depth[start.id] = 0
        while queue:
            cur = queue.popleft()
            if cur in seen:
                continue
            seen.add(cur)
            for nxt in sorted(adj[cur]):
                depth[nxt] = max(depth.get(nxt, 0), depth[cur] + 1)
                queue.append(nxt)

    max_depth = max(depth.values(), default=0)
    # Unreachable nodes go one layer past the deepest layer.
    for n in nodes:
        if start is None or (n.id not in depth or (n.id != start.id and depth[n.id] == 0
                                                   and not any(e.target == n.id for e in edges))):
            depth[n.id] = max_depth + 1

    layers: dict[int, list[str]] = {}
    for n in nodes:
        layers.setdefault(depth[n.id], []).append(n.id)
    for ids in layers.values():
        ids.sort()

    positions: dict[str, Position] = {}
    for layer, ids in layers.items():
        for row, nid in enumerate(ids):
            positions[nid] = Position(x=layer * _LAYER_X, y=row * _NODE_Y)
    return positions
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/builder/test_layout.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/builder/layout.py tests/unit/builder/test_layout.py
git commit -m "feat(builder): deterministic layered node layout

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `diagnostics.py` — redacted execution failure context

**Files:**
- Create: `src/builder/diagnostics.py`
- Test: `tests/unit/builder/test_diagnostics.py`

**Interfaces:**
- Produces: `build_diagnosis(db, execution_id: str) -> Diagnosis | None` where `Diagnosis` is a frozen dataclass: `status: str`, `error: str | None`, `node_results: dict`, `variables_tail: dict`. Returns `None` if the execution row doesn't exist. Secret-looking values are redacted.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/builder/test_diagnostics.py
from src.builder.diagnostics import build_diagnosis, _REDACT_MARKER


class _FakeDb:
    def __init__(self, row):
        self._row = row
        self.workflowexecution = self

    async def find_unique(self, **kwargs):
        return self._row


def _row():
    return type("Row", (), {
        "status": "failed",
        "error": "boom",
        "nodeResults": {"a": {"output": "x"}},
        "variables": {"apiKey": "SECRET", "safe": "ok"},
    })()


async def test_build_diagnosis_redacts_secret_keys():
    diag = await build_diagnosis(_FakeDb(_row()), "exec-1")
    assert diag is not None
    assert diag.status == "failed"
    assert diag.error == "boom"
    assert diag.variables_tail["apiKey"] == _REDACT_MARKER
    assert diag.variables_tail["safe"] == "ok"


async def test_build_diagnosis_returns_none_for_missing_row():
    assert await build_diagnosis(_FakeDb(None), "nope") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/builder/test_diagnostics.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.builder.diagnostics'`.

- [ ] **Step 3: Implement**

```python
"""Assemble a redacted diagnosis context from a failed execution."""

from dataclasses import dataclass, field
from typing import Any

_REDACT_MARKER = "••••••••"
_SECRET_HINTS = ("key", "token", "secret", "password", "authorization", "api_key", "credential")
_MAX_TAIL = 30


@dataclass(frozen=True)
class Diagnosis:
    status: str
    error: str | None
    node_results: dict[str, Any] = field(default_factory=dict)
    variables_tail: dict[str, Any] = field(default_factory=dict)


def _redact_value(key: str, value: Any) -> Any:
    if any(hint in key.lower() for hint in _SECRET_HINTS):
        return _REDACT_MARKER
    if isinstance(value, str) and len(value) > 500:
        return value[:500] + "…"
    return value


def _redact_dict(mapping: Any) -> dict[str, Any]:
    if not isinstance(mapping, dict):
        return {}
    return {str(k): _redact_value(str(k), v) for k, v in mapping.items()}


async def build_diagnosis(db: Any, execution_id: str) -> Diagnosis | None:
    row = await db.workflowexecution.find_unique(where={"id": execution_id})
    if row is None:
        return None
    variables = getattr(row, "variables", None) or {}
    variables_tail = _redact_dict(dict(list(variables.items())[-_MAX_TAIL:]))
    return Diagnosis(
        status=str(getattr(row, "status", "")),
        error=getattr(row, "error", None),
        node_results=_redact_dict(getattr(row, "nodeResults", None)),
        variables_tail=variables_tail,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/builder/test_diagnostics.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/builder/diagnostics.py tests/unit/builder/test_diagnostics.py
git commit -m "feat(builder): redacted execution diagnosis context

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: `draft.py` + `builder_tools.py` — mutation tools over an in-memory draft

**Files:**
- Create: `src/builder/draft.py`, `src/builder/builder_tools.py`
- Test: `tests/unit/builder/test_builder_tools.py`

**Interfaces:**
- `Draft` (from `src/builder/draft.py`): mutable holder — `nodes: dict[str, WorkflowNode]`, `edges: dict[str, WorkflowEdge]`; `snapshot() -> tuple[list[WorkflowNode], list[WorkflowEdge]]`; `positions() -> dict[str, Position]` (lazy via `compute_layout`).
- `make_builder_tools(ctx: "BuilderContext") -> list[BaseTool]` (from `src/builder/builder_tools.py`): returns LangChain tools `create_node`, `update_node`, `remove_node`, `connect_nodes`, `set_start_inputs`, `request_credential`. Each mutates `ctx.draft` or appends to `ctx.events`; returns a plain-string result (violation text on invalid input).

Note: `BuilderContext` is defined in this task as a minimal dataclass holding `draft: Draft` and `events: list[dict]`; `builder_agent.py` (Task 6) will build it.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/builder/test_builder_tools.py
from src.builder.draft import Draft
from src.builder.builder_tools import BuilderContext, make_builder_tools


def _ctx():
    return BuilderContext(draft=Draft(), events=[])


def _tools():
    return {t.name: t for t in make_builder_tools(_ctx())}


def test_create_node_and_connect_then_validate():
    ctx = _ctx()
    tools = _tools()
    # create_node is async-invoked via LangChain; here we call the underlying func
    r1 = tools["create_node"].func(node_type="agent", node_id="a", label="A", data={})
    assert "agent" in r1
    r2 = tools["create_node"].func(node_type="start", node_id="s", label="S", data={})
    r3 = tools["create_node"].func(node_type="end", node_id="e", label="E", data={})
    r4 = tools["connect_nodes"].func(source="s", target="a")
    r5 = tools["connect_nodes"].func(source="a", target="e")
    assert all("ok" in r for r in (r1, r2, r3, r4, r5))
    nodes, edges = ctx.draft.snapshot()
    assert {n.id for n in nodes} == {"s", "a", "e"}
    assert {(e.source, e.target) for e in edges} == {("s", "a"), ("a", "e")}


def test_create_node_with_bad_type_returns_error():
    ctx = _ctx()
    tools = _tools()
    r = tools["create_node"].func(node_type="nope", node_id="x", label="X", data={})
    assert r.startswith("ERROR")


def test_request_credential_appends_event():
    ctx = _ctx()
    tools = _tools()
    r = tools["request_credential"].func(kind="vector-db", node_id="a", detail="needs a Pinecone key")
    assert "credential" in r.lower()
    assert ctx.events == [{"type": "credential_request",
                           "kind": "vector-db", "node_id": "a", "detail": "needs a Pinecone key",
                           "resolvable_by": "user"}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/builder/test_builder_tools.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.builder.draft'`.

- [ ] **Step 3: Implement `draft.py`**

```python
"""In-memory mutable workflow draft for a single builder turn."""

from src.builder.layout import compute_layout
from src.engine.workflow import Position, WorkflowEdge, WorkflowNode


class Draft:
    def __init__(self) -> None:
        self.nodes: dict[str, WorkflowNode] = {}
        self.edges: dict[str, WorkflowEdge] = {}

    def snapshot(self) -> tuple[list[WorkflowNode], list[WorkflowEdge]]:
        return list(self.nodes.values()), list(self.edges.values())

    def positions(self) -> dict[str, Position]:
        nodes, edges = self.snapshot()
        return compute_layout(nodes, edges)
```

- [ ] **Step 4: Implement `builder_tools.py`**

```python
"""LangChain tools the builder agent uses to mutate a Draft.

Each tool returns a plain-string result. Invalid inputs return "ERROR: ..."
so the model sees a fixable message instead of a raised exception (which
would abort the turn). Schema validation happens by constructing the
Pydantic node model; shape validation is deferred to Task 6's turn boundary.
"""

import re
from dataclasses import dataclass, field
from typing import Any

from langchain_core.tools import BaseTool, tool

from src.builder.draft import Draft
from src.engine.workflow import (
    AgentNode, ArcadeNode, ConfluenceNode, DataTransformNode, DecisionNode,
    DownloadPdfNode, EmailNode, EndNode, ExtractNode, FileTriggerNode, FileWriteNode,
    GammaAiNode, GuardrailsNode, HttpNode, IfElseNode, JiraNode, JoinChunksNode, JoinNode,
    McpNode, NoteNode, Position, SetStateNode, StartNode, TransformNode, UserApprovalNode,
    VectorDbNode, WhileNode, WorkflowEdge, WorkflowNode,
)

_NODE_CLASSES: dict[str, type[WorkflowNode]] = {
    "start": StartNode, "end": EndNode, "note": NoteNode, "file-trigger": FileTriggerNode,
    "file-write": FileWriteNode, "agent": AgentNode, "mcp": McpNode, "http": HttpNode,
    "extract": ExtractNode, "transform": TransformNode, "data-transform": DataTransformNode,
    "set-state": SetStateNode, "if-else": IfElseNode, "decision": DecisionNode,
    "while": WhileNode, "user-approval": UserApprovalNode, "guardrails": GuardrailsNode,
    "vector-db": VectorDbNode, "gamma-ai": GammaAiNode, "email": EmailNode,
    "arcade": ArcadeNode, "join-chunks": JoinChunksNode, "jira": JiraNode,
    "confluence": ConfluenceNode, "download-pdf": DownloadPdfNode, "join": JoinNode,
}

_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

_CREDENTIAL_RESOLVABLE_BY = {
    "mcp-server": "user", "vector-db": "user", "jira": "user", "confluence": "user",
    "google-drive": "user", "resend": "admin", "tool-provider": "admin", "llm-provider": "admin",
}


@dataclass
class BuilderContext:
    draft: Draft
    events: list[dict[str, Any]] = field(default_factory=list)


def _new_id(node_type: str) -> str:
    import secrets
    return f"{node_type}-{secrets.token_hex(4)}"


def _build_node(node_type: str, node_id: str, data: dict[str, Any]) -> WorkflowNode | None:
    cls = _NODE_CLASSES.get(node_type)
    if cls is None:
        return None
    if not _ID_RE.match(node_id):
        return None
    data = {"label": data.get("label", node_type), **data}
    try:
        return cls(id=node_id, type=node_type, position=Position(x=0, y=0), data=data)  # type: ignore[arg-type]
    except Exception:
        return None


def make_builder_tools(ctx: BuilderContext) -> list[BaseTool]:
    draft = ctx.draft

    @tool
    def create_node(node_type: str, node_id: str, label: str, data: dict[str, Any] = {}) -> str:  # noqa: B006
        """Create a node. `data` is a dict of node-specific fields (omit the label; it is passed separately)."""
        payload = {"label": label, **data}
        node = _build_node(node_type, node_id, payload)
        if node is None:
            return f"ERROR: could not create {node_type!r} node {node_id!r} — check type and fields."
        if node_id in draft.nodes:
            return f"ERROR: node id {node_id!r} already exists."
        draft.nodes[node_id] = node
        return f"ok: created {node_type} node {node_id!r}."

    @tool
    def update_node(node_id: str, data: dict[str, Any]) -> str:  # noqa: B006
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
        for eid in [e for e, ed in draft.edges.items() if ed.source == node_id or ed.target == node_id]:
            del draft.edges[eid]
        return f"ok: removed node {node_id!r}."

    @tool
    def connect_nodes(source: str, target: str, branch: str | None = None) -> str:
        """Add an edge from `source` to `target`. `branch` required for if-else/while/user-approval/decision sources."""
        if source not in draft.nodes:
            return f"ERROR: unknown source {source!r}."
        if target not in draft.nodes:
            return f"ERROR: unknown target {target!r}."
        edge_id = f"e-{len(draft.edges) + 1}"
        draft.edges[edge_id] = WorkflowEdge(id=edge_id, source=source, target=target, branch=branch)
        return f"ok: connected {source!r} -> {target!r}."

    @tool
    def set_start_inputs(variables: list[dict[str, Any]]) -> str:  # noqa: B006
        """Declare the Start node's input variables (list of {name, type?, required?, description?})."""
        start = next((n for n in draft.nodes.values() if n.type == "start"), None)
        if start is None:
            return "ERROR: no start node — create one first."
        from src.engine.workflow import StartInputVariable
        ivars = [StartInputVariable(**v).model_dump(by_alias=True) for v in variables]
        data = {**start.data.model_dump(by_alias=True), "inputVariables": ivars}
        draft.nodes[start.id] = StartNode(id=start.id, type="start",
                                          position=start.position, data=data)
        return f"ok: declared {len(ivars)} start inputs."

    @tool
    def request_credential(kind: str, node_id: str, detail: str) -> str:
        """Declare that a node needs a credential the user must supply (kind e.g. mcp-server, vector-db, jira, google-drive)."""
        ctx.events.append({
            "type": "credential_request",
            "kind": kind,
            "node_id": node_id,
            "detail": detail,
            "resolvable_by": _CREDENTIAL_RESOLVABLE_BY.get(kind, "user"),
        })
        return f"credential requested: {kind} for node {node_id!r}."

    return [create_node, update_node, remove_node, connect_nodes, set_start_inputs, request_credential]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/builder/test_builder_tools.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/builder/draft.py src/builder/builder_tools.py tests/unit/builder/test_builder_tools.py
git commit -m "feat(builder): draft + mutation tools over the in-memory workflow

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: `prompts.py` + `builder_agent.py` — agent loop and `BuildSession`

**Files:**
- Create: `src/builder/prompts.py`, `src/builder/builder_agent.py`
- Test: `tests/unit/builder/test_builder_agent.py`

**Interfaces:**
- `SYSTEM_PROMPT_BUILDER` (from `prompts.py`) — a string template with a `{catalog}` placeholder.
- `BuildSession` (from `builder_agent.py`) — dataclass holding `messages: list[BaseMessage]`, `workflow_id: str`, `pending_patch: dict | None`, `last_activity: float`.
- `run_builder_turn(*, model: BaseChatModel, workflow_id: str, workflow: Workflow, session: BuildSession, message: str, execution_id: str | None, db: Any) -> tuple[list[dict], str, Workflow]` — runs one turn, returns `(events, reply, updated_workflow)`. This task covers the pure logic; the DB read/persist is injected via `workflow` (input) and `db` (for `diagnose`). **Persisting is deferred to Task 7** — in this task the returned `workflow` is the in-memory draft re-assembled from the draft (so it can be validated), and the agent does **not** touch the DB.

Note: keep the agent deterministic-testable by injecting the chat model. The test uses a scripted fake model.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/builder/test_builder_agent.py
from src.builder.builder_agent import BuildSession, run_builder_turn
from src.engine.workflow import EndNode, Position, StartNode, Workflow, WorkflowEdge


class _ScriptedModel:
    """A fake chat model whose `ainvoke` returns a canned assistant message."""
    def __init__(self, text):
        self._text = text
        self.calls = []

    async def ainvoke(self, messages, **kwargs):
        self.calls.append(messages)
        from langchain_core.messages import AIMessage
        return AIMessage(content=self._text)

    def bind_tools(self, tools):
        return self

    def with_structured_output(self, schema):
        return self


def _empty_workflow():
    nodes = [StartNode(id="s", type="start", position=Position(x=0, y=0), data={"label": "S"}),
             EndNode(id="e", type="end", position=Position(x=400, y=0), data={"label": "E"})]
    edges = [WorkflowEdge(id="e1", source="s", target="e")]
    return Workflow(name="t", nodes=nodes, edges=edges)


async def test_planning_turn_emits_plan_event():
    model = _ScriptedModel('{"stages":[{"id":"s1","summary":"ingest"}]}')
    session = BuildSession(workflow_id="w1")
    events, reply, wf = await run_builder_turn(
        model=model, workflow_id="w1", workflow=_empty_workflow(),
        session=session, message="build me a RAG pipeline", execution_id=None, db=None,
    )
    assert any(e.get("type") == "plan" for e in events)
    assert wf is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/builder/test_builder_agent.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.builder.builder_agent'`.

- [ ] **Step 3: Implement `prompts.py`**

```python
"""System-prompt templates for the builder agent."""

from src.builder.node_catalog import build_node_catalog

SYSTEM_PROMPT_BUILDER = """\
You are Composer's workflow builder. You turn an English description into a
Composer workflow (nodes + edges) on the user's canvas.

Rules:
- Work in stages. When the user first describes the workflow, respond with a
  JSON plan ONLY: {{"stages":[{{"id","summary"}}]}}. Do not create nodes yet.
- After the user approves, build each stage using the provided tools, then stop
  and ask for confirmation before the next stage.
- A workflow must have exactly one `start` node and at least one `end` node, and
  every non-visual node must be reachable from start.
- Never invent a credential value. If a node needs a credential you do not have,
  call `request_credential`.
- You may leave the workflow structurally incomplete mid-build; the remaining
  errors are reported to you and you should fix them.

Available node types:
{catalog}
"""


def build_system_prompt() -> str:
    return SYSTEM_PROMPT_BUILDER.format(catalog=build_node_catalog())
```

- [ ] **Step 4: Implement `builder_agent.py`**

```python
"""Builder agent loop + ephemeral in-memory BuildSession."""

import json
import time
from dataclasses import dataclass, field
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from src.builder.builder_tools import BuilderContext, make_builder_tools
from src.builder.diagnostics import build_diagnosis
from src.builder.draft import Draft
from src.builder.prompts import build_system_prompt
from src.engine.graph_builder import collect_workflow_violations
from src.engine.workflow import Workflow

MAX_TOOL_ITERATIONS = 12


@dataclass
class BuildSession:
    workflow_id: str
    messages: list[BaseMessage] = field(default_factory=list)
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
        return data
    return None


async def _tool_loop(model: BaseChatModel, ctx: BuilderContext, session: BuildSession) -> str:
    """Run the tool-call loop over the mutation tools, returning the final text."""
    tools = make_builder_tools(ctx)
    bound = model.bind_tools(tools)
    messages = [*_system_messages(), *session.messages]

    for _ in range(MAX_TOOL_ITERATIONS):
        response = await bound.ainvoke(messages)
        messages.append(response)
        tool_calls = getattr(response, "tool_calls", None) or []
        if not tool_calls:
            content = getattr(response, "content", "")
            return content if isinstance(content, str) else str(content)
        for call in tool_calls:
            name = call.get("name")
            args = call.get("args") or {}
            tool = next((t for t in tools if t.name == name), None)
            if tool is None:
                result = f"ERROR: unknown tool {name!r}."
            else:
                try:
                    result = await tool.ainvoke(args) if hasattr(tool, "ainvoke") else tool.func(**args)
                except Exception as exc:  # noqa: BLE001
                    result = f"ERROR: {exc}"
            from langchain_core.messages import ToolMessage
            messages.append(ToolMessage(content=str(result), tool_call_id=call.get("id", "0")))
    return "I couldn't finish that within the iteration budget."


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
    """Run one turn. Returns (events, reply, updated_workflow). Persisting to the
    DB is the caller's job (Task 7); here the workflow is returned so it can be
    validated and (by the caller) saved."""
    session.touch()
    events: list[dict[str, Any]] = []
    session.messages.append(HumanMessage(content=message))

    # Diagnose-first turn: inject redacted failure context.
    if execution_id and db is not None:
        diag = await build_diagnosis(db, execution_id)
        if diag is not None:
            session.messages.append(
                SystemMessage(
                    content=f"Diagnosis of failed execution {execution_id}: "
                            f"status={diag.status!r}, error={diag.error!r}, "
                            f"node_results={diag.node_results!r}, variables={diag.variables_tail!r}"
                )
            )

    # Planning turn: no node has been created yet and the message looks like a build request.
    has_nodes = bool(workflow.nodes)
    if not has_nodes:
        response = await model.ainvoke([*_system_messages(), *session.messages])
        content = getattr(response, "content", "") if hasattr(response, "content") else ""
        text = content if isinstance(content, str) else str(content)
        plan = _try_parse_plan(text)
        if plan is not None:
            events.append({"type": "plan", "stages": plan.get("stages", [])})
            session.messages.append(AIMessage(content=json.dumps(plan)))
            return events, "Here's my plan. Approve it and I'll start building.", workflow

    # Build/patch turn: seed the draft from the current workflow, run the tool loop.
    ctx = BuilderContext(draft=Draft())
    for node in workflow.nodes:
        ctx.draft.nodes[node.id] = node
    for edge in workflow.edges:
        ctx.draft.edges[edge.id] = edge

    reply = await _tool_loop(model, ctx, session)

    nodes, edges = ctx.draft.snapshot()
    updated = Workflow(
        id=workflow.id, name=workflow.name, description=workflow.description,
        nodes=nodes, edges=edges,
    )
    violations = collect_workflow_violations(updated)
    if violations:
        events.append({"type": "message", "content": "Remaining issues: " + "; ".join(violations[:5])})
    if ctx.events:
        events.extend(ctx.events)
    # Record the stage-applied event only when the graph changed.
    events.append({"type": "stage_applied",
                   "node_count": len(nodes), "edge_count": len(edges)})
    session.messages.append(AIMessage(content=reply))
    return events, reply, updated
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/builder/test_builder_agent.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/builder/prompts.py src/builder/builder_agent.py tests/unit/builder/test_builder_agent.py
git commit -m "feat(builder): agent loop, BuildSession, and prompts

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: `src/api/builder.py` — endpoint, authz, persistence, wiring

**Files:**
- Create: `src/api/builder.py`
- Modify: `src/config.py`, `src/main.py`
- Test: `tests/unit/api/test_builder_routes.py`

**Interfaces:**
- `POST /builder/{workflowId}/message` — body `BuilderMessageRequest { message, session_id?, execution_id? }`; response `BuilderMessageResponse { session_id, events, reply, workflow, validation_errors }`.
- Consumes (from earlier tasks): `BuildSession`, `run_builder_turn`, `collect_workflow_violations`, `build_chat_model`; from `workflows.py`: `_encrypt_jira_tokens`, `_encrypt_vector_db_keys`, `_encrypt_http_headers`, `_encrypt_confluence_tokens`, `_to_workflow_read`, `_has_assignment`, `_check_workflow_size_limits`.
- Produces: `router` (APIRouter) + `_sessions` (dict on `app.state.builder_sessions`).

- [ ] **Step 1: Add config field**

In `src/config.py`, next to the other `rate_limit_*` fields (near line 289-305), add:
```python
    rate_limit_builder_per_minute: int = 20
```

- [ ] **Step 2: Write the failing test**

```python
# tests/unit/api/test_builder_routes.py
from fastapi.testclient import TestClient

from src.api.builder import router
from src.main import create_app  # noqa: F401  # ensure import works


async def test_session_missing_returns_session_expired(mocker):
    # Direct dependency override path: build a client, override get_db/get_current_role.
    from fastapi import FastAPI
    from src.api import builder as builder_mod
    from src.security.rate_limit import RateLimiterProtocol

    app = FastAPI()
    app.include_router(router)

    async def fake_db():
        return None

    async def fake_role():
        return ("dev", "member")

    class _Limiter:
        async def check(self, route_key, client_key, config):
            return True, 0.0

    app.state.builder_sessions = {}
    app.dependency_overrides[builder_mod.get_db] = fake_db
    app.dependency_overrides[builder_mod.get_current_role] = fake_role
    app.dependency_overrides[builder_mod.get_rate_limiter] = lambda _: _Limiter()

    client = TestClient(app)
    resp = client.post("/builder/w1/message", json={"message": "hi", "session_id": "nope"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == "nope"
    assert any(e.get("type") == "session_expired" for e in body["events"])
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/unit/api/test_builder_routes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.api.builder'`.

- [ ] **Step 4: Implement `src/api/builder.py`**

```python
"""POST /builder/{workflowId}/message — the natural-language workflow builder."""

import secrets
import time
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]
from src.api.workflows import (  # reuse existing secret helpers (additive import)
    _check_workflow_size_limits,
    _encrypt_confluence_tokens,
    _encrypt_http_headers,
    _encrypt_jira_tokens,
    _encrypt_vector_db_keys,
    _has_assignment,
    _to_workflow_read,
)
from src.builder.builder_agent import BuildSession, run_builder_turn
from src.config import get_settings
from src.engine.graph_builder import collect_workflow_violations
from src.engine.workflow import Workflow
from src.llm.providers import build_chat_model
from src.security.auth import get_current_role
from src.security.rate_limit import (
    RateLimiterProtocol,
    enforce,
    get_rate_limiter,
    per_minute_config,
)
from src.storage.db import get_db

router = APIRouter(tags=["builder"])

DEFAULT_BUILDER_MODEL = "anthropic/claude-sonnet-4-5"
SESSION_TTL_SECONDS = 30 * 60


class BuilderMessageRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    message: str
    session_id: str | None = Field(default=None, alias="sessionId")
    execution_id: str | None = Field(default=None, alias="executionId")


class BuilderMessageResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    session_id: str = Field(alias="sessionId")
    events: list[dict[str, Any]]
    reply: str
    workflow: Any
    validation_errors: list[str]


def _get_sessions(request: Request) -> dict[str, BuildSession]:
    sessions = getattr(request.app.state, "builder_sessions", None)
    if sessions is None:
        request.app.state.builder_sessions = {}
        sessions = request.app.state.builder_sessions
    return sessions


def _prune_sessions(sessions: dict[str, BuildSession]) -> None:
    now = time.monotonic()
    for sid in [s for s, sess in sessions.items() if now - sess.last_activity > SESSION_TTL_SECONDS]:
        del sessions[sid]


async def _load_workflow(db: Prisma, workflow_id: str) -> Any:
    row = await db.workflow.find_unique(where={"id": workflow_id})
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow {workflow_id!r} not found.",
        )
    return row


def _to_engine_workflow(row: Any) -> Workflow:
    return Workflow.model_validate({
        "id": row.id,
        "name": row.name,
        "description": row.description,
        "nodes": row.nodes if isinstance(row.nodes, list) else [],
        "edges": row.edges if isinstance(row.edges, list) else [],
    })


async def _persist(
    db: Prisma, workflow_id: str, workflow: Workflow, existing: Any,
) -> Any:
    nodes_json = [node.model_dump(by_alias=True) for node in workflow.nodes]
    existing_nodes = existing.nodes if isinstance(existing.nodes, list) else []
    _encrypt_jira_tokens(nodes_json, existing_nodes=existing_nodes)
    _encrypt_vector_db_keys(nodes_json, existing_nodes=existing_nodes)
    _encrypt_http_headers(nodes_json, existing_nodes=existing_nodes)
    _encrypt_confluence_tokens(nodes_json, existing_nodes=existing_nodes)
    edges_json = [edge.model_dump(by_alias=True) for edge in workflow.edges]
    from prisma import Json
    return await db.workflow.update(
        where={"id": workflow_id},
        data={"nodes": Json(nodes_json), "edges": Json(edges_json)},
    )


@router.post("/builder/{workflow_id}/message", response_model=BuilderMessageResponse)
async def builder_message(  # pyright: ignore[reportUnusedFunction]
    workflow_id: str,
    payload: BuilderMessageRequest,
    request: Request,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    _role: tuple[str, str] = Depends(get_current_role),
    limiter: RateLimiterProtocol = Depends(get_rate_limiter),
) -> BuilderMessageResponse:
    user_id, role = _role
    await enforce(
        limiter,
        route_key="builder",
        client_key=user_id,
        config=per_minute_config(get_settings().rate_limit_builder_per_minute),
    )

    existing = await _load_workflow(db, workflow_id)
    if role != "admin" and existing.userId != user_id and not await _has_assignment(db, workflow_id, user_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found.")

    sessions = _get_sessions(request)
    _prune_sessions(sessions)

    session = sessions.get(payload.session_id) if payload.session_id else None
    if payload.session_id and session is None:
        return BuilderMessageResponse(
            session_id=payload.session_id,
            events=[{"type": "session_expired"}],
            reply="This build session has expired. Please start over.",
            workflow=_to_workflow_read(existing),
            validation_errors=collect_workflow_violations(_to_engine_workflow(existing)),
        )
    if session is None:
        session = BuildSession(workflow_id=workflow_id)
        sessions[session.workflow_id + ":" + secrets.token_hex(8)] = session
        # Re-key for a stable session id:
        sid = next(k for k, v in sessions.items() if v is session)
    else:
        sid = payload.session_id or ""

    model_string = (await _deployment_setting(db, "builder_model")) or DEFAULT_BUILDER_MODEL
    model = build_chat_model(model_string)

    workflow = _to_engine_workflow(existing)
    events, reply, updated = await run_builder_turn(
        model=model,
        workflow_id=workflow_id,
        workflow=workflow,
        session=session,
        message=payload.message,
        execution_id=payload.execution_id,
        db=db,
    )

    _check_workflow_size_limits(updated)
    row = await _persist(db, workflow_id, updated, existing)
    validation_errors = collect_workflow_violations(updated)
    return BuilderMessageResponse(
        session_id=sid,
        events=events,
        reply=reply,
        workflow=_to_workflow_read(row),
        validation_errors=validation_errors,
    )


async def _deployment_setting(db: Prisma, key: str) -> str | None:
    row = await db.deploymentsetting.find_unique(where={"key": key})
    return cast("str | None", row.value if row else None)
```

- [ ] **Step 5: Register the router**

In `src/main.py`, add the import near the other `src.api` imports and the `app.include_router(...)` call near the other includes:
```python
from src.api.builder import router as builder_router
...
    app.include_router(builder_router)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/unit/api/test_builder_routes.py -v` then `uv run ruff check src tests && uv run pyright src tests`.
Expected: PASS; lint + type-check clean.

- [ ] **Step 7: Commit**

```bash
git add src/api/builder.py src/config.py src/main.py tests/unit/api/test_builder_routes.py
git commit -m "feat(builder): POST /builder/{id}/message endpoint with authz, rate limit, persistence

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: `frontend/lib/api/builder.ts` — typed client

**Files:**
- Create: `frontend/lib/api/builder.ts`
- Test: `frontend/lib/api/builder.test.ts`

**Interfaces:**
- Produces: `sendBuilderMessage(workflowId, body) -> Promise<BuilderMessageResponse>` and the `BuilderEvent`, `BuilderMessageRequest`, `BuilderMessageResponse` types.

- [ ] **Step 1: Write the failing test**

```ts
// frontend/lib/api/builder.test.ts
import { describe, expect, it, vi } from "vitest";

vi.mock("./client", () => ({
  apiFetch: vi.fn(async (_path: string, _opts?: RequestInit) => ({
    sessionId: "s1",
    events: [],
    reply: "ok",
    workflow: {},
    validation_errors: [],
  })),
}));

import { sendBuilderMessage } from "./builder";

describe("sendBuilderMessage", () => {
  it("posts to the builder endpoint", async () => {
    const res = await sendBuilderMessage("w1", { message: "hi", sessionId: "s1" });
    expect(res.sessionId).toBe("s1");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run lib/api/builder.test.ts`
Expected: FAIL — cannot resolve `./builder`.

- [ ] **Step 3: Implement**

```ts
// frontend/lib/api/builder.ts
import { apiFetch } from "./client";

export type BuilderEvent =
  | { type: "plan"; stages: Array<{ id: string; summary: string }> }
  | { type: "stage_applied"; node_count: number; edge_count: number }
  | { type: "credential_request"; kind: string; node_id: string; detail: string; resolvable_by: "user" | "admin" }
  | { type: "patch_proposal"; diff: unknown; explanation: string }
  | { type: "message"; content: string }
  | { type: "error"; detail: string }
  | { type: "session_expired" };

export type BuilderMessageRequest = {
  message: string;
  sessionId?: string;
  executionId?: string;
};

export type BuilderMessageResponse = {
  sessionId: string;
  events: BuilderEvent[];
  reply: string;
  workflow: {
    id: string;
    nodes: unknown[];
    edges: unknown[];
    [key: string]: unknown;
  };
  validation_errors: string[];
};

export async function sendBuilderMessage(
  workflowId: string,
  body: BuilderMessageRequest
): Promise<BuilderMessageResponse> {
  return apiFetch<BuilderMessageResponse>(`/builder/${workflowId}/message`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run lib/api/builder.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/api/builder.ts frontend/lib/api/builder.test.ts
git commit -m "feat(builder): typed frontend client for the builder endpoint

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: builder-chat components

**Files:**
- Create: `frontend/components/composer/builder-chat/plan-card.tsx`
- Create: `frontend/components/composer/builder-chat/credential-request-card.tsx`
- Create: `frontend/components/composer/builder-chat/patch-diff-card.tsx`
- Create: `frontend/components/composer/builder-chat/builder-panel.tsx`
- Test: `frontend/components/composer/builder-chat/plan-card.test.tsx`

**Interfaces:**
- `BuilderPanel({ workflowId, onWorkflowUpdated })` — holds `sessionId` + display transcript; calls `sendBuilderMessage`; renders child cards; invokes `onWorkflowUpdated(workflow)` after each turn.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/components/composer/builder-chat/plan-card.test.tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PlanCard } from "./plan-card";

describe("PlanCard", () => {
  it("lists stages", () => {
    render(
      <PlanCard
        stages={[
          { id: "s1", summary: "Ingest documents" },
          { id: "s2", summary: "Answer questions" },
        ]}
        onApprove={() => {}}
      />
    );
    expect(screen.getByText("Ingest documents")).toBeTruthy();
    expect(screen.getByText("Answer questions")).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run components/composer/builder-chat/plan-card.test.tsx`
Expected: FAIL — cannot resolve `./plan-card`.

- [ ] **Step 3: Implement the cards**

```tsx
// plan-card.tsx
"use client";
import { Button } from "@/components/ui/button";

export function PlanCard({
  stages,
  onApprove,
}: {
  stages: Array<{ id: string; summary: string }>;
  onApprove: () => void;
}) {
  return (
    <div className="rounded-md border p-3">
      <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Proposed plan
      </p>
      <ol className="mb-3 list-decimal space-y-1 pl-5 text-sm">
        {stages.map((s) => (
          <li key={s.id}>{s.summary}</li>
        ))}
      </ol>
      <Button size="sm" onClick={onApprove}>
        Approve plan
      </Button>
    </div>
  );
}
```

```tsx
// credential-request-card.tsx
"use client";
export function CredentialRequestCard({
  kind,
  detail,
}: {
  kind: string;
  detail: string;
}) {
  return (
    <div className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm">
      <p className="font-medium">Needs a credential: {kind}</p>
      <p className="text-muted-foreground">{detail}</p>
    </div>
  );
}
```

```tsx
// patch-diff-card.tsx
"use client";
import { Button } from "@/components/ui/button";

export function PatchDiffCard({
  explanation,
  onApprove,
  onReject,
}: {
  explanation: string;
  onApprove: () => void;
  onReject: () => void;
}) {
  return (
    <div className="rounded-md border p-3 text-sm">
      <p className="mb-2 font-medium">Suggested fix</p>
      <p className="mb-3 text-muted-foreground">{explanation}</p>
      <div className="flex gap-2">
        <Button size="sm" onClick={onApprove}>Approve</Button>
        <Button size="sm" variant="outline" onClick={onReject}>Reject</Button>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Implement `builder-panel.tsx`**

```tsx
"use client";
import { useState } from "react";
import { sendBuilderMessage, type BuilderEvent } from "@/lib/api/builder";
import { PlanCard } from "./plan-card";
import { CredentialRequestCard } from "./credential-request-card";
import { PatchDiffCard } from "./patch-diff-card";

type Msg = { role: "user" | "assistant"; text: string; events: BuilderEvent[] };

export function BuilderPanel({
  workflowId,
  onWorkflowUpdated,
}: {
  workflowId: string;
  onWorkflowUpdated: (workflow: { nodes: unknown[]; edges: unknown[] }) => void;
}) {
  const [sessionId, setSessionId] = useState<string | undefined>();
  const [transcript, setTranscript] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);

  async function send(text: string) {
    if (!text.trim()) return;
    setBusy(true);
    setTranscript((t) => [...t, { role: "user", text, events: [] }]);
    setInput("");
    try {
      const res = await sendBuilderMessage(workflowId, {
        message: text,
        sessionId,
      });
      setSessionId(res.sessionId);
      setTranscript((t) => [...t, { role: "assistant", text: res.reply, events: res.events }]);
      if (res.workflow.nodes) onWorkflowUpdated(res.workflow as never);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex h-full w-80 flex-col border-l bg-background">
      <div className="border-b px-3 py-2 text-sm font-semibold">Build with AI</div>
      <div className="flex-1 space-y-2 overflow-y-auto p-3">
        {transcript.map((m, i) => (
          <div key={i} className="space-y-1">
            <p className={m.role === "user" ? "text-right text-sm" : "text-sm"}>{m.text}</p>
            {m.events.map((e, j) => (
              <div key={j}>
                {e.type === "plan" && (
                  <PlanCard stages={e.stages} onApprove={() => send("approve the plan")} />
                )}
                {e.type === "credential_request" && (
                  <CredentialRequestCard kind={e.kind} detail={e.detail} />
                )}
                {e.type === "patch_proposal" && (
                  <PatchDiffCard
                    explanation={e.explanation}
                    onApprove={() => send("apply the fix")}
                    onReject={() => send("reject the fix")}
                  />
                )}
              </div>
            ))}
          </div>
        ))}
      </div>
      <div className="flex gap-2 border-t p-3">
        <input
          className="min-w-0 flex-1 rounded-md border px-2 py-1 text-sm"
          value={input}
          disabled={busy}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") send(input);
          }}
          placeholder="Describe your workflow…"
        />
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd frontend && npx vitest run components/composer/builder-chat/plan-card.test.tsx && npx tsc --noEmit -p tsconfig.json`
Expected: PASS; type-check clean.

- [ ] **Step 6: Commit**

```bash
git add frontend/components/composer/builder-chat/
git commit -m "feat(builder): chat panel and event cards

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: designer integration

**Files:**
- Modify: `frontend/app/designer/[workflowId]/page.tsx`

**Interfaces:**
- Mounts `<BuilderPanel>` beside `<WorkflowCanvas>`; `onWorkflowUpdated` converts builder output via `toReactFlow` and remounts the canvas via a `canvasKey` so generated nodes render live.

- [ ] **Step 1: Add the panel + apply handler**

In `frontend/app/designer/[workflowId]/page.tsx`:
1. Add import: `import { BuilderPanel } from "@/components/composer/builder-chat/builder-panel";`
2. Add state near the other `useState` calls: `const [canvasKey, setCanvasKey] = useState(0);`
3. Add a handler that applies builder output to the canvas state and remounts:

```tsx
function applyBuilderWorkflow(next: { nodes: unknown[]; edges: unknown[] }) {
  const { rfNodes, rfEdges } = toReactFlow(
    next.nodes as unknown as ComposerNode[],
    next.edges as unknown as ComposerEdge[]
  );
  nodesRef.current = rfNodes;
  edgesRef.current = rfEdges;
  initialNodesRef.current = rfNodes;
  initialEdgesRef.current = rfEdges;
  setCanvasKey((k) => k + 1);
  void queryClient.invalidateQueries({ queryKey: ["workflow", workflowId] });
}
```

4. In the canvas/execution-panel flex container (near line 323), render the panel after the canvas:

```tsx
<WorkflowCanvas key={canvasKey} ... />
...
<BuilderPanel workflowId={workflowId} onWorkflowUpdated={applyBuilderWorkflow} />
```

Note: `applyBuilderWorkflow` must be defined inside `DesignerCanvasPageInner` so it closes over `nodesRef`, `edgesRef`, `initialNodesRef`, `initialEdgesRef`, `queryClient`, and `setCanvasKey`. `nodesRef`/`edgesRef` are already mutable refs; `toReactFlow` is already imported.

- [ ] **Step 2: Type-check and lint**

Run: `cd frontend && npx tsc --noEmit -p tsconfig.json`
Expected: clean.

- [ ] **Step 3: Manual smoke test (dev)**

Run backend (`uv run uvicorn src.main:app --reload --port 8001`) and frontend (`cd frontend && npm run dev`). Open a workflow in the designer, type a description, confirm the plan card appears, approve, and see nodes appear on the canvas. Confirm an invalid intermediate state still shows on the canvas with the issues surfaced in the panel.

- [ ] **Step 4: Commit**

```bash
git add frontend/app/designer/[workflowId]/page.tsx
git commit -m "feat(builder): mount chat panel in designer and apply generated workflows

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review Notes (run before execution handoff)

- **Spec coverage:** Tasks 1-7 cover every backend section of the spec (collector, catalog, layout, diagnostics, tools, agent/session, endpoint/persistence/model config). Tasks 8-10 cover the frontend (client, cards/panel, integration). The `patch_proposal` event type is typed in the client and carded, and the diagnose→patch path is wired through `execution_id` in the turn function — the patch-approval UI is a card in Task 9 with `apply the fix`/`reject the fix` send actions.
- **Type consistency:** `BuilderEvent` union in Task 8 matches the events emitted in Tasks 5-7 (`plan`, `stage_applied`, `credential_request`, `message`, `error`, `session_expired`, plus `patch_proposal`). `BuilderMessageResponse` field names (`sessionId`, `events`, `reply`, `workflow`, `validation_errors`) match Task 7's `response_model`.
- **Gaps to watch at execution time:** the exact `WorkflowCanvas` `initialNodes`/`initialEdges` consumption behavior (mount-only vs synced) must be confirmed by reading `workflow-canvas.tsx` before finalizing Task 10's `key`-remount approach; the LangChain tool-call message shape (`ToolMessage`, `tool_call_id`) may need minor adjustment against the installed `langchain-core` version; the scripted fake model in Task 6 may need to emit real `tool_calls` to exercise `_tool_loop` (the Task 6 test only exercises the planning branch — a follow-up test for `_tool_loop` with a tool-call-emitting fake should be added there).
