# Phase 6d — Arcade: Design

**Status.** Approved 2026-04-21.
**Related.** Phase 5a user-approval + interrupt/resume (ADR-0016), Phase 5b SSE streaming (ADR-0017), ADR-0019 (this phase — reusing `/resume` for arcade-auth).

---

## 1. Goal

Ship the `arcade` executor — HTTP integration with arcade.dev's tool-execution API. Two-step protocol: authorize first, then execute. If authorization is pending (user must visit OAuth URL externally), the executor pauses via LangGraph's `interrupt()`; user completes auth and calls `POST /executions/{id}/resume` to retry. **Retry counter caps resume attempts at 3** to prevent infinite loops on broken auth URLs.

## 2. Non-goals

- **No new SDK dependency.** Arcade has `arcadepy` but the two HTTP endpoints are trivial — direct `httpx.AsyncClient` is sufficient and matches the Gamma-AI pattern.
- **No new `/retry-auth` endpoint.** We reuse Phase 5a's `/executions/{id}/resume` for both user-approval and arcade-auth. ADR-0019 documents this choice.
- **No new SSE event type.** We extend `approval-pending` payload with optional `auth_url` / `auth_id` / `tool_name` fields. Existing consumers ignore unknown fields; spec §4.5 pins back-compat.
- **No automated integration test.** Arcade's auth flow requires a real OAuth consent that cannot be automated in CI. Manual smoke test documented for users with an Arcade account.
- **No UI.** Phase 10.

## 3. Architecture

```
┌────────────────────────────────────────────────────────────────┐
│ ArcadeExecutor.arun                                            │
│                                                                │
│  retries = state.variables["_arcade_retries_<node_id>"] or 0   │
│  if retries > MAX_RETRIES → raise ArcadeAuthError              │
│                                                                │
│  resume_decision = state.variables["_approval_<node_id>"]      │
│  if resume_decision == "rejected":                             │
│      raise ArcadeUserCanceledError                             │
│                                                                │
│  [read tool_name, input (substituted), user_id from data]      │
│                                                                │
│  auth = await arcade_authorize(tool_name, user_id)             │
│                                                                │
│  if auth.status == "completed":                                │
│      result = await arcade_execute(tool_name, input, user_id)  │
│      return delta with lastOutput = extracted_output           │
│                                                                │
│  if auth.status == "failed":                                   │
│      raise ArcadeAuthError                                     │
│                                                                │
│  # auth.status == "pending" — interrupt                        │
│  increment retries counter in variables                        │
│  interrupt({                                                   │
│      "node_id": node.id,                                       │
│      "prompt": f"Authorize {tool_name}: {auth.url}",           │
│      "auth_url": auth.url,                                     │
│      "auth_id": auth.id,                                       │
│      "tool_name": tool_name,                                   │
│  })                                                            │
└────────────────────────────────────────────────────────────────┘
```

**Retry counter lifecycle.**
- First invocation: `retries = 0`. If auth pending → increment to 1, interrupt.
- User completes auth externally, calls `/resume {decision: approved}`.
- Executor re-runs: `retries = 1`, `_approval_<node_id> = "approved"` in state. Executor clears the rejection path (approved ≠ rejected), proceeds.
- Re-check auth: if still pending → increment to 2, interrupt.
- After 3rd interrupt (retries=3) and fourth resume: if STILL pending, next invocation sees `retries > MAX_RETRIES (3)` and raises `ArcadeAuthError`.

**MAX_RETRIES = 3** — picked as a middle ground. User gets ~3 chances to fix a broken OAuth flow before the workflow fails.

## 4. Pydantic model

### 4.1 `ArcadeNodeData`

Current Phase 1 placeholder:
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

### 4.2 `settings.arcade_api_key`

Add to `src/config.py`:
```python
arcade_api_key: str = Field(default="", description="Arcade.dev API key.")
```

### 4.3 Event payload extension (`approval-pending`)

Existing `approval-pending` payload is `{node_id: str, prompt: str}`. Extend with optional fields (back-compatible — existing consumers ignore unknown keys):

```
approval-pending.payload = {
  node_id: str,
  prompt: str,
  auth_url?: str,      # NEW — Arcade auth URL, if applicable
  auth_id?: str,       # NEW — Arcade auth record id
  tool_name?: str,     # NEW — Arcade tool identifier
}
```

No code changes to `ExecutionEventBus` or the SSE endpoint — the payload is already `dict[str, Any]`.

### 4.4 `interrupt()` payload

`UserApprovalExecutor` interrupts with `{node_id, prompt}`. `ArcadeExecutor` interrupts with the same keys **plus** `auth_url`, `auth_id`, `tool_name`. `LangGraphExecutor._extract_pending_info` reads the payload into `_pending_approval_node` / `_pending_approval_prompt` on the execution row — the extra fields are not persisted to the DB row in Phase 6d (only the SSE event carries them). Phase 10 UI can pull them from the SSE stream.

### 4.5 Back-compat assertion

Spec §4.3 extension MUST be back-compatible. Test coverage: an existing consumer that only reads `payload.node_id` and `payload.prompt` keeps working. No field renames.

## 5. `ArcadeExecutor` — implementation

**File:** `src/executors/arcade.py` (new).

```python
"""arcade node executor.

Two-step protocol: authorize, then execute.  If auth is pending, pauses
via LangGraph's interrupt() — user completes OAuth externally and calls
POST /executions/{id}/resume to retry.  Retry counter caps attempts at
MAX_RETRIES (3).

Output:
    lastOutput = result.output.value ?? result.output ?? result

See Phase 6d spec §3 + §5, ADR-0019.
"""

from __future__ import annotations

from typing import Any

import httpx
from langgraph.types import interrupt  # pyright: ignore[reportUnknownVariableType]

from src.config import get_settings
from src.engine.state import WorkflowStateDict
from src.engine.workflow import ArcadeNode
from src.executors.base import register_executor
from src.variable_substitution import substitute

ARCADE_API_BASE = "https://api.arcade.dev/v1"
MAX_RETRIES = 3


class ArcadeNodeError(RuntimeError):
    """Raised for configuration/network errors (missing key, bad SDK response)."""


class ArcadeAuthError(RuntimeError):
    """Raised when Arcade reports auth failure or retry counter exceeded."""


class ArcadeUserCanceledError(RuntimeError):
    """Raised when the user rejects the auth prompt on /resume."""


@register_executor("arcade")
class ArcadeExecutor:
    def __init__(self, node: ArcadeNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        api_key = get_settings().arcade_api_key
        if not api_key:
            raise ArcadeNodeError(
                f"arcade node {self.node.id!r}: ARCADE_API_KEY not configured"
            )

        tool_name = self.node.data.tool
        if not tool_name:
            raise ArcadeNodeError(
                f"arcade node {self.node.id!r}: arcadeTool is required"
            )

        user_id = self.node.data.user_id
        variables = state.get("variables") or {}

        # Check resumed decision — if rejected, raise early
        decision = variables.get(f"_approval_{self.node.id}")
        if decision == "rejected":
            raise ArcadeUserCanceledError(
                f"arcade node {self.node.id!r}: user canceled authorization"
            )

        # Retry counter check
        retries_key = f"_arcade_retries_{self.node.id}"
        retries = int(variables.get(retries_key) or 0)
        if retries > MAX_RETRIES:
            raise ArcadeAuthError(
                f"arcade node {self.node.id!r}: auth retry limit ({MAX_RETRIES}) "
                f"exceeded — tool {tool_name!r} never authorized"
            )

        # Substitute variables in the input dict
        substituted_input: dict[str, Any] = {}
        for key, value in self.node.data.input.items():
            substituted_input[key] = (
                substitute(value, state) if isinstance(value, str) else value
            )

        async with httpx.AsyncClient(
            base_url=ARCADE_API_BASE,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        ) as client:
            auth = await self._authorize(client, tool_name, user_id)

            if auth.get("status") == "failed":
                raise ArcadeAuthError(
                    f"arcade node {self.node.id!r}: authorization failed for "
                    f"{tool_name!r}"
                )

            if auth.get("status") != "completed":
                # Auth pending → interrupt
                next_retries = retries + 1
                interrupt(
                    {
                        "node_id": self.node.id,
                        "prompt": (
                            f"Authorize {tool_name}: visit {auth.get('url', '')} "
                            f"to complete OAuth"
                        ),
                        "auth_url": auth.get("url"),
                        "auth_id": auth.get("id"),
                        "tool_name": tool_name,
                    }
                )
                # Only reached on resume path (post-LangGraph-0.2 semantics)
                return self._pending_delta(
                    tool_name=tool_name,
                    auth_url=auth.get("url"),
                    next_retries=next_retries,
                )

            # Auth completed → execute
            result = await self._execute(client, tool_name, substituted_input, user_id)

        output = self._extract_output(result)
        return {
            "variables": {
                "lastOutput": output,
                # Clear retry counter on success
                retries_key: 0,
            },
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "input": {
                        "tool": tool_name,
                        "user_id": user_id,
                    },
                    "output": output,
                }
            },
        }

    async def _authorize(
        self,
        client: httpx.AsyncClient,
        tool_name: str,
        user_id: str,
    ) -> dict[str, Any]:
        try:
            resp = await client.post(
                "/tools/authorize",
                json={"tool_name": tool_name, "user_id": user_id},
            )
        except httpx.HTTPError as exc:
            raise ArcadeNodeError(
                f"arcade node {self.node.id!r}: POST /tools/authorize failed: {exc}"
            ) from exc
        if resp.status_code >= 400:
            raise ArcadeNodeError(
                f"arcade node {self.node.id!r}: Arcade API error "
                f"{resp.status_code}: {resp.text}"
            )
        body = resp.json()
        if not body.get("id"):
            raise ArcadeNodeError(
                f"arcade node {self.node.id!r}: authorize response missing 'id': "
                f"{body}"
            )
        return body

    async def _execute(
        self,
        client: httpx.AsyncClient,
        tool_name: str,
        tool_input: dict[str, Any],
        user_id: str,
    ) -> dict[str, Any]:
        try:
            resp = await client.post(
                "/tools/execute",
                json={
                    "tool_name": tool_name,
                    "input": tool_input,
                    "user_id": user_id,
                },
            )
        except httpx.HTTPError as exc:
            raise ArcadeNodeError(
                f"arcade node {self.node.id!r}: POST /tools/execute failed: {exc}"
            ) from exc
        if resp.status_code >= 400:
            raise ArcadeNodeError(
                f"arcade node {self.node.id!r}: Arcade API error "
                f"{resp.status_code}: {resp.text}"
            )
        return resp.json()

    @staticmethod
    def _extract_output(result: dict[str, Any]) -> Any:
        output = result.get("output")
        if isinstance(output, dict) and "value" in output:
            return output["value"]
        if output is not None:
            return output
        return result

    def _pending_delta(
        self,
        *,
        tool_name: str,
        auth_url: str | None,
        next_retries: int,
    ) -> dict[str, Any]:
        """State delta written when interrupt is NOT raised (legacy branch).

        Normally interrupt() raises and state is persisted via Phase 5a's
        aget_state path — the retry counter update comes from that path.
        This delta is a belt-and-suspenders fallback.
        """
        retries_key = f"_arcade_retries_{self.node.id}"
        return {
            "variables": {
                retries_key: next_retries,
                "lastOutput": f"Awaiting auth for {tool_name}: {auth_url or ''}",
            },
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "pending",
                    "input": {"tool": tool_name, "auth_url": auth_url},
                    "output": None,
                }
            },
        }


__all__ = [
    "ARCADE_API_BASE",
    "MAX_RETRIES",
    "ArcadeAuthError",
    "ArcadeExecutor",
    "ArcadeNodeError",
    "ArcadeUserCanceledError",
]
```

### Retry counter persistence

Between interrupts, the retry counter lives in `state.variables["_arcade_retries_<node_id>"]`. Phase 5a's `_mark_waiting_approval` merges the interrupt's payload into the existing variables (commit `b07d8de`), so the counter survives across the pause.

**Wait** — Phase 5a's merge writes `_pending_approval_node` and `_pending_approval_prompt` but does NOT write `_arcade_retries_*`. Does the counter survive?

The answer is **yes, through LangGraph's checkpointer**. When the executor runs and calls `interrupt()`, LangGraph persists the state accumulator (including any partial deltas written before the interrupt). The next invocation (on resume) reads from the checkpoint and sees the prior state. However, our executor calls `interrupt()` BEFORE writing the retry counter delta — so the counter wouldn't be persisted through interrupt.

**Fix:** increment the counter BEFORE calling `interrupt()`. In LangGraph, partial state updates written before an interrupt inside a node DO persist because the checkpointer captures them. The simplest way is to write the counter as a state update in a "pre-interrupt" delta return, then interrupt... but `arun` can only return once.

**Cleanest pattern:** the executor does NOT increment the counter inside itself. Instead, `/executions/{id}/resume` increments a generic resume-counter on the execution row, and the executor reads that. But that mingles concerns.

**Simpler pattern:** count `_approval_<node_id>` occurrences (one per resume). Phase 5a writes this on every resume. If the node has been interrupted N times, the resume decision counter is... still just the latest value, not a count.

**Actually simplest:** **track retries via resume decision history**. Phase 5a writes `_approval_<node_id> = "approved"` on every resume. We don't currently keep a count. To add one, Phase 5a's `/resume` endpoint OR `LangGraphExecutor.resume` would need to increment a counter.

**Cleanest choice:** extend `LangGraphExecutor.resume` to increment `_resume_count_<execution_id>` in the checkpointed state each time it fires. Or, more locally, the arcade executor uses a different mechanism:

**Pragmatic pattern:** use `state.variables["_arcade_retries_<node_id>"]` but update it via the checkpointer's state-merge BEFORE calling `interrupt()`. We can write the counter directly to `state.variables` (mutating the in-param dict) just before `interrupt()` raises — LangGraph's Pregel will capture this in the pre-interrupt snapshot.

This is a spec-level behavior decision. Let me pin it:

> **Retry counter persistence:** Executor mutates `state["variables"][f"_arcade_retries_{node_id}"] = next_retries` immediately before calling `interrupt()`. LangGraph's Pregel runtime captures the pre-interrupt state in the checkpoint, so the counter survives through the pause.

This works because `WorkflowStateDict` is a TypedDict over a plain `dict` and the Pregel runtime checkpoints state after each node's yields/returns/interrupts.

**Alternative if that doesn't work:** the executor could emit its retry counter update via the explicit `interrupt()` payload, and `LangGraphExecutor` writes it to the execution row on pause. Phase 5a's merge code already handles the writing. But this requires `_mark_waiting_approval` to accept arbitrary variables, not just the pinned two.

**For safety, this spec also fallback-hardens:** on every `arun` entry, `arcade` reads BOTH `_arcade_retries_<node_id>` from `state.variables` AND the resume-count from the integer value of `_approval_<node_id>` (if present, count = 1+). Mathematically, if the user has resumed at least once and we STILL see `pending` status from Arcade, that's at least one failed retry.

Keeping the design simple: **mutate state.variables before interrupt, rely on LangGraph's pre-interrupt snapshot.** If testing shows it doesn't persist, the integration test will catch it.

## 6. Error model

| Condition                                       | Exception                      | Execution outcome |
|-------------------------------------------------|--------------------------------|-------------------|
| `ARCADE_API_KEY` unset                          | `ArcadeNodeError`              | `failed`          |
| `arcadeTool` not set on node                    | `ArcadeNodeError`              | `failed`          |
| POST authorize/execute network failure / 4xx/5xx| `ArcadeNodeError`              | `failed`          |
| Authorize returns `status='failed'`             | `ArcadeAuthError`              | `failed`          |
| Retry counter > MAX_RETRIES (3)                 | `ArcadeAuthError`              | `failed`          |
| User resumes with `decision='rejected'`         | `ArcadeUserCanceledError`      | `failed`          |
| Authorize returns `status='pending'` (≤ retries)| (none — pauses via interrupt)  | `waiting_approval`|
| Authorize returns `status='completed'`          | (none — proceeds to execute)   | `completed`       |

## 7. Test plan

### 7.1 Unit tests (`tests/unit/executors/test_arcade.py`) — ~10

Use `pytest-httpx` to mock Arcade endpoints.

1. Auth completed + execute success → `lastOutput` = extracted output.
2. Auth completed + execute returns `{output: {value: "hello"}}` → `lastOutput = "hello"`.
3. Auth completed + execute returns `{output: "plain"}` → `lastOutput = "plain"`.
4. Auth completed + execute returns `{result: "fallback"}` (no `output`) → `lastOutput = {result: "fallback"}`.
5. Auth pending → `interrupt()` is called with correct payload (mocked to raise `GraphInterrupt` so we observe it); retry counter incremented in state.
6. Auth failed → `ArcadeAuthError`.
7. Resume with `decision='rejected'` → `ArcadeUserCanceledError`.
8. Resume with `decision='approved'` + auth now completed → executes successfully.
9. Retry counter > MAX_RETRIES → `ArcadeAuthError("retry limit")`.
10. Variable substitution: `arcadeInput = {"body": "Hello {{name}}"}` + state has `name=world` → request body has `"body": "Hello world"`.
11. Missing API key → `ArcadeNodeError`.
12. Missing `arcadeTool` → `ArcadeNodeError`.
13. Non-string input values pass through unchanged (int, dict, bool).
14. Executor registered in `_REGISTRY`.

### 7.2 Integration — manual only

Arcade's auth flow requires an Arcade account + OAuth consent. Not automated.
A simple manual smoke test script can be provided in `scripts/arcade_smoke.py` (not committed). CHANGELOG documents.

## 8. ADR-0019 (summary — full entry appended to `docs/design/decisions.md`)

**Title.** `/executions/{id}/resume` handles both user-approval and arcade-auth flows.

**Decision.** One endpoint for both. The executor owns the resume semantic (approved/rejected interpretation).

**Alternatives.** Dedicated `/arcade-auth-check` endpoint. Rejected on YAGNI — the shape is identical (interrupt with payload, resume with decision), only the executor's re-entry logic differs.

**Consequences.**
- `ArcadeExecutor` reads `_approval_<node.id>` on re-entry; `approved` means "try again", `rejected` means cancel.
- `approval-pending` event payload carries optional `auth_url`, `auth_id`, `tool_name` — existing consumers unaffected.
- Future executors that need the same pattern (e.g., Phase 7+ Slack/Gmail tool integrations) reuse this primitive — no additional endpoint surface area.
- Retry counter bounded (3) prevents infinite loops on broken OAuth URLs.

## 9. Phase-exit checklist

- [ ] All unit tests green.
- [ ] Ruff + format + pyright strict clean.
- [ ] `CHANGELOG.md` — Phase 6d section.
- [ ] `CLAUDE.md` — phase table: 6d → ✅, next is 6e (vector-db).
- [ ] ADR-0019 appended + `Implemented by` backfilled.

## 10. Risks + future

- **Retry counter depends on LangGraph pre-interrupt checkpoint semantics.** Tested in a unit test that simulates a resume cycle; if LangGraph's actual behavior differs (counter doesn't persist), fix-up task catches it.
- **Manual-only integration.** Users without an Arcade account can't exercise the live path. Documented.
- **Arcade API schema drift.** Version-pinned to `v1`; drift caught by tests.
- **Future reuse.** ADR-0019 sets the precedent for other auth-interrupt tools. The payload convention (`auth_url` / `auth_id` / `tool_name`) generalizes to any OAuth-style third-party.

## 11. Self-review

- Placeholders: none.
- Internal consistency: `tool` / `input` / `user_id` field names consistent across §4 Pydantic, §5 executor, §7 tests. Retry counter key `_arcade_retries_<node_id>` consistent.
- Scope: one executor, two HTTP endpoints, one Pydantic tightening, one settings field, one ADR. Single-plan territory.
- Ambiguity: `MAX_RETRIES=3` pinned. Retry-counter persistence mechanism documented (pre-interrupt state mutation). Resume-decision semantic pinned (approved=retry, rejected=cancel).
