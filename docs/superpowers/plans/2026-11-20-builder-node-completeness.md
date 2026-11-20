# Builder Node Completeness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** "Build with AI" produces workflows whose nodes are actually filled in (instructions, guardrail checks, models, declared variables), never stores duplicate edges by any path, and has tests that catch a misbehaving model before users do.

**Architecture:** Two kinds of checks, kept apart on purpose. *Structural* rules (`collect_workflow_violations`) gate every canvas save and every run, so they must accept a half-built canvas. Duplicate edges are therefore **silently collapsed** on every save/run path instead of rejected. *Content* rules (new `collect_completeness_issues`) only steer the builder: after each build turn the issues are fed back to the model for up to two repair rounds, and anything left is shown to the user as "Remaining issues". The prompt also gains a field guide generated from the Pydantic node models and the admin-enabled model list, so the model knows what to fill.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, LangChain tools, pytest + pytest-asyncio; Next.js 15, Playwright.

**Background:** 2026-11-19 report: a builder-made workflow had duplicate `tavily_search -> end-1` / `pii-filter -> end-1` edges, and its nodes had no prompts, no start variables and no models. The duplicate-edge cause was fixed in `2b073c2` (PR #18). Root causes for the empty nodes, verified in code: every content field on agent/mcp/guardrails data is optional; `create_node`'s `data` argument is optional and free-form, so a bare type/id/label call passes; the prompt never lists valid model ids or guardrails fields; nothing after the build checks content.

**Branch:** `feat/builder-completeness` off `main`. Do NOT touch `D:/GitHub/open-agent-builder`.

**Commands** (the shell has no `uv`/`python` on PATH; use the venv):

```bash
.venv/Scripts/python -m pytest <path> -q --no-cov -p no:cacheprovider
.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format --check src tests
.venv/Scripts/python -m pyright src tests
cd frontend && npx vitest run <file> && npx tsc --noEmit -p . && npx prettier --check <files>
```

Every commit ends with:

```
Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
```

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/engine/graph_builder.py` | Structural rules + graph compile | Add `dedupe_edges`; collapse before validating in `build_graph` |
| `src/api/workflows.py` | Workflow create/update | Collapse duplicates before validating and saving |
| `src/builder/normalize.py` | Builder start/end repairs | Use shared `dedupe_edges` (delete local copy) |
| `src/builder/completeness.py` | **New.** Content rules for builder output | `collect_completeness_issues` |
| `src/builder/builder_tools.py` | Builder tools | Rename `_NODE_CLASSES` → `NODE_CLASSES` (public; prompt reads it) |
| `src/builder/node_catalog.py` | LLM-facing node vocabulary | Add `build_field_guide`, `describe_models` |
| `src/builder/prompts.py` | System prompt | Field guide, model list, completeness rules |
| `src/builder/model_policy.py` | Builder model policy | Rename `_enabled_catalog` → `enabled_model_catalog` |
| `src/builder/builder_agent.py` | Turn orchestration | Thread `model_catalog`; repair rounds |
| `src/api/builder.py` | Builder route | Load catalog, pass it to the turn |
| `tests/unit/engine/test_dedupe_edges.py` | **New** | Shared helper + save/run paths |
| `tests/unit/builder/test_completeness.py` | **New** | Each content rule |
| `tests/unit/builder/test_node_catalog.py` | Existing | Field guide + model list |
| `tests/unit/builder/test_builder_agent.py` | Existing | Misbehaving-model turn tests |
| `tests/integration/test_builder_live.py` | **New**, `@pytest.mark.live` | Real-model quality check (weekly job) |
| `frontend/e2e/builder-race.spec.ts` | **New** | Delete-edge-then-message race in a real browser |
| `.github/workflows/ci.yml` | CI | Add the race spec to the e2e smoke list (authorized by this plan) |
| `CHANGELOG.md`, `docs/designer-guide.md`, `docs/architecture.md` | Docs | Lockstep updates |

---

### Task 1: Duplicate edges collapsed on every save and run path

**Files:**
- Modify: `src/engine/graph_builder.py` (add helper above `collect_workflow_violations`; first line of `build_graph`)
- Modify: `src/api/workflows.py` (create ≈ line 363, update ≈ line 545: right after `workflow = Workflow.model_validate(...)`)
- Modify: `src/builder/normalize.py` (replace `_collapse_duplicate_edges`)
- Test: `tests/unit/engine/test_dedupe_edges.py`

Why not a validation error: `validate_workflow_shape` runs on every autosave and before every run. Rejecting duplicates would make existing workflows that already contain them unsaveable and unrunnable.

- [ ] **Step 1: Write the failing tests**

```python
"""Duplicate edges (same source, target, branch) are collapsed, never stored.

2026-11-19: duplicate edges reappeared on a builder-made canvas. The builder
path was fixed in 2b073c2; these tests pin the shared rule used by every
other path that saves or runs a workflow.
"""

from src.engine.graph_builder import dedupe_edges
from src.engine.workflow import WorkflowEdge


def _e(eid: str, source: str, target: str, branch: str | None = None) -> WorkflowEdge:
    return WorkflowEdge(id=eid, source=source, target=target, branch=branch)


def test_first_edge_per_source_target_branch_wins() -> None:
    edges = [_e("a", "s", "x"), _e("b", "s", "x"), _e("c", "x", "e"), _e("d", "s", "x")]
    assert [e.id for e in dedupe_edges(edges)] == ["a", "c"]


def test_different_branches_are_different_edges() -> None:
    edges = [_e("t", "if", "e", "true"), _e("f", "if", "e", "false")]
    assert [e.id for e in dedupe_edges(edges)] == ["t", "f"]


def test_no_duplicates_returns_equal_list() -> None:
    edges = [_e("a", "s", "x"), _e("b", "x", "e")]
    assert dedupe_edges(edges) == edges
```

Add to the existing workflow-API unit tests (find the file with `grep -rln "async def test_.*update_workflow\|PUT.*workflows" tests/unit/api`; reuse its client and fixture style) one test per route:

```python
async def test_update_workflow_collapses_duplicate_edges(<existing client fixture>) -> None:
    body = <existing valid update payload helper>(
        edges=[
            {"id": "e1", "source": "start-1", "target": "end-1"},
            {"id": "e2", "source": "start-1", "target": "end-1"},
        ]
    )
    resp = await client.put(f"/workflows/{wf_id}", json=body)
    assert resp.status_code == 200
    assert [e["id"] for e in resp.json()["edges"]] == ["e1"]
```

Without the fix, this test gets 422 ("End node 'end-1' must have exactly one incoming edge ... (found 2)"). That is the real user-facing failure: a canvas holding duplicates can't be saved.

- [ ] **Step 2: Run, verify failure**

Run: `.venv/Scripts/python -m pytest tests/unit/engine/test_dedupe_edges.py -q --no-cov -p no:cacheprovider`
Expected: FAIL with `ImportError: cannot import name 'dedupe_edges'`.

- [ ] **Step 3: Implement**

In `src/engine/graph_builder.py`:

```python
def dedupe_edges(edges: list[WorkflowEdge]) -> list[WorkflowEdge]:
    """Keep the first edge per (source, target, branch); drop later copies.

    The engine routes on `branch` only (never `sourceHandle`), so two edges
    agreeing on all three are indistinguishable at run time. Applied on every
    save and run path instead of being reported as a violation: rejecting
    duplicates would make workflows that already contain them unsaveable.
    """
    seen: set[tuple[str, str, str | None]] = set()
    kept: list[WorkflowEdge] = []
    for edge in edges:
        key = (edge.source, edge.target, edge.branch)
        if key not in seen:
            seen.add(key)
            kept.append(edge)
    return kept
```

At the top of `build_graph`, before `validate_workflow_shape(workflow)`:

```python
    workflow = workflow.model_copy(update={"edges": dedupe_edges(workflow.edges)})
```

In `src/api/workflows.py`, in both `create_workflow` and `update_workflow`, directly after `workflow = Workflow.model_validate(payload.model_dump(by_alias=True))`:

```python
    workflow = workflow.model_copy(update={"edges": dedupe_edges(workflow.edges)})
```

(import `dedupe_edges` next to `validate_workflow_shape`). Both routes already build `edges_json` from `workflow.edges`, so the stored row is clean.

In `src/builder/normalize.py`, delete `_collapse_duplicate_edges` and make the first line of `normalize_orchestration`:

```python
    kept = {e.id for e in dedupe_edges(list(draft.edges.values()))}
    for edge_id in [eid for eid in draft.edges if eid not in kept]:
        del draft.edges[edge_id]
```

Edge ids are unique dict keys in a draft, so filtering by id is exact.

- [ ] **Step 4: Run, verify pass**

Run: `.venv/Scripts/python -m pytest tests/unit/engine tests/unit/builder tests/unit/api -q --no-cov -p no:cacheprovider`
Expected: all pass, including yesterday's `test_duplicate_edges_are_collapsed_to_one`.

- [ ] **Step 5: Commit**

```bash
git add src/engine/graph_builder.py src/api/workflows.py src/builder/normalize.py tests/unit/engine/test_dedupe_edges.py tests/unit/api/<file>
git commit -m "fix(workflows): collapse duplicate edges on every save and run path"
```

Commit body: why duplicates are collapsed rather than rejected (autosave and run both validate).

---

### Task 2: Content rules (`collect_completeness_issues`)

**Files:**
- Create: `src/builder/completeness.py`
- Test: `tests/unit/builder/test_completeness.py`

Rules, all producing model-readable strings that name the node and say how to fix it:

| Node | Rule |
|---|---|
| agent, mcp, jira | `instructions` non-blank |
| decision | `instruction` non-blank |
| gamma-ai | `prompt` non-blank |
| guardrails | at least one of `piiEnabled`, `moderationEnabled`, `jailbreakEnabled`, `hallucinationEnabled` |
| mcp | `mcpServerId` set, **unless** the builder already called `request_credential` for that node |
| agent, mcp | only when a model catalog is given: `model` set and in the catalog |
| every node | each `{{name...}}` head is a known name |

Known names (mirrors run-time scope in `src/executors/_eval.py` and aliases in `src/engine/events_wrapper.py:160-172`): `lastOutput`, `input`, `variables`, `node_results`; Start `inputVariables[].name`; set-state `stateKey`; every node id with `-` → `_`; snake_case of each node's `nodeName`. Labels are **not** aliases at run time, so `{{search}}` for a node labelled "Search" with no `nodeName` is correctly flagged.

The model rule covers agent and mcp only: both store `model` as `"provider/modelId"`, the same format as the catalog keys. Decision stores `provider` separately, and guardrails/jira formats aren't confirmed, so they're left out rather than producing false issues.

- [ ] **Step 1: Write the failing tests**

```python
"""Content checks that steer the builder toward filled-in nodes.

2026-11-19: a builder-made workflow had agent/MCP nodes with no prompts, a
guardrails node with every check off, and no models selected. None of that
is a structural violation, so nothing reported it.
"""

from typing import Any

from src.builder.completeness import collect_completeness_issues
from src.engine.workflow import Workflow

CATALOG = {"anthropic/claude-sonnet-4-5": "Sonnet"}


def _wf(*nodes: dict[str, Any]) -> Workflow:
    base: list[dict[str, Any]] = [
        {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
        {"id": "e", "type": "end", "position": {"x": 0, "y": 0}, "data": {"label": "E"}},
    ]
    full = [*base, *[{"position": {"x": 0, "y": 0}, **n} for n in nodes]]
    return Workflow.model_validate({"name": "t", "nodes": full, "edges": []})


def _agent(nid: str = "a", **data: Any) -> dict[str, Any]:
    return {"id": nid, "type": "agent", "data": {"label": "A", **data}}


def test_filled_agent_has_no_issues() -> None:
    wf = _wf(_agent(instructions="Summarise {{lastOutput}}", model="anthropic/claude-sonnet-4-5"))
    assert collect_completeness_issues(wf, model_catalog=CATALOG) == []


def test_agent_without_instructions_is_reported() -> None:
    issues = collect_completeness_issues(_wf(_agent()))
    assert len(issues) == 1
    assert "'a'" in issues[0] and "instructions" in issues[0]


def test_whitespace_instructions_count_as_missing() -> None:
    assert collect_completeness_issues(_wf(_agent(instructions="   ")))


def test_guardrails_with_every_check_off_is_reported() -> None:
    node = {"id": "pii-filter", "type": "guardrails", "data": {"label": "PII"}}
    issues = collect_completeness_issues(_wf(node))
    assert any("pii-filter" in i and "piiEnabled" in i for i in issues)


def test_guardrails_with_one_check_on_passes() -> None:
    node = {"id": "g", "type": "guardrails", "data": {"label": "G", "piiEnabled": True}}
    assert collect_completeness_issues(_wf(node)) == []


def test_mcp_without_server_is_reported_unless_credential_requested() -> None:
    node = {"id": "m", "type": "mcp", "data": {"label": "M", "instructions": "search"}}
    assert any("mcpServerId" in i for i in collect_completeness_issues(_wf(node)))
    assert collect_completeness_issues(_wf(node), credential_requested={"m"}) == []


def test_model_rules_apply_only_with_a_catalog() -> None:
    wf = _wf(_agent(instructions="x"))
    assert collect_completeness_issues(wf) == []
    assert any("model" in i for i in collect_completeness_issues(wf, model_catalog=CATALOG))


def test_model_outside_the_catalog_is_reported() -> None:
    wf = _wf(_agent(instructions="x", model="openai/not-enabled"))
    issues = collect_completeness_issues(wf, model_catalog=CATALOG)
    assert any("openai/not-enabled" in i for i in issues)


def test_undeclared_variable_is_reported_and_declared_one_is_not() -> None:
    wf = _wf(_agent(instructions="Research {{topic}} for {{audience}}"))
    start = next(n for n in wf.nodes if n.type == "start")
    start.data.input_variables = [  # pyright: ignore[reportAttributeAccessIssue]
        type(start.data).model_fields["input_variables"].annotation.__args__[0](name="topic")  # pyright: ignore
    ]
    issues = collect_completeness_issues(wf)
    assert any("audience" in i for i in issues)
    assert not any("topic" in i for i in issues)


def test_node_aliases_and_builtins_are_known() -> None:
    wf = _wf(
        {"id": "web-search", "type": "agent", "data": {"label": "W", "instructions": "go"}},
        {"id": "x", "type": "agent", "data": {"label": "X", "nodeName": "Place Extractor", "instructions": "go"}},
        _agent("b", instructions="{{web_search}} {{place_extractor.city}} {{lastOutput}} {{input}}"),
    )
    assert collect_completeness_issues(wf) == []


def test_label_is_not_a_run_time_alias() -> None:
    wf = _wf(
        {"id": "n1", "type": "agent", "data": {"label": "Search", "instructions": "go"}},
        _agent("b", instructions="use {{search}}"),
    )
    assert any("search" in i for i in collect_completeness_issues(wf))
```

If the `input_variables` construction in `test_undeclared_variable...` doesn't type-check cleanly, build the Start node with `StartNodeData(label="S", inputVariables=[StartInputVariable(name="topic")])` as `tests/unit/builder/test_normalize.py:_start` does, and pass it through a variant of `_wf` that takes the start node. Behaviour is what matters.

- [ ] **Step 2: Run, verify failure**

Run: `.venv/Scripts/python -m pytest tests/unit/builder/test_completeness.py -q --no-cov -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.builder.completeness'`.

- [ ] **Step 3: Implement `src/builder/completeness.py`**

```python
"""Content checks for builder output: are the nodes actually filled in?

Kept separate from `collect_workflow_violations` on purpose. That validator
gates every canvas save and every run, and a half-built canvas must still
save. These checks only steer the builder (its repair rounds) and warn the
user; they never block a save.
"""

import re
from collections.abc import Iterator, Mapping, Set
from typing import Any

from src.engine.workflow import Workflow

_INSTRUCTION_FIELD: dict[str, str] = {
    "agent": "instructions",
    "mcp": "instructions",
    "jira": "instructions",
    "decision": "instruction",
    "gamma-ai": "prompt",
}
# Types whose `model` is a "provider/modelId" string, the catalog's key format.
_MODEL_CHECKED_TYPES = {"agent", "mcp"}
_GUARDRAIL_FLAGS = ("piiEnabled", "moderationEnabled", "jailbreakEnabled", "hallucinationEnabled")
# Mirrors the run-time scope in src/executors/_eval.py.
_BUILTIN_NAMES = {"lastOutput", "input", "variables", "node_results"}
_MUSTACHE_HEAD = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)")


def _snake(text: str) -> str:
    # Same rule as events_wrapper._sanitize_node_name.
    return re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():  # pyright: ignore[reportUnknownVariableType]
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:  # pyright: ignore[reportUnknownVariableType]
            yield from _strings(item)


def _known_names(dumped: list[tuple[str, str, dict[str, Any]]]) -> set[str]:
    names = set(_BUILTIN_NAMES)
    for node_id, node_type, data in dumped:
        names.add(node_id.replace("-", "_"))
        node_name = data.get("nodeName")
        if isinstance(node_name, str) and _snake(node_name):
            names.add(_snake(node_name))
        if node_type == "start":
            names.update(v["name"] for v in data.get("inputVariables") or [])
        if node_type == "set-state" and isinstance(data.get("stateKey"), str):
            names.add(data["stateKey"])
    return names


def collect_completeness_issues(
    workflow: Workflow,
    *,
    model_catalog: Mapping[str, str | None] | None = None,
    credential_requested: Set[str] = frozenset(),
) -> list[str]:
    """Return one fix-it message per unfilled node (empty = complete)."""
    dumped = [(n.id, n.type, n.data.model_dump(by_alias=True)) for n in workflow.nodes]
    known = _known_names(dumped)
    issues: list[str] = []
    for node_id, node_type, data in dumped:
        where = f"{node_type} node {node_id!r}"
        field = _INSTRUCTION_FIELD.get(node_type)
        if field and not str(data.get(field) or "").strip():
            issues.append(f"{where} has no `{field}`: say what this step must do.")
        if node_type == "guardrails" and not any(data.get(f) for f in _GUARDRAIL_FLAGS):
            issues.append(
                f"{where} has every check off: enable at least one of "
                f"{', '.join(_GUARDRAIL_FLAGS)}."
            )
        if node_type == "mcp" and not data.get("mcpServerId") and node_id not in credential_requested:
            issues.append(
                f"{where} has no `mcpServerId`: call request_credential with kind "
                "'mcp-server' instead of guessing an id."
            )
        if model_catalog and node_type in _MODEL_CHECKED_TYPES:
            model = data.get("model")
            if not model:
                issues.append(f"{where} has no `model`: pick one of the available models.")
            elif model not in model_catalog:
                issues.append(
                    f"{where} uses model {model!r}, which is not available: "
                    "pick one of the available models."
                )
        missing = sorted({m for s in _strings(data) for m in _MUSTACHE_HEAD.findall(s)} - known)
        if missing:
            issues.append(
                f"{where} references undeclared variable(s) {', '.join(missing)}: "
                "declare them with set_start_inputs or fix the reference."
            )
    return issues
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/Scripts/python -m pytest tests/unit/builder/test_completeness.py -q --no-cov -p no:cacheprovider` → all pass. Then `.venv/Scripts/python -m pyright src/builder tests/unit/builder` → 0 errors.

- [ ] **Step 5: Commit** (`feat(builder): add content checks for builder-made nodes`)

---

### Task 3: Field guide and model list in the prompt

**Files:**
- Modify: `src/builder/builder_tools.py` (rename `_NODE_CLASSES` → `NODE_CLASSES`, all uses in the file)
- Modify: `src/builder/node_catalog.py`, `src/builder/prompts.py`
- Modify: `src/builder/model_policy.py` (rename `_enabled_catalog` → `enabled_model_catalog`, 1 def + 2 uses)
- Test: `tests/unit/builder/test_node_catalog.py`

- [ ] **Step 1: Write the failing tests** (append to `tests/unit/builder/test_node_catalog.py`)

```python
from src.builder.builder_tools import NODE_CLASSES
from src.builder.node_catalog import build_field_guide, describe_models
from src.builder.prompts import build_system_prompt


def test_field_guide_covers_every_node_type() -> None:
    guide = build_field_guide(NODE_CLASSES)
    for node_type in NODE_CLASSES:
        assert f"`{node_type}`:" in guide


def test_field_guide_names_the_fields_the_model_kept_leaving_out() -> None:
    lines = {line.split("`")[1]: line for line in build_field_guide(NODE_CLASSES).splitlines()}
    assert "instructions" in lines["agent"] and "model" in lines["agent"]
    assert "piiEnabled (bool)" in lines["guardrails"]
    assert "mcpServerId" in lines["mcp"]
    assert "label" not in lines["agent"]  # passed separately to create_node


def test_field_guide_marks_required_fields_and_literal_choices() -> None:
    lines = {line.split("`")[1]: line for line in build_field_guide(NODE_CLASSES).splitlines()}
    assert "instruction* (text)" in lines["decision"]
    assert "actionOnViolation (block|warn)" in lines["guardrails"]


def test_model_list_lists_ids_or_says_to_leave_unset() -> None:
    assert "`anthropic/claude-sonnet-4-5` (Sonnet)" in describe_models(
        {"anthropic/claude-sonnet-4-5": "Sonnet"}
    )
    assert "leave `model` unset" in describe_models({})


def test_system_prompt_includes_field_guide_and_models() -> None:
    prompt = build_system_prompt("(empty)", {"openai/gpt-5": None})
    assert "piiEnabled" in prompt
    assert "`openai/gpt-5`" in prompt
```

- [ ] **Step 2: Run, verify failure**

Run: `.venv/Scripts/python -m pytest tests/unit/builder/test_node_catalog.py -q --no-cov -p no:cacheprovider`
Expected: FAIL with `ImportError: cannot import name 'NODE_CLASSES'`.

- [ ] **Step 3: Implement**

Rename in `builder_tools.py`: `_NODE_CLASSES` → `NODE_CLASSES` (definition + the two uses in `_try_build_node`).

Append to `src/builder/node_catalog.py`:

```python
from collections.abc import Mapping
from types import UnionType
from typing import Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel

# label is its own create_node argument; nodeName is a UI alias field.
_GUIDE_SKIP = {"label", "nodeName"}


def _describe_type(annotation: Any) -> str:
    origin = get_origin(annotation)
    if origin in (Union, UnionType):
        inner = [a for a in get_args(annotation) if a is not type(None)]
        return _describe_type(inner[0]) if len(inner) == 1 else "value"
    if origin is Literal:
        return "|".join(str(a) for a in get_args(annotation))
    if annotation is bool:
        return "bool"
    if annotation is str:
        return "text"
    if annotation in (int, float):
        return "number"
    if annotation is list or origin is list:
        return "list"
    return "object"


def build_field_guide(node_classes: Mapping[str, type[BaseModel]]) -> str:
    """One line per node type: its `data` keys (camelCase), `*` = required.

    Generated from the Pydantic models so the prompt cannot drift from what
    create_node actually validates.
    """
    lines: list[str] = []
    for node_type in sorted(node_classes):
        data_cls = node_classes[node_type].model_fields["data"].annotation
        if not (isinstance(data_cls, type) and issubclass(data_cls, BaseModel)):
            continue
        fields = [
            f"{info.alias or name}{'*' if info.is_required() else ''} "
            f"({_describe_type(info.annotation)})"
            for name, info in data_cls.model_fields.items()
            if (info.alias or name) not in _GUIDE_SKIP
        ]
        lines.append(f"- `{node_type}`: " + (", ".join(fields) or "no fields"))
    return "\n".join(lines)


def describe_models(catalog: Mapping[str, str | None] | None) -> str:
    if not catalog:
        return "(none listed: leave `model` unset so the deployment default is used)"
    return "\n".join(
        f"- `{model_id}`" + (f" ({label})" if label else "")
        for model_id, label in sorted(catalog.items())
    )
```

(Merge the new imports into the module's import block. If `test_field_guide_marks_required...` fails because an alias differs, e.g. decision's `instruction`, read the field in `src/engine/workflow.py` and fix the *test expectation*, not the generator.)

In `src/builder/prompts.py`:
- Import `NODE_CLASSES`, `build_field_guide`, `describe_models`, `Mapping`.
- Replace the whole `Useful node fields:` block (its five bullet lines included) with:

```
Fill every node completely; a node with empty settings does nothing at run time:
- agent / mcp / jira: write `instructions` saying exactly what the step does,
  referencing inputs with {{{{name}}}}.
- guardrails: turn on at least one check (piiEnabled, moderationEnabled,
  jailbreakEnabled, hallucinationEnabled).
- Set `model` on agent and mcp nodes to one of the available models below.
- Every {{{{name}}}} you use must be a start input (set_start_inputs),
  {{{{lastOutput}}}}, or a node id with '-' written as '_'.
- After you finish, an automatic check lists anything left unfilled; fix
  each item it reports.

Node `data` fields (`*` = required; use these exact keys):
{fields}

Available models for `model`:
{models}
```

- Change `build_system_prompt`:

```python
def build_system_prompt(
    canvas: str = "(empty)", models: Mapping[str, str | None] | None = None
) -> str:
    return SYSTEM_PROMPT_BUILDER.format(
        catalog=build_node_catalog(),
        canvas=canvas,
        fields=build_field_guide(NODE_CLASSES),
        models=describe_models(models),
    )
```

Rename `_enabled_catalog` → `enabled_model_catalog` in `model_policy.py` (no behaviour change).

- [ ] **Step 4: Run, verify pass**

Run: `.venv/Scripts/python -m pytest tests/unit/builder -q --no-cov -p no:cacheprovider` → all pass.

- [ ] **Step 5: Commit** (`feat(builder): tell the builder which fields and models exist`)

---

### Task 4: Repair rounds, plus tests where the model misbehaves

**Files:**
- Modify: `src/builder/builder_agent.py`, `src/api/builder.py`
- Test: `tests/unit/builder/test_builder_agent.py`

These tests drive the **real** tool loop with the existing `_ToolCallingModel` fake, scripted to do what real models did. They are the tests that would have caught 2026-11-19 before release.

- [ ] **Step 1: Let the fake record what it was sent**

Replace `_ToolCallingModel` in `tests/unit/builder/test_builder_agent.py`:

```python
class _ToolCallingModel:
    """Replays scripted AIMessages so the real tool loop runs end to end.

    Records every message list it receives (`self.seen`), so tests can assert
    what the builder fed back. Once the script runs out it answers with plain
    text, which ends the tool loop.
    """

    def __init__(self, script: list[AIMessage]) -> None:
        self._script = list(script)
        self.seen: list[list[Any]] = []

    async def ainvoke(self, messages: Any, **kwargs: Any) -> AIMessage:
        self.seen.append(list(messages))
        return self._script.pop(0) if self._script else AIMessage(content="(script exhausted)")

    def bind_tools(self, tools: Any) -> _ToolCallingModel:
        return self
```

In `test_build_turn_repairs_a_second_start_and_disconnected_end`, give the agent instructions, so the new checks don't start a repair round and change the asserted reply: add `"data": {"instructions": "Search the web"}` to the `create_node` args for `search`.

Add a helper:

```python
async def _build(script: list[AIMessage], workflow: Workflow | None = None, **kw: Any):
    model = _ToolCallingModel(script)
    session = BuildSession(workflow_id="w1")
    session.pending_plan = {"stages": []}
    events, reply, wf = await run_builder_turn(
        model=cast("BaseChatModel", model),
        workflow_id="w1",
        workflow=workflow or _empty_workflow(),
        session=session,
        message="approve the plan",
        execution_id=None,
        db=None,
        **kw,
    )
    return model, events, reply, wf
```

- [ ] **Step 2: Write the failing misbehaving-model tests**

```python
async def test_model_reconnecting_visible_edges_leaves_no_duplicates() -> None:
    """2026-11-19: the model re-added edges it saw in the canvas listing."""
    script = [
        AIMessage(content="", tool_calls=[  # pyright: ignore[reportArgumentType]
            _call("create_node", {"node_type": "agent", "node_id": "a", "label": "A",
                                  "data": {"instructions": "go"}}, "1"),
            _call("connect_nodes", {"source": "s", "target": "a"}, "2"),
            _call("connect_nodes", {"source": "a", "target": "e"}, "3"),
            _call("connect_nodes", {"source": "a", "target": "e"}, "4"),
            _call("connect_nodes", {"source": "s", "target": "a"}, "5"),
        ]),
        AIMessage(content="Done."),
    ]
    _, _, _, wf = await _build(script)
    pairs = [(e.source, e.target, e.branch) for e in wf.edges]
    assert len(pairs) == len(set(pairs))
    assert collect_workflow_violations(wf) == []


async def test_stored_duplicates_are_gone_after_any_build_turn() -> None:
    base = _empty_workflow()
    base = base.model_copy(update={"edges": [*base.edges, WorkflowEdge(id="dup", source="s", target="e")]})
    _, _, _, wf = await _build([AIMessage(content="Nothing to change.")], workflow=base)
    assert [e.id for e in wf.edges] == ["e1"]


async def test_bare_node_is_sent_back_and_the_fix_is_kept() -> None:
    """The model creates an agent with no data; the check makes it fill one in."""
    script = [
        AIMessage(content="", tool_calls=[  # pyright: ignore[reportArgumentType]
            _call("create_node", {"node_type": "agent", "node_id": "a", "label": "A"}, "1"),
            _call("connect_nodes", {"source": "s", "target": "a"}, "2"),
        ]),
        AIMessage(content="Built it."),
        AIMessage(content="", tool_calls=[  # pyright: ignore[reportArgumentType]
            _call("update_node", {"node_id": "a", "data": {"instructions": "Summarise the input"}}, "3"),
        ]),
        AIMessage(content="Filled in the agent."),
    ]
    model, events, reply, wf = await _build(script)
    feedback = [m.content for m in model.seen[2] if isinstance(m, HumanMessage)][-1]
    assert "'a'" in feedback and "instructions" in feedback
    agent = next(n for n in wf.nodes if n.id == "a")
    assert agent.data.instructions == "Summarise the input"  # pyright: ignore[reportAttributeAccessIssue]
    assert reply == "Filled in the agent."
    assert not any("Remaining issues" in str(e.get("content")) for e in events)


async def test_model_that_never_fixes_gets_bounded_rounds_then_user_sees_issues() -> None:
    script = [
        AIMessage(content="", tool_calls=[  # pyright: ignore[reportArgumentType]
            _call("create_node", {"node_type": "guardrails", "node_id": "pii", "label": "PII"}, "1"),
        ]),
        AIMessage(content="Built."),
    ]
    model, events, _, _ = await _build(script)
    # 1 build call + MAX_REPAIR_ROUNDS repair calls, then stop.
    assert len(model.seen) == 1 + 1 + MAX_REPAIR_ROUNDS
    remaining = [e["content"] for e in events if e.get("type") == "message"]
    assert any("Remaining issues" in c and "pii" in c and "piiEnabled" in c for c in remaining)


async def test_undeclared_variable_is_reported_back_to_the_model() -> None:
    script = [
        AIMessage(content="", tool_calls=[  # pyright: ignore[reportArgumentType]
            _call("create_node", {"node_type": "agent", "node_id": "a", "label": "A",
                                  "data": {"instructions": "Research {{topic}}"}}, "1"),
        ]),
        AIMessage(content="Built."),
    ]
    model, _, _, _ = await _build(script)
    feedback = [m.content for m in model.seen[2] if isinstance(m, HumanMessage)][-1]
    assert "topic" in feedback and "set_start_inputs" in feedback


async def test_model_catalog_reaches_the_prompt_and_the_check() -> None:
    script = [
        AIMessage(content="", tool_calls=[  # pyright: ignore[reportArgumentType]
            _call("create_node", {"node_type": "agent", "node_id": "a", "label": "A",
                                  "data": {"instructions": "go"}}, "1"),
        ]),
        AIMessage(content="Built."),
    ]
    model, _, _, _ = await _build(script, model_catalog={"anthropic/claude-sonnet-4-5": "Sonnet"})
    system = model.seen[0][0].content
    assert "`anthropic/claude-sonnet-4-5`" in system
    feedback = [m.content for m in model.seen[2] if isinstance(m, HumanMessage)][-1]
    assert "model" in feedback


async def test_requested_credential_silences_the_mcp_server_issue() -> None:
    script = [
        AIMessage(content="", tool_calls=[  # pyright: ignore[reportArgumentType]
            _call("create_node", {"node_type": "mcp", "node_id": "m", "label": "Search",
                                  "data": {"instructions": "search the web"}}, "1"),
            _call("request_credential", {"kind": "mcp-server", "node_id": "m",
                                         "detail": "a Tavily MCP server"}, "2"),
        ]),
        AIMessage(content="Built; connect a Tavily MCP server."),
    ]
    model, events, _, _ = await _build(script)
    assert len(model.seen) == 2  # no repair round
    assert any(e.get("type") == "credential_request" for e in events)
```

Imports to add at the top: `from langchain_core.messages import AIMessage, HumanMessage` and `from src.builder.builder_agent import MAX_REPAIR_ROUNDS`.

`model.seen[2]` is the first repair call. Calls are: [0] build turn, model emits tool calls; [1] same loop, model says "Built it."; [2] first repair round.

- [ ] **Step 3: Run, verify failure**

Run: `.venv/Scripts/python -m pytest tests/unit/builder/test_builder_agent.py -q --no-cov -p no:cacheprovider`
Expected: FAIL with `ImportError: cannot import name 'MAX_REPAIR_ROUNDS'` (and `model_catalog` unexpected keyword once that's added).

- [ ] **Step 4: Implement in `src/builder/builder_agent.py`**

```python
from collections.abc import Mapping

from src.builder.completeness import collect_completeness_issues

MAX_REPAIR_ROUNDS = 2

ModelCatalog = Mapping[str, str | None]
```

Thread the catalog into prompts:

```python
def _system_messages(workflow: Workflow, models: ModelCatalog | None) -> list[BaseMessage]:
    canvas = describe_canvas(workflow.nodes, workflow.edges)
    return [SystemMessage(content=build_system_prompt(canvas, models))]


def _draft_system_messages(draft: Draft, models: ModelCatalog | None) -> list[BaseMessage]:
    nodes, edges = draft.snapshot()
    return [SystemMessage(content=build_system_prompt(describe_canvas(nodes, edges), models))]
```

`_tool_loop(model, ctx, session, models: ModelCatalog | None = None)` passes `models` to `_draft_system_messages`. The planning call uses `_system_messages(workflow, model_catalog)`, and the diagnose path's `_tool_loop` gets `model_catalog` too.

Add:

```python
def _turn_issues(workflow: Workflow, ctx: BuilderContext, models: ModelCatalog | None) -> list[str]:
    requested = {
        str(e.get("node_id")) for e in ctx.events if e.get("type") == "credential_request"
    }
    return [
        *collect_workflow_violations(workflow),
        *collect_completeness_issues(
            workflow, model_catalog=models, credential_requested=requested
        ),
    ]


def _repair_prompt(issues: list[str]) -> str:
    listed = "\n".join(f"- {i}" for i in issues)
    return (
        "Automatic check: the workflow is not finished. Fix each item with the "
        f"tools, then reply with a one-line summary.\n{listed}"
    )
```

`run_builder_turn` gains the keyword `model_catalog: ModelCatalog | None = None`. Replace the build turn (section `# 4. Build turn.`) from `reply = await _tool_loop(...)` through the `if violations:` block with:

```python
    reply = await _tool_loop(model, ctx, session, model_catalog)
    normalize_orchestration(ctx.draft)
    updated = _draft_to_workflow(workflow, ctx.draft)
    issues = _turn_issues(updated, ctx, model_catalog)
    for _ in range(MAX_REPAIR_ROUNDS):
        if not issues:
            break
        session.messages.append(AIMessage(content=reply))
        session.messages.append(HumanMessage(content=_repair_prompt(issues)))
        reply = await _tool_loop(model, ctx, session, model_catalog)
        normalize_orchestration(ctx.draft)
        updated = _draft_to_workflow(workflow, ctx.draft)
        issues = _turn_issues(updated, ctx, model_catalog)
    if issues:
        events.append({"type": "message", "content": "Remaining issues: " + "; ".join(issues[:5])})
```

Keep the rest unchanged (`events.extend(ctx.events)`, `stage_applied`, the final `session.messages.append(AIMessage(content=reply))`). The feedback is a `HumanMessage`, not a `SystemMessage`, because Anthropic only accepts a system prompt at the start of the conversation.

In `src/api/builder.py`, before `run_builder_turn`:

```python
    model_catalog = await enabled_model_catalog(db)
```

and pass `model_catalog=model_catalog` (import from `src.builder.model_policy`).

- [ ] **Step 5: Run, verify pass**

Run: `.venv/Scripts/python -m pytest tests/unit/builder tests/unit/api -q --no-cov -p no:cacheprovider`
Expected: all pass. If an existing builder or route test now fails only because a scripted model creates an empty node (it gets a repair round and a "Remaining issues" event), give that node the data it needs (e.g. `instructions`) in the test. Don't loosen the new checks. If a route test mocks `db` without `llmmodel`, add `llmmodel.find_many` returning `[]` to its fake.

- [ ] **Step 6: Lint/type/format, then commit** (`feat(builder): send unfilled nodes back to the model before finishing`)

---

### Task 5: Live check against a real model (weekly, not per-PR)

**Files:**
- Create: `tests/integration/test_builder_live.py`

This runs under `live-provider-smoke.yml` (`pytest -m live`, Mondays + manual), which already has `ANTHROPIC_API_KEY`. No workflow change needed. It measures whether the builder *in practice* produces complete workflows: a canary for prompt or model regressions, not a proof.

- [ ] **Step 1: Write the test**

```python
"""Live quality check for "Build with AI" against the real default model.

Runs weekly via live-provider-smoke.yml (`-m live`), never per PR. Each case
is a realistic request; the result must have no duplicate edges, pass
structural validation and pass every completeness check. A failure means the
builder ships half-filled workflows with the current prompt/model.
"""

import os

import pytest

from src.builder.builder_agent import BuildSession, run_builder_turn
from src.builder.completeness import collect_completeness_issues
from src.builder.model_policy import DEFAULT_BUILDER_MODEL
from src.engine.graph_builder import collect_workflow_violations
from src.engine.workflow import (
    EndNode, EndNodeData, Position, StartNode, StartNodeData, Workflow, WorkflowEdge,
)
from src.llm.providers import build_chat_model

pytestmark = [pytest.mark.integration, pytest.mark.live]

CATALOG = {DEFAULT_BUILDER_MODEL: "Default", "anthropic/claude-haiku-4-5-20251001": "Haiku"}

REQUESTS = [
    "Take a topic from the user, research it with an agent and write a 200-word summary",
    "Check the user's text for PII and moderation problems, then rewrite it politely",
    "Search the web for a topic using a Tavily MCP server and summarise the findings",
    "Classify a support ticket as billing or technical and draft a reply for each case",
    "Extract the company name and amount from an invoice text and ask a human to approve",
]


def _canvas() -> Workflow:
    return Workflow(
        name="live",
        nodes=[
            StartNode(id="start-1", type="start", position=Position(x=0, y=0), data=StartNodeData(label="Start")),
            EndNode(id="end-1", type="end", position=Position(x=400, y=0), data=EndNodeData(label="End")),
        ],
        edges=[WorkflowEdge(id="e1", source="start-1", target="end-1")],
    )


@pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY not set")
@pytest.mark.parametrize("request_text", REQUESTS)
async def test_builder_produces_a_complete_workflow(request_text: str) -> None:
    model = build_chat_model(DEFAULT_BUILDER_MODEL)
    session = BuildSession(workflow_id="live")
    workflow = _canvas()
    events: list[dict[str, object]] = []
    for message in (request_text, "approve the plan"):
        turn_events, _, workflow = await run_builder_turn(
            model=model, workflow_id="live", workflow=workflow, session=session,
            message=message, execution_id=None, db=None, model_catalog=CATALOG,
        )
        events.extend(turn_events)

    requested = {str(e.get("node_id")) for e in events if e.get("type") == "credential_request"}
    pairs = [(e.source, e.target, e.branch) for e in workflow.edges]
    assert len(pairs) == len(set(pairs)), f"duplicate edges: {pairs}"
    assert collect_workflow_violations(workflow) == []
    assert collect_completeness_issues(
        workflow, model_catalog=CATALOG, credential_requested=requested
    ) == []
```

If `build_chat_model` needs keys from the database rather than the environment (keys live in Postgres, per [[project_llm_keys_centrally_maintained]]), follow `tests/integration/test_agent_providers.py`'s setup for how live tests get a provider key, and copy that pattern instead of the direct call.

- [ ] **Step 2: Confirm it is collected but skipped in the normal suite**

Run: `.venv/Scripts/python -m pytest tests/integration/test_builder_live.py -q --no-cov -p no:cacheprovider -m "not live"`
Expected: `5 deselected`. Per-PR CI never runs it.

- [ ] **Step 3 (optional, costs ~5 Sonnet build turns): run it once for real**

Only with the user's OK: `.venv/Scripts/python -m pytest tests/integration/test_builder_live.py -m live -q --no-cov -p no:cacheprovider -rs`. Report pass/fail per request. A failing request is a finding about the prompt, so record it in the PR description; don't weaken the assertions to make it pass.

- [ ] **Step 4: Commit** (`test(builder): weekly live check that real models fill in nodes`)

---

### Task 6: Playwright race test (delete an edge, message the builder at once)

**Files:**
- Create: `frontend/e2e/builder-race.spec.ts`
- Modify: `.github/workflows/ci.yml:394` (append the spec to the e2e smoke list; this plan authorizes that CI edit)

The seeded workflow is `start-1 → end-1` plus `start-1 → note-1`. Edges into a note are ignored by the end-node and reachability rules, so deleting that edge leaves a valid graph and the save succeeds. Deleting the only `start → end` edge instead would make the save fail with 422, which the panel correctly refuses to build on; the second test covers that.

- [ ] **Step 1: Write the spec**

```ts
// 2026-11-19 defect: the builder reads the workflow from the database while
// canvas edits autosave on a 3 s debounce. Deleting an edge and messaging the
// builder inside that window made it rebuild from (and save over) the stale
// graph, so the deleted edge came back. The panel must save first.
import { test, expect, type Page } from "@playwright/test";
import { createTestUser, cleanupTestUsers, type TestUser } from "./fixtures/test-user";

const apiUrl = process.env.NEXT_PUBLIC_COMPOSER_API_URL ?? "http://localhost:8000";

test.afterEach(async () => {
  await cleanupTestUsers();
});

async function loginAs(page: Page, user: TestUser) {
  await page.goto("/login");
  await page.fill('input[type="email"]', user.email);
  await page.fill('input[type="password"]', user.password);
  await page.click('button[type="submit"]');
  await expect(page).toHaveURL(/\/runs/, { timeout: 15_000 });
}

async function seedWorkflow(page: Page, user: TestUser, edges: object[]): Promise<string> {
  const res = await page.request.post(`${apiUrl}/workflows`, {
    headers: { Authorization: `Bearer ${user.accessToken}` },
    data: {
      name: "PW Builder Race",
      isTemplate: false,
      isPublic: false,
      nodes: [
        { id: "start-1", type: "start", position: { x: 0, y: 0 }, data: { label: "Start" } },
        { id: "end-1", type: "end", position: { x: 400, y: 0 }, data: { label: "End" } },
        { id: "note-1", type: "note", position: { x: 200, y: 200 }, data: { label: "Note" } },
      ],
      edges,
    },
  });
  expect(res.status()).toBe(201);
  return ((await res.json()) as { id: string }).id;
}

test("unsaved edge deletion is saved before the builder runs", async ({ page }) => {
  const user = await createTestUser();
  const id = await seedWorkflow(page, user, [
    { id: "e1", source: "start-1", target: "end-1" },
    { id: "e-note", source: "start-1", target: "note-1" },
  ]);
  const order: string[] = [];
  let savedEdges: unknown[] | null = null;
  page.on("request", (req) => {
    if (req.method() === "PUT" && new RegExp(`/workflows/${id}$`).test(req.url())) {
      order.push("save");
      savedEdges = (req.postDataJSON() as { edges: unknown[] }).edges;
    }
    if (req.method() === "POST" && req.url().includes(`/builder/${id}/message`)) {
      order.push("builder");
    }
  });
  await page.route(`**/builder/${id}/message`, (route) =>
    route.fulfill({
      json: { sessionId: "s", events: [], reply: "noted", workflow: null, validation_errors: [] },
    })
  );

  await loginAs(page, user);
  await page.goto(`/designer/${id}`);
  const noteEdge = page.locator('.react-flow__edge[data-id="e-note"]');
  await expect(noteEdge).toBeVisible({ timeout: 15_000 });
  await noteEdge.click({ force: true });
  await page.keyboard.press("Delete");

  // Well inside the 3 s autosave debounce.
  const input = page.getByPlaceholder("Describe your workflow…");
  await input.fill("tidy up the edges");
  await input.press("Enter");
  await expect(page.getByText("noted")).toBeVisible({ timeout: 10_000 });

  expect(order[0]).toBe("save");
  expect(order).toContain("builder");
  expect(savedEdges).toHaveLength(1);
});

test("a failed save blocks the builder instead of building on stale data", async ({ page }) => {
  const user = await createTestUser();
  const id = await seedWorkflow(page, user, [{ id: "e1", source: "start-1", target: "end-1" }]);
  let builderCalled = false;
  await page.route(`**/builder/${id}/message`, (route) => {
    builderCalled = true;
    return route.fulfill({ json: { sessionId: "s", events: [], reply: "x", workflow: null, validation_errors: [] } });
  });

  await loginAs(page, user);
  await page.goto(`/designer/${id}`);
  const edge = page.locator('.react-flow__edge[data-id="e1"]');
  await expect(edge).toBeVisible({ timeout: 15_000 });
  await edge.click({ force: true });
  await page.keyboard.press("Delete"); // End now has no incoming edge: save gets 422

  const input = page.getByPlaceholder("Describe your workflow…");
  await input.fill("rebuild it");
  await input.press("Enter");
  await expect(page.getByText(/couldn't save your latest canvas edits/i)).toBeVisible({ timeout: 10_000 });
  expect(builderCalled).toBe(false);
});
```

If React Flow in this version doesn't put `data-id` on `.react-flow__edge`, select by the edge's `aria-label` or `[data-testid="rf__edge-e-note"]`. Inspect the DOM once with `npx playwright test e2e/builder-race.spec.ts --debug` and use what's there.

- [ ] **Step 2: Run against the local stack**

Run (backend on :8000 and frontend dev server up, as `frontend/README.md` describes for e2e): `cd frontend && npx playwright test e2e/builder-race.spec.ts`
Expected: 2 passed. To prove the test bites, revert the `beforeSend` line in `frontend/app/designer/[workflowId]/page.tsx` locally: test 1 must fail on `order[0]` and test 2 on `builderCalled`. Then restore it.

- [ ] **Step 3: Add to CI**

`.github/workflows/ci.yml:394`:

```yaml
        run: npx playwright test e2e/auth.spec.ts e2e/security-headers.spec.ts e2e/builder-race.spec.ts
```

- [ ] **Step 4: Commit** (`test(e2e): delete-then-ask race for Build with AI`)

---

### Task 7: Docs in lockstep

**Files:** `CHANGELOG.md`, `docs/designer-guide.md`, `docs/architecture.md`, `docs/superpowers/specs/2026-11-02-natural-language-workflow-builder-design.md`

Coordinate first: Codex may have a docs PR open for `2b073c2` touching the same files (`gh pr list`). Rebase on it or wait for it, so the two don't conflict.

- [ ] **Step 1:** In `CHANGELOG.md` under `[Unreleased]`, add `### Added — Build with AI fills in nodes completely (<date>)` with bullets for: the field guide and model list in the prompt; the automatic check and up to 2 repair rounds; "Remaining issues" shown to the user; duplicates collapsed on save/run (not rejected, and why); the weekly live check; the e2e race test.
- [ ] **Step 2:** `docs/designer-guide.md`, Build with AI section: what "Remaining issues" means and that the builder picks models from the admin-enabled list.
- [ ] **Step 3:** `docs/architecture.md` and the builder design spec: structural rules vs. content rules, and why content rules never block saves.
- [ ] **Step 4:** Commit (`docs: builder completeness checks`).

---

### Task 8: Verify and hand off

- [ ] Run the full gate: `ruff check`, `ruff format --check`, `pyright src tests`, `pytest` (whole suite), and in `frontend/`: `npx vitest run`, `npx tsc --noEmit -p .`, `npx prettier --check .`.
- [ ] Open a PR from `feat/builder-completeness`; wait for all CI checks (the e2e smoke job now includes the race spec).
- [ ] Ask the user before merging. Merging to `main` deploys to Cloud Run automatically.

---

## Self-review notes

- Spec coverage: field guide (Task 3), model list (Task 3), completeness check that feeds back (Tasks 2 and 4), validation rules on all paths (Task 1), misbehaving-model turn tests (Task 4), live check (Task 5), Playwright race (Task 6), docs (Task 7).
- Deliberately excluded: making content rules block saves or runs (it would break half-built canvases); model checks on decision/guardrails/jira (their `model` format isn't confirmed to match the catalog keys).
- Names used across tasks: `dedupe_edges`, `collect_completeness_issues(workflow, *, model_catalog, credential_requested)`, `NODE_CLASSES`, `build_field_guide`, `describe_models`, `enabled_model_catalog`, `MAX_REPAIR_ROUNDS`, `run_builder_turn(..., model_catalog=)`.
