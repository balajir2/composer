# Phase 6a — Note + Join-Chunks: Design

**Status.** Approved 2026-04-21.
**Related.** Phase 1 Pydantic workflow models (ADR-0002), Phase 4a executor conventions.

---

## 1. Goal

Ship the `join-chunks` executor + lock the already-implemented visual-only behavior of `note`. These are the two node types with no external dependencies; 6a is the "local completion" of the workflow executor catalog before 6b/6c/6d add external-integration executors.

## 2. Non-goals

- **No ADR.** Every design choice here follows established patterns (executor registration, `lastOutput` output convention, variable substitution, Pydantic camelCase aliases). No new architectural decision.
- **No new dependencies.** Both executors are pure Python.
- **No UI.** Phase 10 wires frontend.

## 3. `note` — visual-only, already done

`graph_builder.build_graph` already does:
```python
for node in workflow.nodes:
    if node.type == "note":
        continue  # visual-only; skipped at build time per OAB behavior
    executor = build_executor(node)
    ...
```

And `_check_reachability` explicitly allows note nodes to be disconnected from the graph:
```python
for node_id, node in nodes.items():
    if node.type == "note":
        continue  # visual-only, allowed disconnected
    ...
```

**What Phase 6a does for note:**
1. Add one lock-test in `tests/unit/engine/test_graph_builder.py` that builds a workflow with a disconnected note node and asserts it compiles successfully and the note node is NOT registered with LangGraph (no executor created).
2. Ensure `NoteNodeData` remains permissive — it's a visual model, any `data` payload from the UI should validate. Verify the current definition; loosen if needed.
3. No executor module. No entry in `_REGISTRY`. No `_PHASE_FOR_TYPE` update (already mapped to Phase 6 in `src/executors/base.py`; can remove the mapping since note is never built, OR leave as defensive catch if graph_builder's skip is bypassed — we leave it; cheap).

## 4. `JoinChunksNodeData` — Pydantic tightening

Current `src/engine/workflow.py`:
```python
class JoinChunksNodeData(BaseNodeData):
    # TODO(phase-6): tighten when OAB's join-chunks executor is studied.
    config: dict[str, Any] = Field(default_factory=dict)
```

Replace with explicit fields matching OAB `types.ts:132-137`:

```python
class JoinChunksNodeData(BaseNodeData):
    model_config = ConfigDict(populate_by_name=True)

    variable: str = Field(alias="joinChunksVariable")
    separator: str = Field(default="\n\n", alias="joinChunksSeparator")
    prefix: str = Field(default="", alias="joinChunksPrefix")
    suffix: str = Field(default="", alias="joinChunksSuffix")
    include_metadata: bool = Field(default=False, alias="joinChunksIncludeMetadata")
```

- `variable` is required — no sensible default (executor needs somewhere to read the array from).
- All other fields have defaults; camelCase aliases match the OAB JSON shape.
- `populate_by_name=True` lets Python code construct with snake_case (tests) while JSON uses camelCase.

## 5. `JoinChunksExecutor` — semantics

**File:** `src/executors/join_chunks.py` (new).

### Input resolution

`variable` is a **simple top-level key** into `state.variables` — mirroring how `extract`/`set-state` read variables. No deep dot-path parsing in 6a (simple first, dot-path lands in Phase 7 if there's demand).

```python
chunks_raw = state.get("variables", {}).get(self.node.data.variable)
```

If `chunks_raw is None` → `JoinChunksNodeError(f"variable {variable!r} not found in state")`.
If `chunks_raw` is not a `list` → `JoinChunksNodeError(f"variable {variable!r} is not a list; got {type(...)}")`.
If `chunks_raw` is `[]` → return empty string (legitimate empty case; no error).

### Item rendering

Per chunk:
- If `chunk` is `str` → use as-is.
- If `chunk` is `dict`:
  - Read `chunk.get("content")`; if present and `isinstance(content, str)`, use it.
  - Else serialize the whole dict as compact JSON.
- Else (number, bool, etc.) → `str(chunk)`.

This matches the shape produced by Phase 6d's `vector-db` executor (returns `list[dict]` with `content` + `metadata`) and LangChain's `Document` convention (`page_content` + `metadata`). For 6a, we support the OAB JSON shape (`{"content": "...", "metadata": {...}}`); dot-access to `page_content` isn't needed.

Rendered item:
```python
rendered = f"{prefix}{content}{suffix}"
if include_metadata and isinstance(chunk, dict) and chunk.get("metadata"):
    rendered += f"\n[metadata: {json.dumps(chunk['metadata'])}]"
```

### Output

Write to `lastOutput` in variables (consistent with `http`, `transform`, `extract`):

```python
return {
    "variables": {"lastOutput": joined},
    "current_node_id": self.node.id,
    "node_results": {
        self.node.id: {
            "node_id": self.node.id,
            "status": "completed",
            "input": {"variable": self.node.data.variable, "chunk_count": len(chunks_raw)},
            "output": joined,
        }
    },
}
```

### Variable substitution

The `variable`, `separator`, `prefix`, `suffix` fields are NOT substituted via `{{...}}` — they're configuration, not content templates. If users want dynamic separators, Phase 7 can revisit. Keep 6a simple and predictable.

## 6. Error model

One exception class `JoinChunksNodeError(RuntimeError)` with clear messages:
- `"variable 'foo' not found in state"` → KeyError-equivalent
- `"variable 'foo' is not a list; got <type>"` → TypeError-equivalent

Both propagate up through `LangGraphExecutor.run()` → execution marked `failed` per existing `_mark_failed` path.

## 7. Test plan

### 7.1 Unit tests (`tests/unit/executors/test_join_chunks.py`)

1. String chunks → concat with default separator.
2. String chunks with custom separator (`"\n---\n"`), prefix (`"> "`), suffix (`" "`).
3. Dict chunks with `content` key → extract content.
4. Dict chunks without `content` key → JSON-serialize whole dict.
5. Dict chunks with metadata + `include_metadata=True` → append `[metadata: {...}]`.
6. Dict chunks with metadata + `include_metadata=False` → no metadata line.
7. Mixed-type chunks (strings + dicts) → each rendered correctly.
8. Empty list → empty string output.
9. Missing variable → `JoinChunksNodeError`.
10. Non-list value → `JoinChunksNodeError`.
11. Executor registered — `build_executor(join_chunks_node)` returns `JoinChunksExecutor`.

### 7.2 Note affirmation test (`tests/unit/engine/test_graph_builder.py`)

One new test:
```python
def test_build_graph_skips_note_nodes() -> None:
    wf = Workflow.model_validate({
        "id": "w", "name": "note test",
        "nodes": [
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {"id": "n", "type": "note", "position": {"x": 0, "y": 100}, "data": {"label": "some note", "text": "FYI"}},
            {"id": "e", "type": "end", "position": {"x": 100, "y": 0}, "data": {"label": "E"}},
        ],
        "edges": [{"id": "e1", "source": "s", "target": "e"}],  # note disconnected
    })
    compiled = build_graph(wf, MemorySaver())
    assert compiled is not None
    # Introspect compiled graph if possible; otherwise trust that no error
    # was raised (the executor registry has no "note" entry, so if graph_builder
    # tried to build one we'd get NotImplementedError).
```

### 7.3 Integration test (`tests/integration/test_join_chunks.py`)

One real-Neon integration test: register a workflow `start → set-state(key=chunks, value=[...]) → join-chunks(variable=chunks) → end`; start execution; poll until completed; assert `variables.lastOutput` equals the expected concatenation.

Actually — `set-state` sets scalar values, not arrays. Check existing behavior. If `set-state` can't emit a list, the integration workflow uses `transform` (simpleeval expression `[1,2,3]`) to produce the list, then pipes to `join-chunks`. Pick whichever works; the plan's Task 4 will verify at write time.

## 8. Phase-exit checklist

- [ ] All unit tests green.
- [ ] Integration test green against real Neon.
- [ ] Ruff + format + pyright strict clean.
- [ ] `CHANGELOG.md` — Phase 6a section.
- [ ] `CLAUDE.md` — phase table updated: 6a → ✅, next is 6b (guardrails).

## 9. Self-review

- **Placeholder scan:** None. `TODO(phase-6)` in the current `JoinChunksNodeData` is resolved.
- **Internal consistency:** `variable` / `separator` / `prefix` / `suffix` / `include_metadata` consistent across §4/§5/§7.
- **Scope:** One executor + one lock-test + one model tightening. Single-plan territory.
- **Ambiguity:** Dict-chunk-no-content-key behavior pinned (JSON-serialize whole dict). Metadata format pinned (`[metadata: {json}]` appended). Variable-resolution scope pinned (flat top-level, no dot-path in 6a).
