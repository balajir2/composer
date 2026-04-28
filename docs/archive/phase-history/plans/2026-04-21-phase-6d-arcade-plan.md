# Phase 6d — Arcade: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development`. Steps use checkbox (`- [ ]`).

**Goal:** Ship `arcade` executor — HTTP integration with arcade.dev. Two-step authorize → execute. Pause via `interrupt()` when OAuth pending; user completes auth externally and calls `/resume` to retry. `MAX_RETRIES=3`.

**Architecture.** Reuses Phase 5a's `interrupt()` + `/resume` (ADR-0019). `approval-pending` event payload extended with optional `auth_url`/`auth_id`/`tool_name`. `httpx.AsyncClient` for HTTP — no SDK dep.

**Tech Stack:** httpx, pytest-httpx, existing interrupt/resume infra, executor registry, `substitute()`.

**Spec:** [`docs/superpowers/specs/2026-04-21-phase-6d-arcade-design.md`](../specs/2026-04-21-phase-6d-arcade-design.md)
**ADR:** [ADR-0019](../../design/decisions.md#adr-0019-executionsidresume-handles-both-user-approval-and-arcade-auth-flows)

---

## Sequencing + discipline

6 tasks, one commit each. Every task ends green on:

```bash
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m ruff format --check src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

**⚠️ Forbidden files:** `pyproject.toml`, `.github/workflows/*`, `CLAUDE.md` (except Task 6), `docs/design/*` (except Task 6 for ADR backfill), `docs/superpowers/plans/*`, `docs/superpowers/specs/*`, Prisma schema + migrations.

**Sentinel-test authorization:** If existing sentinel tests (`test_registry.py`, `test_graph_builder.py`, `test_langgraph_executor.py`) use `"arcade"` as their "unshipped Phase 6 type" example, migrate to `"vector-db"` in Task 2. Same precedent as Phase 5a / 6b / 6c.

Commit footer:
```
Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
```

Stay on `main`.

---

## Task 1: Tighten `ArcadeNodeData` + add `arcade_api_key`

**Files:**
- Modify: `src/engine/workflow.py`
- Modify: `src/config.py`
- Modify: `tests/unit/engine/test_workflow_models.py` (append)

- [ ] **Step 1: Replace `ArcadeNodeData`**

Current (around line 321):
```python
class ArcadeNodeData(BaseNodeData):
    config: dict[str, Any] = Field(default_factory=dict)
```

Replace with:
```python
class ArcadeNodeData(BaseNodeData):
    model_config = ConfigDict(populate_by_name=True)

    tool: str = Field(alias="arcadeTool")
    input: dict[str, Any] = Field(default_factory=dict, alias="arcadeInput")
    user_id: str = Field(default="workflow-builder", alias="arcadeUserId")
```

`tool` is required. `input` defaults to empty dict. `user_id` defaults to `"workflow-builder"` (matches OAB default).

- [ ] **Step 2: Add `arcade_api_key` to `src/config.py`**

In the "Agent tools (Phase 2)" or "Gamma-AI (Phase 6c)" block (alphabetize appropriately), add:

```python
    # ─── Arcade (Phase 6d) ────────────────────────
    arcade_api_key: str = Field(default="", description="Arcade.dev API key.")
```

- [ ] **Step 3: Append 4 tests to `tests/unit/engine/test_workflow_models.py`**

```python
def test_arcade_node_data_parses_camelcase_aliases() -> None:
    from src.engine.workflow import ArcadeNodeData

    data = ArcadeNodeData.model_validate({
        "label": "AR",
        "arcadeTool": "GoogleDocs.CreateDocumentFromText@4.3.1",
        "arcadeInput": {"title": "Hello", "body": "World"},
        "arcadeUserId": "alice@example.com",
    })
    assert data.tool == "GoogleDocs.CreateDocumentFromText@4.3.1"
    assert data.input == {"title": "Hello", "body": "World"}
    assert data.user_id == "alice@example.com"


def test_arcade_node_data_defaults() -> None:
    from src.engine.workflow import ArcadeNodeData

    data = ArcadeNodeData.model_validate({
        "label": "AR",
        "arcadeTool": "Slack.SendMessage@1.0.0",
    })
    assert data.tool == "Slack.SendMessage@1.0.0"
    assert data.input == {}
    assert data.user_id == "workflow-builder"


def test_arcade_node_data_missing_tool_raises() -> None:
    import pytest
    from pydantic import ValidationError

    from src.engine.workflow import ArcadeNodeData

    with pytest.raises(ValidationError):
        ArcadeNodeData.model_validate({"label": "AR"})


def test_arcade_node_full_round_trip() -> None:
    from src.engine.workflow import ArcadeNode

    node = ArcadeNode.model_validate({
        "id": "ar1",
        "type": "arcade",
        "position": {"x": 0, "y": 0},
        "data": {"label": "AR", "arcadeTool": "Tool@1"},
    })
    assert node.id == "ar1"
    assert node.type == "arcade"
    assert node.data.tool == "Tool@1"
```

If the file has a parametrized `test_every_node_type_parses_minimal_instance` including `"arcade"`, update it to provide `arcadeTool` (now required).

- [ ] **Step 4: Run + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/engine/test_workflow_models.py -v -k "arcade"
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

Expected: 4 new tests pass; overall ~454 (from 450 at Phase 6c exit).

```bash
git add src/engine/workflow.py src/config.py tests/unit/engine/test_workflow_models.py
git commit -m "feat(workflow): tighten ArcadeNodeData + add arcade_api_key setting (Phase 6d)

Replaces the Phase 1 placeholder {config: dict} with explicit fields
matching OAB types.ts + executors/arcade.ts:
  - tool (required, alias arcadeTool)
  - input (default {}, alias arcadeInput)
  - user_id (default 'workflow-builder', alias arcadeUserId)

settings.arcade_api_key added (env var ARCADE_API_KEY).

See Phase 6d spec §4.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `ArcadeExecutor` + unit tests

**Files:**
- Create: `src/executors/arcade.py`
- Modify: `src/engine/graph_builder.py` — side-effect import
- Create: `tests/unit/executors/test_arcade.py`
- **Sentinel migration (authorized):** if `tests/unit/executors/test_registry.py`, `tests/unit/engine/test_graph_builder.py`, or `tests/unit/engine/test_langgraph_executor.py` use `"arcade"` as the "unshipped Phase 6 type" example, change to `"vector-db"`. Run `grep -rn '"arcade"' tests/unit/` to check.

- [ ] **Step 1: Write failing tests `tests/unit/executors/test_arcade.py`**

```python
"""Tests for the arcade executor (mocked Arcade API via pytest-httpx)."""

from typing import Any
from unittest.mock import MagicMock

import pytest
from pytest_httpx import HTTPXMock

from src.engine.state import initial_state
from src.engine.workflow import ArcadeNode
from src.executors.arcade import (
    ARCADE_API_BASE,
    MAX_RETRIES,
    ArcadeAuthError,
    ArcadeExecutor,
    ArcadeNodeError,
    ArcadeUserCanceledError,
)


@pytest.fixture(autouse=True)
def _stub_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.config import get_settings

    monkeypatch.setenv("ARCADE_API_KEY", "test-arcade-key")
    get_settings.cache_clear()


def _node(**data_overrides: Any) -> ArcadeNode:
    data: dict[str, Any] = {
        "label": "AR",
        "arcadeTool": "TestTool@1.0.0",
        "arcadeInput": {},
    }
    data.update(data_overrides)
    return ArcadeNode.model_validate({
        "id": "ar",
        "type": "arcade",
        "position": {"x": 0, "y": 0},
        "data": data,
    })


async def test_auth_completed_executes_and_returns_output(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST", url=f"{ARCADE_API_BASE}/tools/authorize",
        json={"id": "auth1", "status": "completed"},
        status_code=200,
    )
    httpx_mock.add_response(
        method="POST", url=f"{ARCADE_API_BASE}/tools/execute",
        json={"output": {"value": "hello"}},
        status_code=200,
    )
    delta = await ArcadeExecutor(_node()).arun(initial_state())
    assert delta["variables"]["lastOutput"] == "hello"


async def test_output_plain_dict(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST", url=f"{ARCADE_API_BASE}/tools/authorize",
        json={"id": "auth1", "status": "completed"}, status_code=200,
    )
    httpx_mock.add_response(
        method="POST", url=f"{ARCADE_API_BASE}/tools/execute",
        json={"output": {"foo": "bar"}}, status_code=200,
    )
    delta = await ArcadeExecutor(_node()).arun(initial_state())
    assert delta["variables"]["lastOutput"] == {"foo": "bar"}


async def test_output_fallback_to_result(httpx_mock: HTTPXMock) -> None:
    """If no 'output' key, fall back to the whole result dict."""
    httpx_mock.add_response(
        method="POST", url=f"{ARCADE_API_BASE}/tools/authorize",
        json={"id": "auth1", "status": "completed"}, status_code=200,
    )
    httpx_mock.add_response(
        method="POST", url=f"{ARCADE_API_BASE}/tools/execute",
        json={"result": "only-result"}, status_code=200,
    )
    delta = await ArcadeExecutor(_node()).arun(initial_state())
    assert delta["variables"]["lastOutput"] == {"result": "only-result"}


async def test_auth_pending_calls_interrupt(
    monkeypatch: pytest.MonkeyPatch, httpx_mock: HTTPXMock,
) -> None:
    """First-pass auth pending → interrupt() is called with correct payload."""
    from langgraph.errors import GraphInterrupt

    import src.executors.arcade as ar_mod

    captured: list[dict[str, Any]] = []

    def _fake_interrupt(payload: Any) -> Any:
        captured.append(payload)
        raise GraphInterrupt(payload)

    monkeypatch.setattr(ar_mod, "interrupt", _fake_interrupt)

    httpx_mock.add_response(
        method="POST", url=f"{ARCADE_API_BASE}/tools/authorize",
        json={
            "id": "auth_pending",
            "status": "pending",
            "url": "https://auth.arcade.dev/x",
        },
        status_code=200,
    )

    state = initial_state()
    with pytest.raises(GraphInterrupt):
        await ArcadeExecutor(_node()).arun(state)

    assert captured[0]["node_id"] == "ar"
    assert captured[0]["auth_url"] == "https://auth.arcade.dev/x"
    assert captured[0]["auth_id"] == "auth_pending"
    assert captured[0]["tool_name"] == "TestTool@1.0.0"
    # State mutation: retry counter incremented in-place before interrupt
    assert state["variables"]["_arcade_retries_ar"] == 1


async def test_auth_failed_raises(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST", url=f"{ARCADE_API_BASE}/tools/authorize",
        json={"id": "auth1", "status": "failed"}, status_code=200,
    )
    with pytest.raises(ArcadeAuthError, match="authorization failed"):
        await ArcadeExecutor(_node()).arun(initial_state())


async def test_resume_rejected_raises_canceled() -> None:
    state = initial_state()
    state["variables"]["_approval_ar"] = "rejected"
    with pytest.raises(ArcadeUserCanceledError):
        await ArcadeExecutor(_node()).arun(state)


async def test_resume_approved_retries_and_executes(httpx_mock: HTTPXMock) -> None:
    """After resume with approved, executor re-runs authorize; if now completed,
    proceeds to execute."""
    httpx_mock.add_response(
        method="POST", url=f"{ARCADE_API_BASE}/tools/authorize",
        json={"id": "a", "status": "completed"}, status_code=200,
    )
    httpx_mock.add_response(
        method="POST", url=f"{ARCADE_API_BASE}/tools/execute",
        json={"output": "done"}, status_code=200,
    )
    state = initial_state()
    state["variables"]["_approval_ar"] = "approved"
    state["variables"]["_arcade_retries_ar"] = 1
    delta = await ArcadeExecutor(_node()).arun(state)
    assert delta["variables"]["lastOutput"] == "done"
    # Retry counter reset on success
    assert delta["variables"]["_arcade_retries_ar"] == 0


async def test_retry_limit_exceeded_raises(httpx_mock: HTTPXMock) -> None:
    state = initial_state()
    state["variables"]["_arcade_retries_ar"] = MAX_RETRIES + 1  # already past
    with pytest.raises(ArcadeAuthError, match="retry limit"):
        await ArcadeExecutor(_node()).arun(state)


async def test_variable_substitution_in_input(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST", url=f"{ARCADE_API_BASE}/tools/authorize",
        json={"id": "a", "status": "completed"}, status_code=200,
    )
    httpx_mock.add_response(
        method="POST", url=f"{ARCADE_API_BASE}/tools/execute",
        json={"output": "ok"}, status_code=200,
    )
    state = initial_state()
    state["variables"]["name"] = "world"
    node = _node(arcadeInput={"body": "Hello {{name}}", "count": 42})
    await ArcadeExecutor(node).arun(state)

    import json

    exec_request = httpx_mock.get_requests(
        method="POST", url=f"{ARCADE_API_BASE}/tools/execute",
    )[0]
    body = json.loads(exec_request.content)
    assert body["input"]["body"] == "Hello world"
    assert body["input"]["count"] == 42  # non-string passed through


async def test_missing_api_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.config import get_settings

    monkeypatch.setenv("ARCADE_API_KEY", "")
    get_settings.cache_clear()
    with pytest.raises(ArcadeNodeError, match="ARCADE_API_KEY"):
        await ArcadeExecutor(_node()).arun(initial_state())


async def test_authorize_network_error_raises(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_exception(
        method="POST", url=f"{ARCADE_API_BASE}/tools/authorize",
        exception=Exception("network down"),
    )
    with pytest.raises(ArcadeNodeError, match="/tools/authorize"):
        await ArcadeExecutor(_node()).arun(initial_state())


async def test_authorize_4xx_raises(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST", url=f"{ARCADE_API_BASE}/tools/authorize",
        status_code=401, text="bad key",
    )
    with pytest.raises(ArcadeNodeError, match="401"):
        await ArcadeExecutor(_node()).arun(initial_state())


async def test_authorize_missing_id_raises(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST", url=f"{ARCADE_API_BASE}/tools/authorize",
        json={"status": "completed"}, status_code=200,  # no 'id'
    )
    with pytest.raises(ArcadeNodeError, match="missing 'id'"):
        await ArcadeExecutor(_node()).arun(initial_state())


async def test_executor_is_registered() -> None:
    import src.executors.arcade  # noqa: F401  # pyright: ignore[reportUnusedImport]

    from src.executors.base import build_executor

    executor = build_executor(_node())
    assert isinstance(executor, ArcadeExecutor)
```

- [ ] **Step 2: Verify fail**

```bash
.venv/Scripts/python -m pytest tests/unit/executors/test_arcade.py -v
```

Expected: ImportError on `src.executors.arcade`.

- [ ] **Step 3: Implement `src/executors/arcade.py`**

Copy the code block from Phase 6d spec §5 verbatim. Key points:
- `ARCADE_API_BASE = "https://api.arcade.dev/v1"`
- `MAX_RETRIES = 3`
- Three exception classes: `ArcadeNodeError`, `ArcadeAuthError`, `ArcadeUserCanceledError`
- **Retry counter persistence:** mutate `state["variables"][retries_key] = next_retries` BEFORE calling `interrupt()`. LangGraph captures pre-interrupt state in the checkpoint.
- **Resume decision:** read `state.variables[f"_approval_{node.id}"]` at entry; `"rejected"` → `ArcadeUserCanceledError`.

The exact executor body is in the spec. Write it.

- [ ] **Step 4: Register side-effect import in `src/engine/graph_builder.py`**

Alphabetically (before or around `data_transform`/`extract`):

```python
from src.executors import (
    arcade as _arcade_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
```

- [ ] **Step 5: Check stale sentinel tests**

```bash
grep -rn '"arcade"' tests/unit/executors/test_registry.py tests/unit/engine/test_graph_builder.py tests/unit/engine/test_langgraph_executor.py
```

If any file uses `"arcade"` as the unshipped-sentinel example, change to `"vector-db"`. Include in same commit.

- [ ] **Step 6: Run + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/executors/test_arcade.py -v
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

Expected: ~14 new tests pass; overall +14 (~468 from 454 after Task 1). Pyright 0 errors.

```bash
git add src/executors/arcade.py src/engine/graph_builder.py tests/unit/executors/test_arcade.py
# If sentinel files changed, add them too.
git commit -m "feat(executors): arcade — auth-interrupt HTTP integration (Phase 6d)

Two-step protocol via direct HTTP (httpx):
  POST /v1/tools/authorize → {id, status, url}
  POST /v1/tools/execute → {output, ...}

If auth.status == 'completed': execute and write lastOutput.
If 'failed': ArcadeAuthError.
If 'pending': increment retry counter (_arcade_retries_<node_id>) in
state and call interrupt() with {node_id, prompt, auth_url, auth_id,
tool_name}.  User completes OAuth externally and calls POST
/executions/{id}/resume with decision='approved' (retry) or 'rejected'
(ArcadeUserCanceledError).

Retry counter capped at MAX_RETRIES=3 — prevents infinite loops on
broken OAuth URLs.  State mutation happens BEFORE interrupt() so the
counter persists through LangGraph's pre-interrupt checkpoint.

Input substitution: each string value in arcadeInput runs through
substitute(); non-strings pass through.

Output extraction: result.output.value ?? result.output ?? result.

Reuses Phase 5a's /resume endpoint and Phase 5b's approval-pending
event type (payload extended with optional auth_url/auth_id/tool_name).
See ADR-0019.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Sanity check

No code changes. Run the full suite to confirm no regressions.

```bash
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m ruff format --check src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

All green. No commit.

---

## Task 4: Manual smoke test (skipped by default)

Arcade's OAuth flow can't be automated in CI. If the user wants to verify live behavior, they create a test script `scripts/arcade_smoke.py` (not committed) that:
1. Sets `ARCADE_API_KEY` + a real tool name in `.env`.
2. Starts the FastAPI server.
3. Creates a workflow with one arcade node.
4. Starts an execution.
5. Polls until `waiting_approval`, visits the auth URL, completes OAuth.
6. Calls `/resume` with `decision='approved'`.
7. Polls until `completed`, verifies `lastOutput`.

No commit. CHANGELOG documents integration is manual.

---

## Task 5: Reserved — fix-up slot

If the retry-counter persistence pattern (mutate-before-interrupt) doesn't work as expected in LangGraph, fix in a `fix(phase-6d): ...` commit. Signs: retry counter always resets to 0 between invocations; `_approval_<node_id>` from state doesn't survive; etc.

If no issues, skip.

---

## Task 6: Phase-exit — CHANGELOG + CLAUDE.md + ADR-0019 backfill + push

**Authorized:** `CHANGELOG.md`, `CLAUDE.md`, `docs/design/decisions.md`.

- [ ] **Step 1: Final exit checklist**

```bash
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m ruff format --check src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

- [ ] **Step 2: Update `CHANGELOG.md`** — insert above Phase 6c:

```markdown
### Phase 6d — Arcade (2026-04-21)

#### Added
- [Phase 6d design spec](docs/superpowers/specs/2026-04-21-phase-6d-arcade-design.md) + ADR-0019.
- `src/executors/arcade.py` — `ArcadeExecutor` calls arcade.dev's tool-execution API via direct HTTP (no SDK). Two-step protocol: `POST /v1/tools/authorize` → `POST /v1/tools/execute`. If auth is pending, pauses via Phase 5a's `interrupt()` primitive; user completes OAuth externally and calls `POST /executions/{id}/resume` with `decision='approved'` (retry) or `'rejected'` (`ArcadeUserCanceledError`). Retry counter `_arcade_retries_<node_id>` caps resume attempts at `MAX_RETRIES=3` to prevent infinite loops.
- `src/engine/workflow.py` — `ArcadeNodeData` tightened: `tool` (required, alias `arcadeTool`), `input` (default `{}`, alias `arcadeInput`), `user_id` (default `workflow-builder`, alias `arcadeUserId`).
- `src/config.py` — `arcade_api_key` setting (env var `ARCADE_API_KEY`).
- SSE `approval-pending` event payload extended with optional `auth_url` / `auth_id` / `tool_name` fields (back-compat: existing consumers ignore unknown keys).
- Exception classes: `ArcadeNodeError` (config/network), `ArcadeAuthError` (auth failed / retry limit), `ArcadeUserCanceledError` (user rejected).
- Unit tests: ~14 via `pytest-httpx`; `interrupt()` mocked to raise `GraphInterrupt` in the pending-auth case.

#### Changed
- `/executions/{id}/resume` now serves TWO interrupt sources: user-approval (Phase 5a) AND arcade-auth (Phase 6d). Same endpoint, same event type, same retry primitive. Executor owns the re-entry semantic (ADR-0019).

#### Notes
- Max runtime unbounded (depends on how long the user takes to complete OAuth). Retry counter caps the number of resume cycles.
- Variable substitution applies to each string value in `arcadeInput`; non-string values pass through.
- Output extraction: `result.output.value` first, then `result.output`, then the whole `result` dict — matches OAB's fallback chain.
- **Integration test is manual-only.** Arcade's OAuth flow cannot be automated in CI. Smoke-test pattern documented in spec §7.2.

#### Verified
- ~468/468 unit tests green (+18 from Phase 6c 450: 4 Pydantic tests + 14 executor tests).
- Pyright 0 errors, ruff + format clean.

### Phase 6c — Gamma-AI (2026-04-21)
```

- [ ] **Step 3: Update `CLAUDE.md` phase table**

Change:
```markdown
| 6c — Gamma-AI | ✅ Complete | HTTP integration with gamma.app; 60s/10s/4min polling; exportAs pptx/pdf supported; 13 unit tests, smoke test manual |
| 6d — Arcade | ⏭ Next | HTTP integration with auth-interrupt flow (reuses Phase 5a `/resume` pattern) |
| 6e — Vector-DB | ⏸ | provider framework (embed/upsert/query) |
```
To:
```markdown
| 6c — Gamma-AI | ✅ Complete | HTTP integration with gamma.app; 60s/10s/4min polling; exportAs pptx/pdf supported; 13 unit tests, smoke test manual |
| 6d — Arcade | ✅ Complete | HTTP integration with auth-interrupt flow; reuses Phase 5a `/resume`; retry counter MAX_RETRIES=3; 14 unit tests, smoke test manual |
| 6e — Vector-DB | ⏭ Next | provider framework (embed/upsert/query) |
```

- [ ] **Step 4: Backfill ADR-0019 `Implemented by`**

```bash
git log --oneline ffc3b13..HEAD
```
Replace `**Implemented by.** Phase 6d (commits TBD).` with the range.

- [ ] **Step 5: Commit + push**

```bash
git add CHANGELOG.md CLAUDE.md docs/design/decisions.md
git commit -m "docs(phase-6d): mark Phase 6d complete

Arcade shipped.  HTTP integration with arcade.dev tool-execution API;
two-step authorize → execute; auth-pending pauses via Phase 5a
interrupt(), resumes via /executions/{id}/resume with retry counter
MAX_RETRIES=3.

ADR-0019 documents reusing /resume for both user-approval and
arcade-auth flows.  Same endpoint, same event type (approval-pending
payload extended), executor owns the re-entry semantic.

Integration manual (Arcade OAuth can't be automated); 14 unit tests
with pytest-httpx + mocked interrupt.

Phase 6e (Vector-DB) is next.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"

git push origin main
```

---

## Spec coverage self-review

| Spec section | Tasks |
|---|---|
| §4.1 Pydantic | Task 1 |
| §4.2 settings | Task 1 |
| §4.3 event payload extension | Task 2 (executor writes the payload; no explicit code change to event_bus needed since payload is dict[str, Any]) |
| §5 executor | Task 2 |
| §6 error model | Task 2 (tests 4-6, 8-12) |
| §7 test plan | Task 2 |
| §8 ADR-0019 | Pre-plan spec commit + Task 6 backfill |
| §9 phase-exit | Task 6 |

No placeholders. Type consistency: `tool` / `input` / `user_id` match across §4 Pydantic, §5 executor, §7 tests. Retry counter key `_arcade_retries_<node_id>` consistent. Three exception classes consistent.

---

## Execution handoff

Plan saved. Controller proceeds to `superpowers:subagent-driven-development` for Tasks 1–6.
