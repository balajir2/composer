# Approve-via-Email + Waiting-Approval Auto-Expiry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a `user-approval` node email its approver a one-click Approve/Reject link (no login required), and auto-expire `waiting_approval` executions that sit unresolved past a configurable timeout.

**Architecture:** Two new signed-JWT decision tokens (approve/reject) get emailed the moment a graph pauses at a `user-approval` node with `approverEmail` set. A new public endpoint resolves either link by verifying the token, performing the same resume logic as the authenticated in-app path, and redirecting the browser to a frontend confirmation page. A new sweeper check marks stale `waiting_approval` rows failed after a longer, independent timeout.

**Tech Stack:** FastAPI, python-jose (existing JWT lib), Prisma, LangGraph 1.1.8, Resend (existing email integration), Next.js/React (frontend confirmation page + node panel fields).

---

### Task 1: `approval_email` JWT token type

**Files:**
- Modify: `src/security/jwt.py`
- Test: `tests/unit/security/test_jwt.py`

- [ ] **Step 1: Write the failing tests**

```python
# Append to tests/unit/security/test_jwt.py

def test_create_and_verify_approval_email_token() -> None:
    from src.security.jwt import create_approval_email_token, verify_approval_email_token

    token = create_approval_email_token("exec-1", "approval-1", "approved")
    payload = verify_approval_email_token(token)
    assert payload.sub == "exec-1"
    assert payload.node_id == "approval-1"
    assert payload.decision == "approved"
    assert payload.type == "approval_email"


def test_approval_email_token_rejects_access_token() -> None:
    from src.security.jwt import create_access_token, verify_approval_email_token

    access = create_access_token("u1")
    with pytest.raises(TokenVerificationError):
        verify_approval_email_token(access)


def test_approval_email_token_respects_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.config import get_settings
    from src.security.jwt import create_approval_email_token, verify_approval_email_token

    monkeypatch.setenv("APPROVAL_LINK_TTL_HOURS", "0")
    get_settings.cache_clear()
    token = create_approval_email_token("exec-1", "approval-1", "rejected")
    time.sleep(2.2)
    with pytest.raises(TokenVerificationError):
        verify_approval_email_token(token)
    get_settings.cache_clear()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/security/test_jwt.py -k approval_email -v`
Expected: FAIL with `ImportError: cannot import name 'create_approval_email_token'`

- [ ] **Step 3: Add the new setting `approval_link_ttl_hours`**

In `src/config.py`, right after `rate_limit_forgot_password_per_minute: int = 5` (inside the rate-limits section is fine, or add a new small section — place it near `frontend_url`/`backend_public_url` since Task 3 will add `backend_public_url` in the same area):

```python
    # ─── Approve-via-email (2026-07-11) ──────────
    approval_link_ttl_hours: int = Field(
        default=72,
        description=(
            "How long an emailed approve/reject link stays valid. After it "
            "expires, in-app approval (POST /executions/{id}/resume) still "
            "works — this only bounds the emailed shortcut."
        ),
    )
```

- [ ] **Step 4: Implement the token type**

In `src/security/jwt.py`, add after `PasswordChangeTokenPayload`:

```python
class ApprovalEmailTokenPayload(BaseModel):
    sub: str  # execution_id
    node_id: str
    decision: str  # "approved" | "rejected"
    iat: int
    exp: int
    type: str = "approval_email"
```

Add after `create_password_change_token`:

```python
def create_approval_email_token(execution_id: str, node_id: str, decision: str) -> str:
    settings = get_settings()
    now = _now()
    payload = ApprovalEmailTokenPayload(
        sub=execution_id,
        node_id=node_id,
        decision=decision,
        iat=now,
        exp=now + settings.approval_link_ttl_hours * 3600,
    )
    return _encode(payload)
```

Add after `verify_password_change_token`:

```python
def verify_approval_email_token(token: str) -> ApprovalEmailTokenPayload:
    raw = _decode(token)
    if raw.get("type") != "approval_email":
        raise TokenVerificationError(
            f"Expected token type 'approval_email', got {raw.get('type')!r}"
        )
    return ApprovalEmailTokenPayload.model_validate(raw)
```

Update `__all__` to add `"ApprovalEmailTokenPayload"`, `"create_approval_email_token"`, `"verify_approval_email_token"` (keep alphabetical within each group, matching the existing list's style).

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/security/test_jwt.py -v`
Expected: PASS (all, including the 3 new ones)

- [ ] **Step 6: Commit**

```bash
git add src/security/jwt.py src/config.py tests/unit/security/test_jwt.py
git commit -m "feat(security): add approval_email JWT token type"
```

---

### Task 2: `Approval` model schema changes

**Files:**
- Modify: `prisma/schema.prisma`
- Test: `tests/unit/api/test_executions_resume.py` (or wherever existing resume tests live — locate via `grep -rl "approverUserId" tests/`)

- [ ] **Step 1: Locate existing resume/Approval tests**

Run: `grep -rl "db.approval.create\|approverUserId" tests/`
Read whatever file(s) that returns before editing, so the new nullable field doesn't break an existing assertion that assumed `approverUserId` is always present in the mock row shape.

- [ ] **Step 2: Update the Prisma schema**

In `prisma/schema.prisma`, change the `Approval` model:

```prisma
model Approval {
  id               String            @id @default(cuid())
  executionId      String            @map("execution_id")
  nodeId           String            @map("node_id")
  approverUserId   String?           @map("approver_user_id")
  approverEmail    String?           @map("approver_email")
  viaEmailLink     Boolean           @default(false) @map("via_email_link")
  decision         ApprovalDecision
  note             String?
  createdAt        DateTime          @default(now()) @map("created_at")

  execution        WorkflowExecution @relation(fields: [executionId], references: [id], onDelete: Cascade)

  @@map("approvals")
  @@index([executionId])
  @@index([approverUserId])
}
```

(Only `approverUserId` changed `String` → `String?`; `approverEmail` and `viaEmailLink` are new.)

- [ ] **Step 3: Generate the Prisma client and create the migration**

Run: `uv run prisma generate`
Run: `uv run prisma migrate dev --name approval_email_fields`
Expected: migration file created under `prisma/migrations/`, applies cleanly against the dev database.

- [ ] **Step 4: Run the full existing test suite to catch any break from the nullable change**

Run: `uv run pytest tests/unit -k approval -v`
Expected: PASS. If anything constructs an `Approval`-shaped mock dict and asserts on `approverUserId` being present/required, it should still pass since existing call sites still always set it — only new code paths (Task 8) will leave it null.

- [ ] **Step 5: Commit**

```bash
git add prisma/schema.prisma prisma/migrations
git commit -m "feat(db): make Approval.approverUserId nullable, add approverEmail + viaEmailLink"
```

---

### Task 3: `backend_public_url` and `approval_wait_timeout_hours` settings

**Files:**
- Modify: `src/config.py`
- Modify: `.env.example`
- Test: none needed (pure config additions; covered indirectly by Tasks 4/7's tests)

- [ ] **Step 1: Add the settings**

In `src/config.py`, in the same `# ─── Approve-via-email (2026-07-11) ──────────` section started in Task 1:

```python
    backend_public_url: str = Field(
        default="http://localhost:8000",
        description=(
            "Public base URL of THIS backend (not the frontend) — used to "
            "build emailed approve/reject links, which must resolve directly "
            "to the backend since they trigger a server-side resume. "
            "Production sets this to the Cloud Run backend service URL."
        ),
    )
    approval_wait_timeout_hours: int = Field(
        default=168,
        description=(
            "Auto-fail a waiting_approval execution after this many hours "
            "with no decision, independent of the emailed link's own shorter "
            "TTL — bounds Postgres/checkpoint row growth from runs nobody "
            "ever approves or rejects."
        ),
    )
```

- [ ] **Step 2: Add matching `.env.example` entries**

In `.env.example`, near the existing `FRONTEND_URL=` line:

```
# Approve-via-email (2026-07-11)
BACKEND_PUBLIC_URL=http://localhost:8000
APPROVAL_LINK_TTL_HOURS=72
APPROVAL_WAIT_TIMEOUT_HOURS=168
```

- [ ] **Step 3: Verify settings load correctly**

Run: `uv run python -c "from src.config import get_settings; s = get_settings(); print(s.backend_public_url, s.approval_link_ttl_hours, s.approval_wait_timeout_hours)"`
Expected: prints the three defaults with no error.

- [ ] **Step 4: Commit**

```bash
git add src/config.py .env.example
git commit -m "feat(config): add backend_public_url + approval_wait_timeout_hours settings"
```

---

### Task 4: `UserApprovalNodeData` gains `approverEmail`/`approverCc`

**Files:**
- Modify: `src/engine/workflow.py`
- Test: `tests/unit/engine/test_workflow.py` (or wherever `UserApprovalNodeData` is covered — locate via `grep -rl "UserApprovalNodeData\|approval_message" tests/`)

- [ ] **Step 1: Locate the existing test coverage**

Run: `grep -rl "UserApprovalNodeData\|approvalMessage" tests/unit/engine/`
Read the file that returns.

- [ ] **Step 2: Write the failing test**

Append to that file:

```python
def test_user_approval_node_parses_approver_email_and_cc() -> None:
    from src.engine.workflow import UserApprovalNode

    node = UserApprovalNode.model_validate(
        {
            "id": "a1",
            "type": "user-approval",
            "position": {"x": 0, "y": 0},
            "data": {
                "label": "User Approval",
                "approvalMessage": "Approve?",
                "approverEmail": "reviewer@example.com",
                "approverCc": "manager@example.com",
            },
        }
    )
    assert node.data.approver_email == "reviewer@example.com"
    assert node.data.approver_cc == "manager@example.com"


def test_user_approval_node_approver_fields_optional() -> None:
    from src.engine.workflow import UserApprovalNode

    node = UserApprovalNode.model_validate(
        {
            "id": "a1",
            "type": "user-approval",
            "position": {"x": 0, "y": 0},
            "data": {"label": "User Approval", "approvalMessage": "Approve?"},
        }
    )
    assert node.data.approver_email is None
    assert node.data.approver_cc is None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/unit/engine/ -k approver_email -v`
Expected: FAIL — `approver_email` not a recognized field (or silently absorbed by `extra="allow"` and the assertion fails since it's `None` via extras rather than a real field — either way, the test fails until the field exists as a proper typed attribute).

- [ ] **Step 4: Add the fields**

In `src/engine/workflow.py`, find `class UserApprovalNodeData(BaseNodeData):` and change it to:

```python
class UserApprovalNodeData(BaseNodeData):
    approval_message: str | None = Field(default=None, alias="approvalMessage")
    approver_email: str | None = Field(default=None, alias="approverEmail")
    approver_cc: str | None = Field(default=None, alias="approverCc")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/unit/engine/ -k approver_email -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/engine/workflow.py tests/unit/engine/
git commit -m "feat(engine): add approverEmail/approverCc to UserApprovalNodeData"
```

---

### Task 5: Fold approver fields into the `interrupt()` payload

**Files:**
- Modify: `src/executors/user_approval.py`
- Test: `tests/unit/executors/test_user_approval_executor.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/executors/test_user_approval_executor.py` (check the exact fixture/helper names already in that file first via `Read`, then match them — the sketch below assumes a `_node(...)` helper exists; adapt to whatever the file actually calls it):

```python
async def test_arun_includes_approver_fields_in_interrupt_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.engine.state import initial_state
    from src.engine.workflow import UserApprovalNode
    from src.executors.user_approval import UserApprovalExecutor

    captured: dict[str, Any] = {}

    def fake_interrupt(value: dict[str, Any]) -> str:
        captured.update(value)
        return "approved"

    monkeypatch.setattr("src.executors.user_approval.interrupt", fake_interrupt)

    node = UserApprovalNode.model_validate(
        {
            "id": "a1",
            "type": "user-approval",
            "position": {"x": 0, "y": 0},
            "data": {
                "label": "User Approval",
                "approvalMessage": "Approve {{thing}}?",
                "approverEmail": "reviewer@example.com",
                "approverCc": "manager@example.com",
            },
        }
    )
    state = initial_state()
    state["variables"]["thing"] = "the BRD"
    await UserApprovalExecutor(node).arun(state)

    assert captured["prompt"] == "Approve the BRD?"
    assert captured["approver_email"] == "reviewer@example.com"
    assert captured["approver_cc"] == "manager@example.com"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/executors/test_user_approval_executor.py -k approver_fields -v`
Expected: FAIL — `KeyError: 'approver_email'`

- [ ] **Step 3: Update the executor**

In `src/executors/user_approval.py`, change:

```python
        decision = interrupt({"node_id": self.node.id, "prompt": prompt})
```

to:

```python
        decision = interrupt(
            {
                "node_id": self.node.id,
                "prompt": prompt,
                "approver_email": substitute(self.node.data.approver_email or "", state),
                "approver_cc": substitute(self.node.data.approver_cc or "", state),
            }
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/executors/test_user_approval_executor.py -v`
Expected: PASS (all, including pre-existing tests in that file)

- [ ] **Step 5: Commit**

```bash
git add src/executors/user_approval.py tests/unit/executors/test_user_approval_executor.py
git commit -m "feat(executors): fold approverEmail/approverCc into user-approval interrupt payload"
```

---

### Task 6: `send_approval_email` helper

**Files:**
- Create: `src/engine/approval_email.py`
- Test: `tests/unit/engine/test_approval_email.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/engine/test_approval_email.py
"""Tests for the approve-via-email helper."""

from unittest.mock import AsyncMock

import pytest


async def test_send_approval_email_noop_when_no_approver(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.engine.approval_email import send_approval_email

    send_mock = AsyncMock()
    monkeypatch.setattr(
        "src.engine.approval_email.ResendEmailProvider.send_email", send_mock
    )
    await send_approval_email(
        execution_id="exec-1",
        node_id="approval-1",
        prompt="Approve?",
        approver_email="",
        approver_cc=None,
    )
    send_mock.assert_not_awaited()


async def test_send_approval_email_sends_with_both_links(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.engine.approval_email import send_approval_email

    monkeypatch.setenv("BACKEND_PUBLIC_URL", "https://api.example.com")
    monkeypatch.setenv("RESEND_FROM_EMAIL", "noreply@example.com")
    from src.config import get_settings

    get_settings.cache_clear()

    captured: dict[str, object] = {}

    async def fake_send_email(self: object, payload: dict[str, object], **kw: object) -> dict[str, object]:
        captured.update(payload)
        return {"id": "email-1"}

    monkeypatch.setattr(
        "src.engine.approval_email.ResendEmailProvider.send_email", fake_send_email
    )

    await send_approval_email(
        execution_id="exec-1",
        node_id="approval-1",
        prompt="Approve the BRD?",
        approver_email="reviewer@example.com",
        approver_cc="manager@example.com",
    )

    assert captured["to"] == ["reviewer@example.com"]
    assert captured["cc"] == ["manager@example.com"]
    assert "Approve the BRD?" in captured["html"]
    assert "https://api.example.com/approvals/email/" in captured["html"]
    # Two distinct links (approve + reject) must both be present.
    assert captured["html"].count("https://api.example.com/approvals/email/") == 2

    get_settings.cache_clear()


async def test_send_approval_email_omits_cc_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.engine.approval_email import send_approval_email

    captured: dict[str, object] = {}

    async def fake_send_email(self: object, payload: dict[str, object], **kw: object) -> dict[str, object]:
        captured.update(payload)
        return {"id": "email-1"}

    monkeypatch.setattr(
        "src.engine.approval_email.ResendEmailProvider.send_email", fake_send_email
    )

    await send_approval_email(
        execution_id="exec-1",
        node_id="approval-1",
        prompt="Approve?",
        approver_email="reviewer@example.com",
        approver_cc="",
    )
    assert "cc" not in captured
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/engine/test_approval_email.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.engine.approval_email'`

- [ ] **Step 3: Implement the helper**

```python
# src/engine/approval_email.py
"""Approve-via-email: renders and sends the emailed decision links.

Called exactly once per genuine pause (from LangGraphExecutor.run's and
.resume's "just detected a fresh interrupt" branches) — never from inside
UserApprovalExecutor itself, since LangGraph replays a node's function body
on resume and a send here would be a non-idempotent side effect re-fired
on every resume. See docs/archive/phase-history/specs/2026-07-11-approval-email-notifications-design.md §C.
"""

from typing import Any

from src.config import get_settings
from src.integrations.email.resend import ResendEmailProvider
from src.security.jwt import create_approval_email_token


async def send_approval_email(
    *,
    execution_id: str,
    node_id: str,
    prompt: str,
    approver_email: str,
    approver_cc: str | None,
) -> None:
    """No-ops if approver_email is empty — email approval is opt-in per node."""
    if not approver_email:
        return

    settings = get_settings()
    approve_token = create_approval_email_token(execution_id, node_id, "approved")
    reject_token = create_approval_email_token(execution_id, node_id, "rejected")
    approve_url = f"{settings.backend_public_url}/approvals/email/{approve_token}"
    reject_url = f"{settings.backend_public_url}/approvals/email/{reject_token}"

    html = (
        f"<p>{prompt}</p>"
        f'<p><a href="{approve_url}" style="background:#16a34a;color:#fff;padding:10px 20px;'
        'text-decoration:none;border-radius:6px;margin-right:12px;">Approve</a>'
        f'<a href="{reject_url}" style="background:#dc2626;color:#fff;padding:10px 20px;'
        'text-decoration:none;border-radius:6px;">Reject</a></p>'
        f"<p style='color:#666;font-size:12px'>This link expires in "
        f"{settings.approval_link_ttl_hours} hours. If it expires, you (or "
        "an admin) can still approve or reject from within Composer.</p>"
    )

    payload: dict[str, Any] = {
        "from": settings.resend_from_email,
        "to": [approver_email],
        "subject": "Approval needed",
        "html": html,
    }
    if approver_cc:
        payload["cc"] = [approver_cc]

    await ResendEmailProvider(settings.resend_api_key).send_email(payload)


__all__ = ["send_approval_email"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/engine/test_approval_email.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/engine/approval_email.py tests/unit/engine/test_approval_email.py
git commit -m "feat(engine): add send_approval_email helper"
```

---

### Task 7: Wire the send into `LangGraphExecutor` + stamp `_pending_approval_since`

**Files:**
- Modify: `src/engine/langgraph_executor.py`
- Test: `tests/unit/engine/test_langgraph_executor.py` (locate exact file via `grep -rl "_mark_waiting_approval\|waiting_approval" tests/unit/engine/`)

- [ ] **Step 1: Read the existing pause-detection test file**

Read `tests/unit/engine/test_langgraph_executor.py` in full — it already has `test_run_marks_waiting_approval_on_interrupt` and `test_run_emits_approval_pending_on_pause`, whose exact `_FakeCompiled`/`_FakeSnapPaused`/`_FakeTask`/`_FakeInterrupt` fixture pattern the new tests below reuse verbatim.

- [ ] **Step 2: Write the failing tests**

Append to `tests/unit/engine/test_langgraph_executor.py`:

```python
async def test_run_sends_approval_email_when_approver_email_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from typing import ClassVar
    from unittest.mock import AsyncMock

    from src.engine import langgraph_executor as lge_mod

    send_mock = AsyncMock()
    monkeypatch.setattr(lge_mod, "send_approval_email", send_mock)

    db = MagicMock()
    db.workflowexecution = MagicMock()
    db.workflowexecution.find_unique = AsyncMock(
        return_value=SimpleNamespace(
            id="e1", workflowId="w1", userId="dev", threadId="t1", input=None
        )
    )
    db.workflow = MagicMock()
    db.workflow.find_unique = AsyncMock(
        return_value=SimpleNamespace(
            id="w1",
            name="t",
            nodes=[
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                {
                    "id": "ua",
                    "type": "user-approval",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": "UA",
                        "approvalMessage": "Approve?",
                        "approverEmail": "reviewer@example.com",
                    },
                },
                {"id": "a", "type": "end", "position": {"x": 0, "y": 0}, "data": {"label": "A"}},
                {"id": "b", "type": "end", "position": {"x": 0, "y": 0}, "data": {"label": "B"}},
            ],
            edges=[
                {"id": "e1", "source": "s", "target": "ua"},
                {"id": "e2", "source": "ua", "target": "a", "branch": "approved"},
                {"id": "e3", "source": "ua", "target": "b", "branch": "rejected"},
            ],
        )
    )
    db.workflowexecution.update = AsyncMock()

    class _FakeInterrupt:
        value: ClassVar[dict[str, str]] = {
            "node_id": "ua",
            "prompt": "Approve?",
            "approver_email": "reviewer@example.com",
            "approver_cc": "",
        }

    class _FakeTask:
        interrupts: ClassVar[tuple[_FakeInterrupt, ...]] = (_FakeInterrupt(),)

    class _FakeSnapPaused:
        next: ClassVar[tuple[str, ...]] = ("ua",)
        tasks: ClassVar[tuple[_FakeTask, ...]] = (_FakeTask(),)

    class _FakeCompiled:
        async def ainvoke(self, *a: Any, **kw: Any) -> dict[str, Any]:
            return {"variables": {"input": ""}, "node_results": {}}

        async def aget_state(self, *a: Any, **kw: Any) -> Any:
            return _FakeSnapPaused()

    monkeypatch.setattr(lge_mod, "build_graph", lambda *a, **kw: _FakeCompiled())  # pyright: ignore[reportUnknownLambdaType]

    orch = LangGraphExecutor(db, MagicMock())
    await orch.run("e1")

    send_mock.assert_awaited_once()
    call_kwargs = send_mock.await_args.kwargs
    assert call_kwargs["approver_email"] == "reviewer@example.com"
    assert call_kwargs["node_id"] == "ua"
    assert call_kwargs["prompt"] == "Approve?"


async def test_mark_waiting_approval_stamps_pending_since() -> None:
    db = MagicMock()
    db.workflowexecution = MagicMock()
    update_calls: list[dict[str, Any]] = []

    async def _update(*, where: Any, data: Any) -> Any:
        update_calls.append({"where": where, "data": data})
        return None

    db.workflowexecution.update = _update

    orch = LangGraphExecutor(db, MagicMock())
    await orch._mark_waiting_approval("e1", {"node_id": "ua", "prompt": "Approve?"}, {})

    saved_vars: dict[str, Any] = update_calls[0]["data"]["variables"].data
    since = saved_vars.get("_pending_approval_since")
    assert since is not None
    from datetime import datetime

    datetime.fromisoformat(since)  # raises if not a valid ISO-8601 string
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/unit/engine/test_langgraph_executor.py -k "approval_email or pending_since" -v`
Expected: FAIL

- [ ] **Step 4: Implement**

In `src/engine/langgraph_executor.py`, add the import:

```python
from src.engine.approval_email import send_approval_email
```

Update `_mark_waiting_approval` to stamp the timestamp:

```python
    async def _mark_waiting_approval(
        self,
        execution_id: str,
        pending_info: dict[str, Any],
        existing_vars: dict[str, Any] | None = None,
    ) -> None:
        """Persist waiting_approval status + pending node/prompt into variables.

        Merges pending markers into existing_vars so pre-interrupt state is preserved.
        """
        merged: dict[str, Any] = {**(existing_vars or {})}
        merged["_pending_approval_node"] = pending_info.get("node_id")
        merged["_pending_approval_prompt"] = pending_info.get("prompt")
        merged["_pending_approval_since"] = datetime.now(UTC).isoformat()

        await self.db.workflowexecution.update(  # pyright: ignore[reportAttributeAccessIssue]
            where={"id": execution_id},
            data={
                "status": "waiting_approval",
                "variables": Json(merged),
            },
        )
```

In `run()`, right after the existing `await self._mark_waiting_approval(execution_id, pending_info, existing_vars)` call (inside the `if snapshot.next:` branch), add:

```python
                await send_approval_email(
                    execution_id=execution_id,
                    node_id=str(pending_info.get("node_id", "")),
                    prompt=str(pending_info.get("prompt", "")),
                    approver_email=str(pending_info.get("approver_email", "")),
                    approver_cc=str(pending_info.get("approver_cc")) or None,
                )
```

Add the identical block in `resume()`'s matching `if snapshot.next:` branch (the chained-pause case), right after its own `await self._mark_waiting_approval(...)` call.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/engine/test_langgraph_executor.py -v`
Expected: PASS (all, including every pre-existing test in the file — this is a well-exercised file, watch for any regression)

- [ ] **Step 6: Commit**

```bash
git add src/engine/langgraph_executor.py tests/unit/engine/test_langgraph_executor.py
git commit -m "feat(engine): send approval email on fresh pause, stamp pending-since timestamp"
```

---

### Task 8: Public `GET /approvals/email/{token}` endpoint

**Files:**
- Create: `src/api/approval_email.py`
- Modify: `src/main.py`
- Test: `tests/unit/api/test_approval_email.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/api/test_approval_email.py
"""Tests for GET /approvals/email/{token}."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient


def _execution_row(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": "exec-1",
        "workflowId": "wf-1",
        "userId": "owner-1",
        "status": "waiting_approval",
        "variables": {"_pending_approval_node": "approval-1"},
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _client(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, MagicMock]:
    from fastapi import FastAPI

    from src.api.approval_email import router as approval_email_router

    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("FRONTEND_URL", "https://app.example.com")
    from src.config import get_settings

    get_settings.cache_clear()
    app = FastAPI()
    app.include_router(approval_email_router)
    db = MagicMock()
    db.workflowexecution = MagicMock()
    db.workflowexecution.find_unique = AsyncMock(return_value=_execution_row())
    db.workflowexecution.update = AsyncMock(return_value=_execution_row(status="running"))
    db.approval = MagicMock()
    db.approval.create = AsyncMock()
    app.state.db = db
    app.state.checkpointer = MagicMock()
    from src.engine.events import ExecutionEventBus

    app.state.event_bus = ExecutionEventBus()
    return TestClient(app), db


def test_valid_token_records_approval_and_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.security.jwt import create_approval_email_token

    client, db = _client(monkeypatch)
    token = create_approval_email_token("exec-1", "approval-1", "approved")
    resp = client.get(f"/approvals/email/{token}", follow_redirects=False)
    assert resp.status_code == 303
    assert "approval-result" in resp.headers["location"]
    assert "status=approved" in resp.headers["location"]

    db.approval.create.assert_awaited_once()
    approval_data = db.approval.create.await_args.kwargs["data"]
    assert approval_data["approverUserId"] is None
    assert approval_data["viaEmailLink"] is True
    assert approval_data["decision"] == "approved"

    db.workflowexecution.update.assert_awaited_once()
    update_data = db.workflowexecution.update.await_args.kwargs["data"]
    assert update_data["status"] == "running"


def test_malformed_token_redirects_to_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _db = _client(monkeypatch)
    resp = client.get("/approvals/email/not-a-real-token", follow_redirects=False)
    assert resp.status_code == 303
    assert "status=invalid" in resp.headers["location"]


def test_already_resolved_execution_redirects_to_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.security.jwt import create_approval_email_token

    client, db = _client(monkeypatch)
    db.workflowexecution.find_unique = AsyncMock(
        return_value=_execution_row(status="completed")
    )
    token = create_approval_email_token("exec-1", "approval-1", "approved")
    resp = client.get(f"/approvals/email/{token}", follow_redirects=False)
    assert resp.status_code == 303
    assert "status=invalid" in resp.headers["location"]
    db.approval.create.assert_not_awaited()


def test_node_mismatch_redirects_to_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guards against a stale token from an earlier, already-passed pause
    (e.g. a chained second user-approval node) resolving the wrong gate."""
    from src.security.jwt import create_approval_email_token

    client, db = _client(monkeypatch)
    db.workflowexecution.find_unique = AsyncMock(
        return_value=_execution_row(variables={"_pending_approval_node": "approval-2"})
    )
    token = create_approval_email_token("exec-1", "approval-1", "approved")
    resp = client.get(f"/approvals/email/{token}", follow_redirects=False)
    assert resp.status_code == 303
    assert "status=invalid" in resp.headers["location"]
    db.approval.create.assert_not_awaited()


def test_execution_not_found_redirects_to_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.security.jwt import create_approval_email_token

    client, db = _client(monkeypatch)
    db.workflowexecution.find_unique = AsyncMock(return_value=None)
    token = create_approval_email_token("exec-1", "approval-1", "approved")
    resp = client.get(f"/approvals/email/{token}", follow_redirects=False)
    assert resp.status_code == 303
    assert "status=invalid" in resp.headers["location"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/api/test_approval_email.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.api.approval_email'`

- [ ] **Step 3: Implement the endpoint**

```python
# src/api/approval_email.py
"""Public, unauthenticated endpoint that resolves an emailed approve/reject link.

No login required by design — the signed token itself is the credential,
so external reviewers with no Composer account can decide. Single-use falls
out for free: resuming flips execution status away from 'waiting_approval'
immediately, so a second click on either link (or a stale one from an
earlier chained pause) naturally lands on the "no longer valid" redirect.
"""

from urllib.parse import urlencode

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import RedirectResponse
from starlette import status

from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]
from src.config import get_settings
from src.security.jwt import TokenVerificationError, verify_approval_email_token
from src.storage.db import get_db

router = APIRouter(tags=["approvals"])


def _redirect(status_param: str) -> RedirectResponse:
    settings = get_settings()
    params = urlencode({"status": status_param})
    return RedirectResponse(
        url=f"{settings.frontend_url}/approval-result?{params}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/approvals/email/{token}")
async def resolve_approval_email(
    token: str,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
) -> RedirectResponse:  # pyright: ignore[reportUnusedFunction]
    try:
        claims = verify_approval_email_token(token)
    except TokenVerificationError:
        return _redirect("invalid")

    execution = await db.workflowexecution.find_unique(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": claims.sub}
    )
    if execution is None or execution.status != "waiting_approval":
        return _redirect("invalid")

    variables = execution.variables or {}
    pending_node_id = variables.get("_pending_approval_node") if isinstance(variables, dict) else None
    if pending_node_id != claims.node_id:
        return _redirect("invalid")

    await db.approval.create(  # pyright: ignore[reportAttributeAccessIssue]
        data={
            "executionId": claims.sub,
            "nodeId": claims.node_id,
            "approverUserId": None,
            "approverEmail": None,  # token doesn't carry the address; see Task 8 follow-up note below
            "viaEmailLink": True,
            "decision": claims.decision,
        }
    )

    await db.workflowexecution.update(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": claims.sub},
        data={"status": "running"},
    )

    checkpointer = getattr(request.app.state, "checkpointer", None)
    event_bus = getattr(request.app.state, "event_bus", None)
    from src.engine.langgraph_executor import LangGraphExecutor

    executor = LangGraphExecutor(db=db, checkpointer=checkpointer, event_bus=event_bus)
    background_tasks.add_task(executor.resume, claims.sub, claims.decision)

    return _redirect(claims.decision)


__all__ = ["router"]
```

**Note on `approverEmail: None` above:** the `ApprovalEmailTokenPayload` (Task 1) doesn't carry the approver's address — only `execution_id`/`node_id`/`decision`. To populate `approverEmail` on the audit row accurately, either (a) add `approver_email` as a fourth claim on the token (simple, slightly bigger token), or (b) read it back from `execution.variables["_pending_approval_node_email"]` if Task 7 is extended to also stash it there. Pick (a) at implementation time — it's one extra field on an already-established payload shape (Task 1) and keeps this endpoint fully self-contained without needing yet another `variables` key. If you go with (a), revisit Task 1's payload/tests and this endpoint together before considering Task 8 done.

- [ ] **Step 4: Register the router**

In `src/main.py`, add the import alongside the others:

```python
from src.api.approval_email import router as approval_email_router
```

And register it alongside the other `app.include_router(...)` calls:

```python
    app.include_router(approval_email_router)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/api/test_approval_email.py -v`
Expected: PASS

- [ ] **Step 6: Run the full backend suite to check for regressions**

Run: `uv run pytest -m "not integration"`
Expected: PASS, no regressions

- [ ] **Step 7: Commit**

```bash
git add src/api/approval_email.py src/main.py tests/unit/api/test_approval_email.py
git commit -m "feat(api): add public GET /approvals/email/{token} resolver"
```

---

### Task 9: Sweeper extension — auto-expire stale `waiting_approval` rows

**Files:**
- Modify: `src/maintenance/execution_sweeper.py`
- Modify: `src/main.py`
- Test: `tests/unit/maintenance/test_execution_sweeper.py`

- [ ] **Step 1: Read the existing sweeper test file**

Read `tests/unit/maintenance/test_execution_sweeper.py` in full. It defines `_Row` (a plain dataclass: `id`, `status`, `startedAt`, `error`, `completedAt` — **no `variables` field yet**, needs one added), `_ExecutionTable` (`find_many`/`update` stubs), `_DbStub`, and a fixed `_now()`. Two existing tests — `test_start_sweeper_disabled_returns_none` and `test_start_and_stop_sweeper_runs_at_least_once` — call `start_sweeper(app, db, interval_seconds=..., stuck_after_seconds=900)` with no third timing argument; since `start_sweeper` gains a new required `approval_timeout_hours` parameter in this task, **both existing call sites need updating too** (Step 4 below), not just new tests added.

- [ ] **Step 2: Add a `variables` field to `_Row`**

```python
@dataclass
class _Row:
    id: str
    status: str
    startedAt: datetime
    error: str | None = None
    completedAt: datetime | None = None
    variables: dict[str, Any] | None = None
```

- [ ] **Step 3: Write the failing tests**

Append to `tests/unit/maintenance/test_execution_sweeper.py`:

```python
from src.maintenance.execution_sweeper import sweep_expired_approvals  # add to existing import block


async def test_stale_waiting_approval_rows_are_marked_failed() -> None:
    now = _now()
    stale_since = (now - timedelta(hours=200)).isoformat()
    db = _DbStub()
    db.workflowexecution.rows = [
        _Row(
            id="exe-stale",
            status="waiting_approval",
            startedAt=now - timedelta(hours=200),
            variables={"_pending_approval_since": stale_since},
        ),
    ]

    result = await sweep_expired_approvals(db, timeout_hours=168, now=now)

    assert result == SweepResult(scanned=1, marked_failed=1)
    [(exec_id, data)] = db.workflowexecution.update_calls
    assert exec_id == "exe-stale"
    assert data["status"] == "failed"
    assert "expired waiting for approval" in data["error"].lower()
    assert data["completedAt"] == now


async def test_recent_waiting_approval_rows_are_skipped() -> None:
    now = _now()
    fresh_since = (now - timedelta(hours=1)).isoformat()
    db = _DbStub()
    db.workflowexecution.rows = [
        _Row(
            id="exe-fresh",
            status="waiting_approval",
            startedAt=now - timedelta(hours=1),
            variables={"_pending_approval_since": fresh_since},
        ),
    ]

    result = await sweep_expired_approvals(db, timeout_hours=168, now=now)

    assert result == SweepResult(scanned=1, marked_failed=0)
    assert db.workflowexecution.update_calls == []


async def test_waiting_approval_rows_without_timestamp_are_skipped() -> None:
    """Defensive: a row somehow missing the stamp (e.g. pre-migration data)
    is left alone rather than immediately expired."""
    now = _now()
    db = _DbStub()
    db.workflowexecution.rows = [
        _Row(id="exe-no-ts", status="waiting_approval", startedAt=now - timedelta(days=30), variables={}),
    ]

    result = await sweep_expired_approvals(db, timeout_hours=168, now=now)

    assert result == SweepResult(scanned=1, marked_failed=0)
    assert db.workflowexecution.update_calls == []


async def test_running_rows_are_not_touched_by_approval_sweep() -> None:
    """sweep_expired_approvals only ever looks at waiting_approval — a stuck
    running row is sweep_stuck_executions's job, not this one's."""
    now = _now()
    db = _DbStub()
    db.workflowexecution.rows = [
        _Row(id="exe-running", status="running", startedAt=now - timedelta(hours=200)),
    ]

    result = await sweep_expired_approvals(db, timeout_hours=168, now=now)

    assert result == SweepResult(scanned=0, marked_failed=0)


async def test_approval_sweep_zero_timeout_rejected() -> None:
    db = _DbStub()
    with pytest.raises(ValueError, match="must be > 0"):
        await sweep_expired_approvals(db, timeout_hours=0)
```

- [ ] **Step 4: Update the two existing `start_sweeper` call sites**

In the same file, update both existing tests to pass the new argument:

```python
    task = start_sweeper(
        app, db, interval_seconds=0, stuck_after_seconds=900, approval_timeout_hours=168
    )
```

and

```python
    task = start_sweeper(
        app, db, interval_seconds=1, stuck_after_seconds=900, approval_timeout_hours=168
    )
```

(`test_start_sweeper_disabled_returns_none` and `test_start_and_stop_sweeper_runs_at_least_once` respectively — same call shape, just the added kwarg.)

- [ ] **Step 5: Run tests to verify they fail**

Run: `uv run pytest tests/unit/maintenance/test_execution_sweeper.py -k expired_approvals -v`
Expected: FAIL — `ImportError`

- [ ] **Step 6: Implement `sweep_expired_approvals`**

Add to `src/maintenance/execution_sweeper.py`, after `sweep_stuck_executions`:

```python
_APPROVAL_TIMEOUT_ERROR_TEMPLATE = (
    "Execution expired waiting for approval after {hours}h with no decision. "
    "Auto-failed by the approval-timeout sweeper."
)


async def sweep_expired_approvals(
    db: Any,
    *,
    timeout_hours: int,
    now: datetime | None = None,
) -> SweepResult:
    """Find waiting_approval executions past timeout_hours since they entered
    that state, and fail them.

    Independent of sweep_stuck_executions — waiting_approval rows are
    deliberately excluded from that function (see its docstring); they get
    their own, much longer timeout here instead, since this is a
    Postgres/checkpoint-row-growth hygiene measure, not a crash-recovery one
    (a paused execution holds no compute — see
    docs/archive/phase-history/specs/2026-07-11-approval-email-notifications-design.md).
    """
    if timeout_hours <= 0:
        raise ValueError("timeout_hours must be > 0")

    current_time = now or datetime.now(UTC)
    cutoff = current_time - timedelta(hours=timeout_hours)

    rows: list[Any] = await db.workflowexecution.find_many(
        where={"status": "waiting_approval"},
    )

    marked = 0
    for row in rows:
        variables = getattr(row, "variables", None) or {}
        since_raw = variables.get("_pending_approval_since") if isinstance(variables, dict) else None
        if not since_raw:
            continue
        try:
            since = datetime.fromisoformat(since_raw)
        except (TypeError, ValueError):
            continue
        if since >= cutoff:
            continue
        try:
            await db.workflowexecution.update(
                where={"id": row.id},
                data={
                    "status": "failed",
                    "error": _APPROVAL_TIMEOUT_ERROR_TEMPLATE.format(hours=timeout_hours),
                    "completedAt": current_time,
                },
            )
            marked += 1
        except Exception:
            logger.exception(
                "execution_sweeper: failed to mark expired approval %s as failed",
                getattr(row, "id", "<unknown>"),
            )

    if marked > 0:
        logger.info(
            "execution_sweeper: marked %d/%d waiting_approval rows as expired",
            marked,
            len(rows),
        )

    return SweepResult(scanned=len(rows), marked_failed=marked)
```

Update `_sweeper_loop` to also call this each tick:

```python
async def _sweeper_loop(
    db: Any,
    *,
    interval_seconds: int,
    stuck_after_seconds: int,
    approval_timeout_hours: int,
) -> None:
    while True:
        try:
            await sweep_stuck_executions(db, stuck_after_seconds=stuck_after_seconds)
            await sweep_expired_approvals(db, timeout_hours=approval_timeout_hours)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("execution_sweeper: unexpected error in loop")
        await asyncio.sleep(interval_seconds)
```

Update `start_sweeper`'s signature and its call to `_sweeper_loop` to accept and pass through `approval_timeout_hours: int`. Update `__all__` to add `"sweep_expired_approvals"`.

- [ ] **Step 7: Wire the new setting through `main.py`**

In `src/main.py`, update the `start_sweeper(...)` call:

```python
        sweeper_task = start_sweeper(
            app,
            db,
            interval_seconds=settings.execution_sweeper_interval_seconds,
            stuck_after_seconds=settings.execution_stuck_after_seconds,
            approval_timeout_hours=settings.approval_wait_timeout_hours,
        )
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/unit/maintenance/test_execution_sweeper.py -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add src/maintenance/execution_sweeper.py src/main.py tests/unit/maintenance/test_execution_sweeper.py
git commit -m "feat(maintenance): auto-expire stale waiting_approval executions"
```

---

### Task 10: Frontend — node panel fields + `/approval-result` confirmation page

**Files:**
- Modify: `frontend/components/composer/canvas/node-panels/user-approval.tsx`
- Create: `frontend/app/(auth)/approval-result/page.tsx` (or `frontend/app/approval-result/page.tsx` — check whether `user-approval.tsx`'s sibling anonymous pages like `forgot-password` live under a route group by reading `frontend/app/(auth)/` first; this page must be reachable **without** a session, same as `forgot-password`/`reset-password`)
- Modify: `frontend/middleware.ts` (exclude `approval-result` from the auth-required matcher, same pattern as `forgot-password|reset-password`)

- [ ] **Step 1: Read the existing user-approval panel and the forgot-password page for exact patterns to match**

Read `frontend/components/composer/canvas/node-panels/user-approval.tsx` and `frontend/app/(auth)/forgot-password/page.tsx` in full before writing anything.

- [ ] **Step 2: Add the two fields to the node panel**

In `user-approval.tsx`, add (matching the file's existing `Input`/`Label` pattern for `approvalMessage`):

```tsx
<div className="space-y-1">
  <Label htmlFor="approval-approver-email" className="text-xs">
    Approver email (optional)
  </Label>
  <Input
    id="approval-approver-email"
    value={(data.approverEmail as string) ?? ""}
    onChange={(e) => onChange({ approverEmail: e.target.value })}
    placeholder="reviewer@example.com or {{approver}}"
    className="font-mono text-xs"
  />
  <p className="text-[10px] text-muted-foreground">
    If set, this person gets an email with one-click Approve/Reject links —
    no Composer login required. In-app approval always still works too.
  </p>
</div>
<div className="space-y-1">
  <Label htmlFor="approval-approver-cc" className="text-xs">
    CC (optional)
  </Label>
  <Input
    id="approval-approver-cc"
    value={(data.approverCc as string) ?? ""}
    onChange={(e) => onChange({ approverCc: e.target.value })}
    placeholder="manager@example.com"
    className="font-mono text-xs"
  />
</div>
```

- [ ] **Step 3: Create the confirmation page**

```tsx
// frontend/app/(auth)/approval-result/page.tsx
"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { CheckCircle2, XCircle, AlertTriangle } from "lucide-react";

function ApprovalResultInner() {
  const params = useSearchParams();
  const status = params.get("status");

  const content =
    status === "approved"
      ? {
          Icon: CheckCircle2,
          color: "text-emerald-600",
          title: "Approved",
          message: "Your approval has been recorded. The workflow is continuing.",
        }
      : status === "rejected"
        ? {
            Icon: XCircle,
            color: "text-rose-600",
            title: "Rejected",
            message: "Your rejection has been recorded. The workflow has stopped here.",
          }
        : {
            Icon: AlertTriangle,
            color: "text-amber-600",
            title: "Link no longer valid",
            message:
              "This link has expired or the decision may already have been made. If you still need to act on this, open the run in Composer directly.",
          };

  const { Icon, color, title, message } = content;

  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-4 px-4 text-center">
      <Icon className={`size-12 ${color}`} />
      <h1 className="text-xl font-semibold">{title}</h1>
      <p className="max-w-md text-sm text-muted-foreground">{message}</p>
    </div>
  );
}

export default function ApprovalResultPage() {
  return (
    <Suspense fallback={null}>
      <ApprovalResultInner />
    </Suspense>
  );
}
```

- [ ] **Step 4: Exclude the route from auth middleware**

In `frontend/middleware.ts`, find the matcher that currently excludes `login|register|forgot-password|reset-password` and add `approval-result` to that same list.

- [ ] **Step 5: Manual verification**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors

Run: `cd frontend && npx vitest run`
Expected: all existing tests still pass (no test exists yet for this new page — acceptable, it's a thin presentational page; add one only if the implementer judges the branching logic non-trivial enough to warrant it)

- [ ] **Step 6: Commit**

```bash
git add frontend/components/composer/canvas/node-panels/user-approval.tsx frontend/app/\(auth\)/approval-result frontend/middleware.ts
git commit -m "feat(frontend): approver email/cc fields + /approval-result confirmation page"
```

---

### Task 11: Docs

**Files:**
- Modify: `docs/designer-guide.md` (user-approval node-reference section)
- Modify: `docs/decisions.md` (new ADR)
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Update the designer guide**

In `docs/designer-guide.md`'s `#### \`user-approval\`` section, add a short paragraph after the existing content describing `approverEmail`/`approverCc`, the one-click no-login link behavior, the 72h default link TTL with in-app fallback, and the 7-day (168h) outer auto-expiry.

- [ ] **Step 2: Add the ADR**

Append a new `## ADR-0029: Approve-via-email — signed one-click links, not reply-parsing` entry to `docs/decisions.md`, following the exact section format of ADR-0027/ADR-0028 (Status/Context/Decision/Consequences/Implemented by/Related). Cover: why signed links over reply-parsing (spoofing, no inbound-email infra needed); why no login required; why the sweeper timeout is independent of the link TTL; confirmation that paused executions hold no compute (checkpointed to Postgres).

- [ ] **Step 3: Add the CHANGELOG entry**

Add a new `### Added — Approve-via-email + waiting-approval auto-expiry (2026-07-11)` section under `## [Unreleased]`, following the exact style of the Jira-node entry immediately below it (Added/Fixed/Notes subsections, file links).

- [ ] **Step 4: Commit**

```bash
git add docs/designer-guide.md docs/decisions.md CHANGELOG.md
git commit -m "docs: approve-via-email design record + designer guide + changelog"
```

---

### Final verification

- [ ] Run the full suite one more time end to end:

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run pyright src tests
uv run pytest -m "not integration"
cd frontend && npx tsc --noEmit && npx vitest run
```

Expected: everything green, 0 pyright errors, no regressions in any previously-passing test.
