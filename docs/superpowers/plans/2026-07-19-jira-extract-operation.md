# Jira Extract Operation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic `operation: "extract"` mode to the existing `jira` node type — paginated, field-explicit, no-LLM bulk JQL search — alongside the current agentic `operation: "agent"` mode (the default, unchanged).

**Architecture:** One new `Literal["agent", "extract"]` field on `JiraNodeData` picks the code path inside the same executor class. `extract` mode never touches an LLM; it loops `POST /rest/api/3/search` with `startAt` pagination until Jira's `total` is satisfied or a configured `maxIssues` cap is hit, with bounded retry on 429/5xx. Output is the raw per-issue field JSON (plus optional changelog) — this executor does not shape data for any specific customer's business rules. The Designer panel gets an Operation toggle mirroring the existing `vector-db` node's `query`/`upsert` pattern.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, httpx, pytest + pytest-httpx (backend). Next.js/React, vitest + @testing-library/react (frontend).

**Spec:** `docs/superpowers/specs/2026-07-19-jira-confluence-foundation-design.md` (Component 1).

---

## Scope note (platform vs. customer flow)

Per [[feedback_platform_vs_customer_flow]] — this plan adds a **generic** capability. Nothing here hardcodes any customer's JQL, project key, or field IDs (e.g. Macy's `customfield_10026`). The `fields` list has no default at all; every workflow supplies its own.

---

### Task 0: Make the Jira REST helpers reusable across modules

**Why:** the new extract-mode code (Task 2) needs to build the same `Authorization: Basic ...` header and `https://{domain}/rest/api/3/{path}` URL that `src/tools/providers/jira.py` already builds internally via `_build_headers`/`_build_url`. Those are currently module-private (leading underscore) — under this repo's `pyright strict` mode, importing an underscore-prefixed name from another module triggers `reportPrivateUsage`. Renaming them to public names is the correct fix (not a private-import workaround), since they're now genuinely used from two modules.

**Files:**
- Modify: `src/tools/providers/jira.py:66,75,181-182,223-224,294-295,358-359,364-365,389-390,426-427,470-471,546-547`

- [ ] **Step 1: Rename `_build_headers` → `build_headers` and `_build_url` → `build_url`**

In `src/tools/providers/jira.py`, at the two definitions (currently lines 66 and 75):

```python
def build_headers(email: str, api_token: str) -> dict[str, str]:
    encoded = base64.b64encode(f"{email}:{api_token}".encode()).decode()
    return {
        "Authorization": f"Basic {encoded}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def build_url(domain: str, path: str) -> str:
    return f"https://{domain}/rest/api/3/{path}"
```

Then update every call site in the same file (12 occurrences) — each `_build_headers(` becomes `build_headers(` and each `_build_url(` becomes `build_url(`. The safest way to do this is two find-and-replace-all passes over the whole file (there are no other identifiers containing these substrings in this file):

- Replace all `_build_headers(` → `build_headers(`
- Replace all `_build_url(` → `build_url(`

- [ ] **Step 2: Run the existing Jira provider/executor test suite to confirm the rename didn't break anything**

Run: `uv run pytest tests/unit/tools/providers/test_jira.py tests/unit/executors/test_jira_executor.py -v`
Expected: PASS (all existing tests — this is a pure rename, no behavior change)

- [ ] **Step 3: Commit**

```bash
git add src/tools/providers/jira.py
git commit -m "$(cat <<'EOF'
refactor(jira): make REST helpers public so the extract operation can reuse them

build_headers/build_url were module-private but are about to be needed by
src/executors/jira.py's new deterministic extract mode. Renaming (not a
cross-module private import) keeps pyright strict mode clean.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 1: Schema — add `operation` and extract-mode fields to `JiraNodeData`

**Files:**
- Modify: `src/engine/workflow.py:533-548`
- Test: `tests/unit/executors/test_jira_executor.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/executors/test_jira_executor.py` (after the existing `test_camelcase_api_token_populates_field` function):

```python
def test_operation_defaults_to_agent_for_existing_workflows() -> None:
    """Every workflow saved before this field existed has no `operation` key
    at all — it must keep behaving exactly as before (agentic tool loop)."""
    node = JiraNode.model_validate(_jira_node_json())
    assert node.data.operation == "agent"


def test_extract_operation_fields_parse_with_camelcase_aliases() -> None:
    node = JiraNode.model_validate(
        _jira_node_json(
            operation="extract",
            jql="project = MB",
            fields=["summary", "status"],
            expandChangelog=False,
            maxIssues=250,
        )
    )
    assert node.data.operation == "extract"
    assert node.data.jql == "project = MB"
    assert node.data.fields == ["summary", "status"]
    assert node.data.expand_changelog is False
    assert node.data.max_issues == 250


def test_extract_mode_field_defaults() -> None:
    node = JiraNode.model_validate(_jira_node_json(operation="extract", jql="project = MB"))
    assert node.data.fields is None
    assert node.data.expand_changelog is True
    assert node.data.max_issues == 1000
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/executors/test_jira_executor.py -k "operation_defaults or extract_operation_fields or extract_mode_field_defaults" -v`
Expected: FAIL — `AttributeError: 'JiraNodeData' object has no attribute 'operation'` (the field doesn't exist yet; `extra="allow"` on `BaseNodeData` means unknown input keys are silently stored but not exposed as typed attributes)

- [ ] **Step 3: Add the fields to `JiraNodeData`**

In `src/engine/workflow.py`, replace the `JiraNodeData` class (currently lines 533-548):

```python
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
    max_issues: int = Field(default=1000, alias="maxIssues")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/executors/test_jira_executor.py -v`
Expected: PASS (all tests, including the pre-existing ones — this is a pure additive schema change)

- [ ] **Step 5: Commit**

```bash
git add src/engine/workflow.py tests/unit/executors/test_jira_executor.py
git commit -m "$(cat <<'EOF'
feat(jira-node): add operation field and extract-mode schema

Adds operation: agent|extract (default agent, preserving existing
workflows) plus jql/fields/expandChangelog/maxIssues, only meaningful in
extract mode. No executor behavior change yet — that's the next task.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Executor — implement deterministic `extract` operation

**Files:**
- Modify: `src/executors/jira.py`
- Test: `tests/unit/executors/test_jira_executor.py`

- [ ] **Step 1: Write the failing tests**

Add these imports at the top of `tests/unit/executors/test_jira_executor.py` (alongside the existing ones):

```python
import json

from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]
```

Append these test functions:

```python
async def test_extract_paginates_across_multiple_pages(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        url="https://test.atlassian.net/rest/api/3/search",
        method="POST",
        json={
            "total": 3,
            "issues": [
                {"key": "MB-1", "fields": {"summary": "One"}},
                {"key": "MB-2", "fields": {"summary": "Two"}},
            ],
        },
    )
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        url="https://test.atlassian.net/rest/api/3/search",
        method="POST",
        json={"total": 3, "issues": [{"key": "MB-3", "fields": {"summary": "Three"}}]},
    )
    node = JiraNode.model_validate(
        _jira_node_json(operation="extract", jql="project = MB", fields=["summary"])
    )
    delta = await JiraExecutor(node).arun(initial_state())
    output = delta["variables"]["lastOutput"]
    assert [i["key"] for i in output["issues"]] == ["MB-1", "MB-2", "MB-3"]
    assert output["total"] == 3
    assert output["fetched"] == 3
    assert output["truncated"] is False


async def test_extract_stops_at_max_issues_and_flags_truncated(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        url="https://test.atlassian.net/rest/api/3/search",
        method="POST",
        json={"total": 500, "issues": [{"key": f"MB-{i}", "fields": {}} for i in range(100)]},
    )
    node = JiraNode.model_validate(
        _jira_node_json(
            operation="extract", jql="project = MB", fields=["summary"], maxIssues=100
        )
    )
    delta = await JiraExecutor(node).arun(initial_state())
    output = delta["variables"]["lastOutput"]
    assert output["fetched"] == 100
    assert output["truncated"] is True


async def test_extract_passes_expand_changelog_when_enabled(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        url="https://test.atlassian.net/rest/api/3/search", method="POST", json={"total": 0, "issues": []}
    )
    node = JiraNode.model_validate(
        _jira_node_json(operation="extract", jql="project = MB", fields=["summary"], expandChangelog=True)
    )
    await JiraExecutor(node).arun(initial_state())
    req = httpx_mock.get_request()  # pyright: ignore[reportUnknownMemberType]
    assert req is not None
    body = json.loads(req.content)
    assert body["expand"] == ["changelog"]


async def test_extract_omits_expand_when_changelog_disabled(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        url="https://test.atlassian.net/rest/api/3/search", method="POST", json={"total": 0, "issues": []}
    )
    node = JiraNode.model_validate(
        _jira_node_json(
            operation="extract", jql="project = MB", fields=["summary"], expandChangelog=False
        )
    )
    await JiraExecutor(node).arun(initial_state())
    req = httpx_mock.get_request()  # pyright: ignore[reportUnknownMemberType]
    assert req is not None
    body = json.loads(req.content)
    assert "expand" not in body


async def test_extract_retries_on_429_then_succeeds(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.executors import jira as jira_executor_module

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(jira_executor_module.asyncio, "sleep", _no_sleep)

    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        url="https://test.atlassian.net/rest/api/3/search",
        method="POST",
        status_code=429,
        text="rate limited",
    )
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        url="https://test.atlassian.net/rest/api/3/search",
        method="POST",
        json={"total": 1, "issues": [{"key": "MB-1", "fields": {}}]},
    )
    node = JiraNode.model_validate(
        _jira_node_json(operation="extract", jql="project = MB", fields=["summary"])
    )
    delta = await JiraExecutor(node).arun(initial_state())
    assert delta["variables"]["lastOutput"]["fetched"] == 1


async def test_extract_raises_after_exhausting_retries(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.executors import jira as jira_executor_module
    from src.executors.jira import JiraExtractHttpError

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(jira_executor_module.asyncio, "sleep", _no_sleep)

    for _ in range(3):
        httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
            url="https://test.atlassian.net/rest/api/3/search",
            method="POST",
            status_code=503,
            text="unavailable",
        )
    node = JiraNode.model_validate(
        _jira_node_json(operation="extract", jql="project = MB", fields=["summary"])
    )
    with pytest.raises(JiraExtractHttpError):
        await JiraExecutor(node).arun(initial_state())


async def test_extract_requires_jql() -> None:
    from src.executors.jira import JiraExtractConfigError

    node = JiraNode.model_validate(_jira_node_json(operation="extract", jql=""))
    with pytest.raises(JiraExtractConfigError):
        await JiraExecutor(node).arun(initial_state())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/executors/test_jira_executor.py -k extract -v`
Expected: FAIL — `ImportError: cannot import name 'JiraExtractHttpError'` / `JiraExtractConfigError` (don't exist yet), and the pagination/retry tests fail because `arun()` doesn't branch on `operation` yet.

- [ ] **Step 3: Implement the extract operation**

In `src/executors/jira.py`, update the imports at the top of the file — replace:

```python
from src.tools.base import BuildContext
from src.tools.providers.jira import JiraProvider
from src.variable_substitution import substitute
```

with:

```python
import asyncio

import httpx

from src.tools.base import BuildContext
from src.tools.providers.jira import JiraProvider, build_headers, build_url
from src.variable_substitution import substitute
```

Add these constants and exception classes right after the existing `_CREATED_ISSUE_PATTERN` definition:

```python
_EXTRACT_PAGE_SIZE = 100
_EXTRACT_MAX_RETRY_ATTEMPTS = 3


class JiraExtractConfigError(RuntimeError):
    """Raised when operation='extract' is missing required config (domain/email/apiToken/jql)."""


class JiraExtractHttpError(RuntimeError):
    """Raised when the Jira search REST call fails after exhausting retries, or fails
    with a non-retryable 4xx status."""
```

Rename the existing `async def arun(self, state: ...)` method on `JiraExecutor` to `_run_agent`, keeping its body byte-for-byte identical. Then add a new `arun` that dispatches on `operation`, and the new `_run_extract` method:

```python
    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        if self.node.data.operation == "extract":
            return await self._run_extract(state)
        return await self._run_agent(state)

    async def _run_extract(self, state: WorkflowStateDict) -> dict[str, Any]:
        domain = self.node.data.domain or ""
        email = self.node.data.email or ""
        api_token = decrypt_jira_api_token(self.node.data.api_token or "")
        jql = substitute(self.node.data.jql or "", state)
        if not all([domain, email, api_token, jql]):
            raise JiraExtractConfigError(
                f"jira node {self.node.id!r}: operation='extract' requires domain, email, "
                "apiToken, and jql to all be set."
            )
        fields = self.node.data.fields or []
        expand_changelog = self.node.data.expand_changelog
        max_issues = self.node.data.max_issues

        issues: list[dict[str, Any]] = []
        start_at = 0
        total: int | None = None
        headers = build_headers(email, api_token)
        url = build_url(domain, "search")

        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
            while True:
                remaining = max_issues - len(issues)
                if remaining <= 0:
                    break
                body: dict[str, Any] = {
                    "jql": jql,
                    "startAt": start_at,
                    "maxResults": min(_EXTRACT_PAGE_SIZE, remaining),
                    "fields": fields,
                }
                if expand_changelog:
                    body["expand"] = ["changelog"]
                resp = await _post_search_with_retry(client, url, headers, body)
                data = resp.json()
                page_issues = data.get("issues", [])
                total = data.get("total", len(page_issues))
                for iss in page_issues:
                    entry: dict[str, Any] = {
                        "key": iss.get("key"),
                        "fields": iss.get("fields", {}),
                    }
                    if expand_changelog:
                        entry["changelog"] = iss.get("changelog", {})
                    issues.append(entry)
                start_at += len(page_issues)
                if not page_issues or (total is not None and start_at >= total):
                    break

        truncated = total is not None and len(issues) < total
        output = {
            "issues": issues,
            "total": total if total is not None else len(issues),
            "fetched": len(issues),
            "truncated": truncated,
        }
        return {
            "variables": {"lastOutput": output},
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "input": {"jql": jql, "fields": fields},
                    "output": output,
                }
            },
        }
```

Add the retry helper as a module-level function (near `_run_tool`):

```python
async def _post_search_with_retry(
    client: httpx.AsyncClient, url: str, headers: dict[str, str], body: dict[str, Any]
) -> httpx.Response:
    last_error: JiraExtractHttpError | None = None
    for attempt in range(_EXTRACT_MAX_RETRY_ATTEMPTS):
        try:
            resp = await client.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            last_error = JiraExtractHttpError(f"Jira search request failed: {exc}")
            if attempt < _EXTRACT_MAX_RETRY_ATTEMPTS - 1:
                await asyncio.sleep(2**attempt)
            continue
        if resp.status_code == 429 or resp.status_code >= 500:
            last_error = JiraExtractHttpError(
                f"Jira search failed (HTTP {resp.status_code}): {resp.text[:500]}"
            )
            if attempt < _EXTRACT_MAX_RETRY_ATTEMPTS - 1:
                await asyncio.sleep(2**attempt)
            continue
        if resp.status_code >= 400:
            raise JiraExtractHttpError(
                f"Jira search failed (HTTP {resp.status_code}): {resp.text[:500]}"
            )
        return resp
    assert last_error is not None
    raise last_error
```

Finally, update the `__all__` list at the bottom of the file:

```python
__all__ = [
    "JiraActionPolicyError",
    "JiraExecutor",
    "JiraExtractConfigError",
    "JiraExtractHttpError",
    "JiraMaxIterationsError",
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/executors/test_jira_executor.py -v`
Expected: PASS — all extract-mode tests, plus every pre-existing agent-mode test (unaffected, since `_run_agent`'s body is unchanged and `operation` defaults to `"agent"`)

- [ ] **Step 5: Run the full backend test suite and type check**

Run: `uv run pytest && uv run pyright src tests`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/executors/jira.py tests/unit/executors/test_jira_executor.py
git commit -m "$(cat <<'EOF'
feat(jira-node): implement deterministic extract operation

Paginated, no-LLM JQL search with a maxIssues cap and bounded retry on
429/5xx. Output is raw per-issue field JSON, left unshaped so downstream
transform/data-transform nodes compute customer-specific metrics — this
executor makes no assumption about any tenant's field layout.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Designer panel — operation toggle + extract-mode fields

**Files:**
- Modify: `frontend/components/composer/canvas/node-panels/jira.tsx`
- Test: `frontend/components/composer/canvas/node-panels/jira.test.tsx` (new)

- [ ] **Step 1: Write the failing test**

Create `frontend/components/composer/canvas/node-panels/jira.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import JiraPanel from "./jira";

const { listEnabledLlmModels } = vi.hoisted(() => ({
  listEnabledLlmModels: vi.fn(),
}));

vi.mock("@/lib/api/llm-models", () => ({ listEnabledLlmModels }));

function wrap(children: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  vi.clearAllMocks();
  listEnabledLlmModels.mockResolvedValue([]);
});

describe("JiraPanel — operation toggle", () => {
  it("defaults to agent mode and shows the Prompt field", () => {
    render(wrap(<JiraPanel data={{}} onChange={vi.fn()} />));
    expect(screen.getByText("Prompt")).toBeInTheDocument();
    expect(screen.queryByLabelText("JQL")).not.toBeInTheDocument();
  });

  it("switching to extract mode calls onChange and reveals extract fields", () => {
    const onChange = vi.fn();
    const { rerender } = render(wrap(<JiraPanel data={{}} onChange={onChange} />));
    fireEvent.change(screen.getByLabelText("Operation"), {
      target: { value: "extract" },
    });
    expect(onChange).toHaveBeenCalledWith({ operation: "extract" });

    rerender(wrap(<JiraPanel data={{ operation: "extract" }} onChange={onChange} />));
    expect(screen.getByLabelText("JQL")).toBeInTheDocument();
    expect(screen.queryByText("Prompt")).not.toBeInTheDocument();
  });

  it("editing JQL calls onChange with jql", () => {
    const onChange = vi.fn();
    render(wrap(<JiraPanel data={{ operation: "extract" }} onChange={onChange} />));
    fireEvent.change(screen.getByLabelText("JQL"), {
      target: { value: "project = MB" },
    });
    expect(onChange).toHaveBeenCalledWith({ jql: "project = MB" });
  });

  it("editing the comma-separated fields list calls onChange with an array", () => {
    const onChange = vi.fn();
    render(wrap(<JiraPanel data={{ operation: "extract" }} onChange={onChange} />));
    fireEvent.change(screen.getByLabelText("Fields"), {
      target: { value: "summary, status, priority" },
    });
    expect(onChange).toHaveBeenCalledWith({ fields: ["summary", "status", "priority"] });
  });

  it("toggling expand changelog calls onChange with expandChangelog", () => {
    const onChange = vi.fn();
    render(
      wrap(
        <JiraPanel
          data={{ operation: "extract", expandChangelog: true }}
          onChange={onChange}
        />
      )
    );
    fireEvent.click(screen.getByLabelText("Expand changelog"));
    expect(onChange).toHaveBeenCalledWith({ expandChangelog: false });
  });

  it("editing max issues calls onChange with a number", () => {
    const onChange = vi.fn();
    render(wrap(<JiraPanel data={{ operation: "extract" }} onChange={onChange} />));
    fireEvent.change(screen.getByLabelText("Max issues"), {
      target: { value: "2000" },
    });
    expect(onChange).toHaveBeenCalledWith({ maxIssues: 2000 });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run components/composer/canvas/node-panels/jira.test.tsx`
Expected: FAIL — `getByLabelText("Operation")` not found (panel has no operation toggle yet)

- [ ] **Step 3: Add the operation toggle and extract-mode fields to the panel**

In `frontend/components/composer/canvas/node-panels/jira.tsx`, add `NativeSelect` and `Textarea` imports (currently only `Input`, `Label`, `NativeSelect` are imported — add `Textarea`):

```tsx
import { Textarea } from "@/components/ui/textarea";
```

Add this constant near `PROVIDER_OPTIONS`:

```tsx
const OPERATION_OPTIONS = [
  { value: "agent", label: "Agent — LLM decides which Jira action to take" },
  { value: "extract", label: "Extract — deterministic bulk JQL search, no LLM" },
];
```

Inside the `JiraPanel` component, after the `apiTokenIsRedacted` line, add:

```tsx
  const operation = (data.operation as string) || "agent";
  const jql = (data.jql as string) ?? "";
  const fieldsList = (data.fields as string[] | undefined) ?? [];
  const expandChangelog = (data.expandChangelog as boolean | undefined) ?? true;
  const maxIssues = (data.maxIssues as number | undefined) ?? 1000;
```

Right after the "Jira Connection" credentials block (`</div>` that closes it, before the `{/* Instructions */}` comment), add the operation selector:

```tsx
      <div className="space-y-2">
        <Label htmlFor="jira-operation">Operation</Label>
        <NativeSelect
          id="jira-operation"
          value={operation}
          onValueChange={(v) => onChange({ operation: v })}
          options={OPERATION_OPTIONS}
        />
      </div>
```

Wrap the existing "Instructions" block (`PromptField`) and everything through "Max iterations" in `{operation === "agent" && (...)}`, and add a new extract-mode block. The full return statement becomes:

```tsx
  return (
    <div className="space-y-4">
      {/* Credentials */}
      <div className="rounded-md border bg-muted/20 p-3">
        <p className="mb-2 text-xs font-semibold text-muted-foreground">
          Jira Connection
        </p>
        <div className="space-y-2">
          <div className="space-y-1">
            <Label htmlFor="jira-domain" className="text-xs">
              Domain
            </Label>
            <Input
              id="jira-domain"
              value={(data.domain as string) ?? ""}
              onChange={(e) => onChange({ domain: e.target.value })}
              placeholder="your-org.atlassian.net"
              className="font-mono text-xs"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="jira-email" className="text-xs">
              Email
            </Label>
            <Input
              id="jira-email"
              value={(data.email as string) ?? ""}
              onChange={(e) => onChange({ email: e.target.value })}
              placeholder="you@example.com"
              className="font-mono text-xs"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="jira-api-token" className="text-xs">
              API Token
            </Label>
            <Input
              id="jira-api-token"
              type="password"
              value={apiTokenIsRedacted ? "" : rawApiToken}
              onChange={(e) => onChange({ apiToken: e.target.value })}
              placeholder={
                apiTokenIsRedacted
                  ? "API token is set — leave blank to keep, type to replace"
                  : "••••••••"
              }
              className="font-mono text-xs"
            />
            <p className="text-[10px] text-muted-foreground">
              Generate at{" "}
              <a
                href="https://id.atlassian.com/manage/api-tokens"
                target="_blank"
                rel="noopener noreferrer"
                className="underline"
              >
                id.atlassian.com/manage/api-tokens
              </a>
            </p>
          </div>
        </div>
      </div>

      <div className="space-y-2">
        <Label htmlFor="jira-operation">Operation</Label>
        <NativeSelect
          id="jira-operation"
          value={operation}
          onValueChange={(v) => onChange({ operation: v })}
          options={OPERATION_OPTIONS}
        />
      </div>

      {operation === "extract" ? (
        <>
          <div className="space-y-2">
            <Label htmlFor="jira-jql">JQL</Label>
            <Textarea
              id="jira-jql"
              value={jql}
              onChange={(e) => onChange({ jql: e.target.value })}
              placeholder="project = MB AND updated >= -7d"
              rows={2}
              className="font-mono text-xs"
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="jira-fields">Fields</Label>
            <Input
              id="jira-fields"
              value={fieldsList.join(", ")}
              onChange={(e) =>
                onChange({
                  fields: e.target.value
                    .split(",")
                    .map((s) => s.trim())
                    .filter(Boolean),
                })
              }
              placeholder="summary, status, assignee, duedate"
              className="font-mono text-xs"
            />
            <p className="text-xs text-muted-foreground">
              Comma-separated Jira field names/IDs to request. No default —
              every workflow specifies exactly what it needs.
            </p>
          </div>

          <div className="flex items-center justify-between rounded-md border px-3 py-2">
            <div>
              <p className="text-sm font-medium">Expand changelog</p>
              <p className="text-xs text-muted-foreground">
                Include status-transition history per issue (needed for
                blocker-age/staleness calculations downstream).
              </p>
            </div>
            <input
              id="jira-expand-changelog"
              aria-label="Expand changelog"
              type="checkbox"
              checked={expandChangelog}
              onChange={(e) => onChange({ expandChangelog: e.target.checked })}
              className="h-4 w-4"
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="jira-max-issues">Max issues</Label>
            <Input
              id="jira-max-issues"
              type="number"
              min={1}
              max={5000}
              value={maxIssues}
              onChange={(e) =>
                onChange({
                  maxIssues: Math.min(Math.max(parseInt(e.target.value, 10) || 1, 1), 5000),
                })
              }
            />
            <p className="text-xs text-muted-foreground">
              Hard cap on issues fetched. Default 1000, ceiling 5000.
            </p>
          </div>
        </>
      ) : (
        <>
          {/* Instructions */}
          <PromptField
            label="Prompt"
            value={(data.instructions as string) ?? ""}
            onChange={(next) => onChange({ instructions: next })}
            nodes={allNodes ?? []}
            currentNodeId={currentNodeId ?? ""}
            rows={6}
            placeholder={
              "Describe what this node should do with Jira. Reference upstream variables with {{name}}.\n\nExample: Search for all open bugs in project PROJ and create a summary of the top 3 by priority."
            }
          />

          {/* Model picker */}
          <div className="space-y-2">
            <Label htmlFor="jira-provider">Model provider (optional)</Label>
            <NativeSelect
              id="jira-provider"
              value={provider}
              onValueChange={(v) => onChange({ provider: v, model: "" })}
              options={[{ value: "", label: "Use default" }, ...PROVIDER_OPTIONS]}
            />
          </div>

          {provider && (
            <div className="space-y-2">
              <Label htmlFor="jira-model">Model</Label>
              {modelsLoading ? (
                <p className="text-xs text-muted-foreground">Loading models…</p>
              ) : modelOptions.length === 0 ? (
                <p className="text-xs text-muted-foreground">
                  No models enabled for this provider. Ask an admin to add one
                  under Admin → LLM models.
                </p>
              ) : (
                <NativeSelect
                  id="jira-model"
                  value={normalizedModel}
                  onValueChange={(v) => onChange({ model: v })}
                  options={modelOptions}
                  placeholder="Select model"
                />
              )}
            </div>
          )}

          <div className="space-y-2">
            <Label htmlFor="jira-max-iterations">Max iterations</Label>
            <Input
              id="jira-max-iterations"
              type="number"
              min={1}
              max={100}
              placeholder="10 (default)"
              value={
                typeof data.maxIterations === "number"
                  ? String(data.maxIterations)
                  : ""
              }
              onChange={(e) => {
                const v = e.target.value;
                if (v === "") {
                  onChange({ maxIterations: undefined });
                  return;
                }
                const n = Math.min(Math.max(parseInt(v, 10) || 0, 1), 100);
                onChange({ maxIterations: n });
              }}
            />
            <p className="text-xs text-muted-foreground">
              How many LLM ↔ tool round-trips the node is allowed before giving
              up. Default 10, hard ceiling 100.
            </p>
          </div>
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run components/composer/canvas/node-panels/jira.test.tsx`
Expected: PASS

- [ ] **Step 5: Run the full frontend test suite and type check**

Run: `cd frontend && npx vitest run && npx tsc --noEmit`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add frontend/components/composer/canvas/node-panels/jira.tsx frontend/components/composer/canvas/node-panels/jira.test.tsx
git commit -m "$(cat <<'EOF'
feat(jira-panel): add operation toggle and extract-mode fields

Agent mode's existing fields (Prompt, model picker, max iterations) are
unchanged and still shown by default. Extract mode reveals JQL, a
comma-separated Fields input (no default value — matches the backend
having no hardcoded field list), Expand changelog, and Max issues.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-review notes

- **Spec coverage**: Component 1 of the spec (Jira `extract` operation — jql/fields/expandChangelog/maxIssues, pagination, retry, un-opinionated output shape) is covered by Tasks 1-2. The Designer-facing half (so a human can configure it without hand-editing JSON) is Task 3. Component 2 (Confluence node) and Component 3 (baseline pattern) are out of scope for this plan — see `docs/superpowers/plans/2026-07-19-confluence-node.md`.
- **Placeholder scan**: none found — every step has complete code.
- **Type consistency**: `operation`/`jql`/`fields`/`expand_changelog`/`max_issues` (Python, `JiraNodeData`) match `operation`/`jql`/`fields`/`expandChangelog`/`maxIssues` (the camelCase aliases the frontend panel actually sends) throughout Tasks 1-3. `JiraExtractConfigError`/`JiraExtractHttpError` names match between Task 2's implementation and its own tests.
