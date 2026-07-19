# Confluence Node Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A new `confluence` node type — fully deterministic (no LLM), four operations: `create_or_update_page`, `get_page`, `get_property`, `set_property` — using Confluence Cloud REST API v1 (`/wiki/rest/api/content`) with Basic auth (email + API token), matching the encrypted-per-node credential pattern the `jira` node already uses.

**Architecture:** One executor class, one operation dispatch, four private methods. `create_or_update_page` looks up an existing page by space+title first (idempotent reruns), creates or updates accordingly, then reconciles labels to exactly the requested set. `get_property`/`set_property` use Confluence's native content-properties API — invisible page metadata, not part of the rendered content — which is what lets a later workflow (not built in this plan) store and retrieve a week-over-week baseline without parsing rendered HTML back into data.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, httpx, pytest + pytest-httpx (backend). Next.js/React, vitest + @testing-library/react (frontend).

**Spec:** `docs/superpowers/specs/2026-07-19-jira-confluence-foundation-design.md` (Component 2). Component 3 (the baseline-via-properties usage pattern) is *not* built here — it's workflow configuration for a future Macy's-flow plan, consuming this node's `get_property`/`set_property` operations.

---

## Scope note (platform vs. customer flow)

Per [[feedback_platform_vs_customer_flow]] — this is generic platform capability. No Macy's-specific space key, page-naming convention, or property key is hardcoded anywhere in this plan's code; those are workflow-level configuration for a separate future plan.

---

### Task 1: Schema — `ConfluenceNodeData` / `ConfluenceNode`

**Files:**
- Modify: `src/engine/workflow.py` (add new classes after `JoinChunksNode`, currently ending line 620; add to the `WorkflowNode` union at lines 647-671; add to `__all__` at lines 697+)
- Test: `tests/unit/engine/test_confluence_node.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/engine/test_confluence_node.py`:

```python
"""Tests for the ConfluenceNode/ConfluenceNodeData schema."""

from typing import Any

from src.engine.workflow import ConfluenceNode


def _confluence_node_json(**data_overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "label": "Confluence",
        "domain": "test.atlassian.net",
        "email": "test@example.com",
        "apiToken": "plaintext-token",
        "operation": "create_or_update_page",
    }
    data.update(data_overrides)
    return {"id": "c1", "type": "confluence", "position": {"x": 0, "y": 0}, "data": data}


def test_defaults_to_create_or_update_page() -> None:
    node = ConfluenceNode.model_validate(_confluence_node_json())
    assert node.data.operation == "create_or_update_page"


def test_camelcase_fields_parse_with_aliases() -> None:
    node = ConfluenceNode.model_validate(
        _confluence_node_json(
            operation="get_property",
            spaceKey="MB",
            parentPageId="100",
            bodyStorageHtml="<p>hi</p>",
            pageId="123",
            propertyKey="metrics_snapshot",
            propertyValue={"total": 3},
            labels=["weekly-report"],
        )
    )
    assert node.data.space_key == "MB"
    assert node.data.parent_page_id == "100"
    assert node.data.body_storage_html == "<p>hi</p>"
    assert node.data.page_id == "123"
    assert node.data.property_key == "metrics_snapshot"
    assert node.data.property_value == {"total": 3}
    assert node.data.labels == ["weekly-report"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/engine/test_confluence_node.py -v`
Expected: FAIL — `ImportError: cannot import name 'ConfluenceNode' from 'src.engine.workflow'`

- [ ] **Step 3: Add the schema classes**

In `src/engine/workflow.py`, add this block immediately after the `JoinChunksNode` class (currently ending at line 620, right before the `# ─── discriminated union` comment at line 643):

```python
# ─── confluence (foundation) ─────────────────────────────────────────────


class ConfluenceNodeData(BaseNodeData):
    domain: str | None = None
    email: str | None = None
    api_token: str | None = Field(default=None, alias="apiToken")
    operation: Literal[
        "create_or_update_page", "get_page", "get_property", "set_property"
    ] = "create_or_update_page"
    # create_or_update_page / get_page
    space_key: str | None = Field(default=None, alias="spaceKey")
    parent_page_id: str | None = Field(default=None, alias="parentPageId")
    title: str | None = None
    body_storage_html: str | None = Field(default=None, alias="bodyStorageHtml")
    labels: list[str] | None = None
    # get_property / set_property
    page_id: str | None = Field(default=None, alias="pageId")
    property_key: str | None = Field(default=None, alias="propertyKey")
    property_value: Any | None = Field(default=None, alias="propertyValue")


class ConfluenceNode(BaseModel):
    id: str
    type: Literal["confluence"]
    position: Position
    data: ConfluenceNodeData
```

Add `ConfluenceNode` to the `WorkflowNode` union (currently lines 647-671) — insert `| ConfluenceNode` right before the closing `| JiraNode,`:

```python
WorkflowNode = Annotated[
    StartNode
    | EndNode
    | NoteNode
    | FileTriggerNode
    | FileWriteNode
    | AgentNode
    | McpNode
    | IfElseNode
    | WhileNode
    | UserApprovalNode
    | TransformNode
    | DataTransformNode
    | SetStateNode
    | ExtractNode
    | HttpNode
    | GuardrailsNode
    | VectorDbNode
    | GammaAiNode
    | EmailNode
    | ArcadeNode
    | JoinChunksNode
    | JiraNode
    | ConfluenceNode,
    Field(discriminator="type"),
]
```

Add `"ConfluenceNode"` and `"ConfluenceNodeData"` to the `__all__` list (in alphabetical position, matching the existing ordering):

```python
    "ConfluenceNode",
    "ConfluenceNodeData",
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/engine/test_confluence_node.py -v`
Expected: PASS

- [ ] **Step 5: Run the full backend test suite and type check**

Run: `uv run pytest && uv run pyright src tests`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/engine/workflow.py tests/unit/engine/test_confluence_node.py
git commit -m "$(cat <<'EOF'
feat(confluence-node): add ConfluenceNodeData/ConfluenceNode schema

Fully deterministic operation selector (create_or_update_page/get_page/
get_property/set_property), no LLM fields at all. No executor yet — next
task.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Encryption/redaction for the Confluence `apiToken` field

**Files:**
- Modify: `src/api/workflows.py`
- Test: `tests/unit/api/test_workflows_confluence_tokens.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/api/test_workflows_confluence_tokens.py` — this mirrors `tests/unit/api/test_workflows_vector_db_keys.py` exactly, applied to the Confluence node's single `apiToken` field using the same generic `encrypt_marked`/`REDACTED_MARKER` helpers vector-db already uses (not Jira's older bespoke helpers):

```python
"""Tests for confluence node apiToken encryption-at-rest and redaction-on-read.

Mirrors tests/unit/api/test_workflows_vector_db_keys.py's pattern exactly.
"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.main import create_app
from src.security.encryption import REDACTED_MARKER, decrypt_marked


def _confluence_node(**data_overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "label": "Confluence",
        "domain": "test.atlassian.net",
        "email": "test@example.com",
        "apiToken": "plaintext-confluence-token",
        "operation": "create_or_update_page",
        "spaceKey": "MB",
        "title": "Weekly Report",
    }
    data.update(data_overrides)
    return {"id": "c1", "type": "confluence", "position": {"x": 0, "y": 0}, "data": data}


def _nodes_with_confluence(**data_overrides: Any) -> list[dict[str, Any]]:
    return [
        {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
        _confluence_node(**data_overrides),
        {"id": "e", "type": "end", "position": {"x": 100, "y": 0}, "data": {"label": "E"}},
    ]


def _edges() -> list[dict[str, Any]]:
    return [
        {"id": "e1", "source": "s", "target": "c1"},
        {"id": "e2", "source": "c1", "target": "e"},
    ]


def _wf_row(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": "w1",
        "userId": "dev",
        "name": "Sample",
        "description": None,
        "category": None,
        "tags": [],
        "difficulty": None,
        "estimatedTime": None,
        "nodes": _nodes_with_confluence(),
        "edges": _edges(),
        "version": None,
        "isTemplate": False,
        "isPublic": False,
        "createdAt": "2026-07-19T00:00:00Z",
        "updatedAt": "2026-07-19T00:00:00Z",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _client(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, MagicMock]:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()
    db = MagicMock()
    db.workflow = MagicMock()
    db.user = MagicMock()
    db.user.find_unique = AsyncMock(return_value=None)
    db.workflowassignment = MagicMock()
    db.workflowassignment.find_unique = AsyncMock(return_value=None)
    app.state.db = db
    app.state.checkpointer = MagicMock()
    from src.engine.events import ExecutionEventBus

    app.state.event_bus = ExecutionEventBus()
    return TestClient(app), db


def test_post_workflow_encrypts_confluence_token_before_persisting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, db = _client(monkeypatch)
    db.workflow.create = AsyncMock(return_value=_wf_row())

    resp = client.post(
        "/workflows",
        json={"name": "Sample", "nodes": _nodes_with_confluence(), "edges": _edges()},
    )
    assert resp.status_code == 201, resp.text

    resp_node = next(n for n in resp.json()["nodes"] if n["type"] == "confluence")
    assert resp_node["data"]["apiToken"] == REDACTED_MARKER

    call_kwargs = db.workflow.create.await_args.kwargs  # type: ignore[union-attr]
    persisted_nodes = call_kwargs["data"]["nodes"].data
    persisted = next(n for n in persisted_nodes if n["type"] == "confluence")
    stored_token = persisted["data"]["apiToken"]
    assert stored_token != "plaintext-confluence-token"
    assert decrypt_marked(stored_token) == "plaintext-confluence-token"


def test_get_workflow_redacts_confluence_token(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client(monkeypatch)
    db.workflow.find_unique = AsyncMock(return_value=_wf_row())

    resp = client.get("/workflows/w1")
    assert resp.status_code == 200, resp.text
    resp_node = next(n for n in resp.json()["nodes"] if n["type"] == "confluence")
    assert resp_node["data"]["apiToken"] == REDACTED_MARKER
    assert "plaintext-confluence-token" not in resp.text


def test_list_workflows_redacts_confluence_token(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client(monkeypatch)
    db.workflow.count = AsyncMock(return_value=1)
    db.workflow.find_many = AsyncMock(return_value=[_wf_row()])

    resp = client.get("/workflows")
    assert resp.status_code == 200, resp.text
    assert "plaintext-confluence-token" not in resp.text


def test_put_workflow_with_redacted_marker_preserves_stored_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.security.encryption import encrypt_marked

    stored_token = encrypt_marked("original-confluence-token")
    existing = _wf_row(nodes=_nodes_with_confluence(apiToken=stored_token))

    client, db = _client(monkeypatch)
    db.workflow.find_unique = AsyncMock(return_value=existing)
    db.workflow.update = AsyncMock(return_value=existing)

    body = {
        "name": "Sample",
        "nodes": _nodes_with_confluence(apiToken=REDACTED_MARKER),
        "edges": _edges(),
    }
    resp = client.put("/workflows/w1", json=body)
    assert resp.status_code == 200, resp.text

    call_kwargs = db.workflow.update.await_args.kwargs  # type: ignore[union-attr]
    persisted_nodes = call_kwargs["data"]["nodes"].data
    persisted = next(n for n in persisted_nodes if n["type"] == "confluence")
    assert persisted["data"]["apiToken"] == stored_token


def test_put_workflow_with_new_plaintext_token_encrypts_it(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.security.encryption import encrypt_marked

    existing = _wf_row(nodes=_nodes_with_confluence(apiToken=encrypt_marked("old-token")))

    client, db = _client(monkeypatch)
    db.workflow.find_unique = AsyncMock(return_value=existing)
    db.workflow.update = AsyncMock(return_value=existing)

    body = {
        "name": "Sample",
        "nodes": _nodes_with_confluence(apiToken="brand-new-token"),
        "edges": _edges(),
    }
    resp = client.put("/workflows/w1", json=body)
    assert resp.status_code == 200, resp.text

    call_kwargs = db.workflow.update.await_args.kwargs  # type: ignore[union-attr]
    persisted_nodes = call_kwargs["data"]["nodes"].data
    persisted = next(n for n in persisted_nodes if n["type"] == "confluence")
    assert decrypt_marked(persisted["data"]["apiToken"]) == "brand-new-token"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/api/test_workflows_confluence_tokens.py -v`
Expected: FAIL — the confluence node's `apiToken` is persisted/returned as plaintext (no redaction/encryption wired up yet)

- [ ] **Step 3: Wire up redaction and encryption**

In `src/api/workflows.py`, add a constant near `_VECTOR_DB_SECRET_FIELDS` (line 35):

```python
_CONFLUENCE_SECRET_FIELDS = ("apiToken",)
```

Add these two functions right after `_redact_vector_db_keys` (currently ending at line 124):

```python
def _redact_confluence_tokens(nodes: list[Any]) -> list[Any]:
    """Never let a confluence node's encrypted apiToken leave the server —
    same treatment as _redact_vector_db_keys above."""
    redacted: list[Any] = []
    for node in nodes:
        if isinstance(node, dict) and node.get("type") == "confluence":
            data = dict(node.get("data") or {})
            for field in _CONFLUENCE_SECRET_FIELDS:
                if data.get(field):
                    data[field] = REDACTED_MARKER
            node = {**node, "data": data}
        redacted.append(node)
    return redacted
```

Update `_to_workflow_read` (currently lines 144-149) to call it:

```python
def _to_workflow_read(row: Any) -> "WorkflowRead":
    read = WorkflowRead.model_validate(row)
    read.nodes = _redact_jira_tokens(read.nodes)
    read.nodes = _redact_vector_db_keys(read.nodes)
    read.nodes = _redact_http_headers(read.nodes)
    read.nodes = _redact_confluence_tokens(read.nodes)
    return read
```

Add the encryption counterpart right after `_encrypt_vector_db_keys` (currently ending at line 191):

```python
def _encrypt_confluence_tokens(nodes_json: list[dict[str, Any]], existing_nodes: list[Any]) -> None:
    """Encrypt plaintext confluence apiToken values in-place before
    persisting — same preserve-on-redacted-marker treatment as
    _encrypt_vector_db_keys above."""
    existing_by_id = {node.get("id"): node for node in existing_nodes if isinstance(node, dict)}
    for node in nodes_json:
        if node.get("type") != "confluence":
            continue
        data = node.get("data") or {}
        for field in _CONFLUENCE_SECRET_FIELDS:
            value = data.get(field)
            if not value:
                continue
            if value == REDACTED_MARKER:
                prior = existing_by_id.get(node.get("id")) or {}
                data[field] = (prior.get("data") or {}).get(field)
            elif not is_marked_encrypted(value):
                data[field] = encrypt_marked(value)
```

Call it at both existing call sites — after line 305 (`_encrypt_http_headers(nodes_json, existing_nodes=[])`):

```python
    _encrypt_jira_tokens(nodes_json, existing_nodes=[])
    _encrypt_vector_db_keys(nodes_json, existing_nodes=[])
    _encrypt_http_headers(nodes_json, existing_nodes=[])
    _encrypt_confluence_tokens(nodes_json, existing_nodes=[])
```

and after line 488 (`_encrypt_http_headers(nodes_json, existing_nodes=existing_nodes)`):

```python
    _encrypt_jira_tokens(nodes_json, existing_nodes=existing_nodes)
    _encrypt_vector_db_keys(nodes_json, existing_nodes=existing_nodes)
    _encrypt_http_headers(nodes_json, existing_nodes=existing_nodes)
    _encrypt_confluence_tokens(nodes_json, existing_nodes=existing_nodes)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/api/test_workflows_confluence_tokens.py -v`
Expected: PASS

- [ ] **Step 5: Run the full backend test suite**

Run: `uv run pytest && uv run pyright src tests`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/api/workflows.py tests/unit/api/test_workflows_confluence_tokens.py
git commit -m "$(cat <<'EOF'
feat(confluence-node): encrypt apiToken at rest, redact on every read

Same class of gap Jira/vector-db/http already closed (P0-5), applied to
the new confluence node's single apiToken field via the generic
encrypt_marked/REDACTED_MARKER helpers (not Jira's older bespoke ones).

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Executor — the four Confluence operations

**Files:**
- Create: `src/executors/confluence.py`
- Test: `tests/unit/executors/test_confluence_executor.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/executors/test_confluence_executor.py`:

```python
"""Tests for the Confluence executor — deterministic create/update/property operations."""

from typing import Any

import pytest
from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]

from src.engine.state import initial_state
from src.engine.workflow import ConfluenceNode
from src.executors.confluence import (
    ConfluenceConfigError,
    ConfluenceExecutor,
    ConfluenceHttpError,
)
from src.security.encryption import encrypt_marked


def _confluence_node_json(**data_overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "label": "Confluence",
        "domain": "test.atlassian.net",
        "email": "test@example.com",
        "apiToken": "plaintext-token",
        "operation": "create_or_update_page",
    }
    data.update(data_overrides)
    return {"id": "c1", "type": "confluence", "position": {"x": 0, "y": 0}, "data": data}


async def test_create_or_update_page_creates_when_no_existing_page(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    httpx_mock.add_response(method="GET", json={"results": []})  # find-by-title: none  # pyright: ignore[reportUnknownMemberType]
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        method="POST",
        url="https://test.atlassian.net/wiki/rest/api/content",
        json={"id": "123", "version": {"number": 1}},
    )
    httpx_mock.add_response(method="GET", json={"results": []})  # existing labels: none  # pyright: ignore[reportUnknownMemberType]

    node = ConfluenceNode.model_validate(
        _confluence_node_json(
            spaceKey="MB",
            parentPageId="100",
            title="MB - Weekly Delivery Report - 2026-07-19",
            bodyStorageHtml="<p>hello</p>",
        )
    )
    delta = await ConfluenceExecutor(node).arun(initial_state())
    output = delta["variables"]["lastOutput"]
    assert output["pageId"] == "123"
    assert output["version"] == 1
    assert output["created"] is True
    assert "123" in output["url"]


async def test_create_or_update_page_updates_and_reconciles_labels(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    httpx_mock.add_response(  # find-by-title: existing page  # pyright: ignore[reportUnknownMemberType]
        method="GET",
        json={"results": [{"id": "123", "version": {"number": 2}}]},
    )
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        method="PUT",
        url="https://test.atlassian.net/wiki/rest/api/content/123",
        json={"id": "123", "version": {"number": 3}},
    )
    httpx_mock.add_response(  # existing labels: one to keep out, one to remove  # pyright: ignore[reportUnknownMemberType]
        method="GET",
        json={"results": [{"name": "stale-label", "prefix": "global"}]},
    )
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        method="DELETE",
        url="https://test.atlassian.net/wiki/rest/api/content/123/label/stale-label",
        json={},
    )
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        method="POST",
        url="https://test.atlassian.net/wiki/rest/api/content/123/label",
        json={"results": []},
    )

    node = ConfluenceNode.model_validate(
        _confluence_node_json(
            spaceKey="MB",
            title="MB - Weekly Delivery Report - 2026-07-19",
            bodyStorageHtml="<p>updated</p>",
            labels=["weekly-report"],
        )
    )
    delta = await ConfluenceExecutor(node).arun(initial_state())
    output = delta["variables"]["lastOutput"]
    assert output["pageId"] == "123"
    assert output["version"] == 3
    assert output["created"] is False


async def test_get_page_returns_found_false_when_no_match(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    httpx_mock.add_response(method="GET", json={"results": []})  # pyright: ignore[reportUnknownMemberType]

    node = ConfluenceNode.model_validate(
        _confluence_node_json(operation="get_page", spaceKey="MB", title="Not There Yet")
    )
    delta = await ConfluenceExecutor(node).arun(initial_state())
    output = delta["variables"]["lastOutput"]
    assert output == {"found": False, "pageId": None, "bodyStorageHtml": None, "version": None}


async def test_get_page_returns_body_when_found(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        method="GET",
        json={
            "results": [
                {
                    "id": "123",
                    "version": {"number": 4},
                    "body": {"storage": {"value": "<p>existing content</p>"}},
                }
            ]
        },
    )

    node = ConfluenceNode.model_validate(
        _confluence_node_json(operation="get_page", spaceKey="MB", title="Weekly Report")
    )
    delta = await ConfluenceExecutor(node).arun(initial_state())
    output = delta["variables"]["lastOutput"]
    assert output == {
        "found": True,
        "pageId": "123",
        "bodyStorageHtml": "<p>existing content</p>",
        "version": 4,
    }


async def test_get_property_returns_found_false_on_404(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        method="GET",
        url="https://test.atlassian.net/wiki/rest/api/content/123/property/metrics_snapshot",
        status_code=404,
        json={},
    )
    node = ConfluenceNode.model_validate(
        _confluence_node_json(operation="get_property", pageId="123", propertyKey="metrics_snapshot")
    )
    delta = await ConfluenceExecutor(node).arun(initial_state())
    assert delta["variables"]["lastOutput"] == {"found": False, "value": None}


async def test_set_property_creates_when_missing(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    httpx_mock.add_response(  # existence check: not found  # pyright: ignore[reportUnknownMemberType]
        method="GET",
        url="https://test.atlassian.net/wiki/rest/api/content/123/property/metrics_snapshot",
        status_code=404,
        json={},
    )
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        method="POST",
        url="https://test.atlassian.net/wiki/rest/api/content/123/property",
        json={"key": "metrics_snapshot", "version": {"number": 1, "when": "2026-07-19T00:00:00.000Z"}},
    )
    node = ConfluenceNode.model_validate(
        _confluence_node_json(
            operation="set_property",
            pageId="123",
            propertyKey="metrics_snapshot",
            propertyValue={"total": 3},
        )
    )
    delta = await ConfluenceExecutor(node).arun(initial_state())
    output = delta["variables"]["lastOutput"]
    assert output["key"] == "metrics_snapshot"


async def test_set_property_updates_when_existing(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        method="GET",
        url="https://test.atlassian.net/wiki/rest/api/content/123/property/metrics_snapshot",
        json={"key": "metrics_snapshot", "value": {"total": 2}, "version": {"number": 1}},
    )
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        method="PUT",
        url="https://test.atlassian.net/wiki/rest/api/content/123/property/metrics_snapshot",
        json={"key": "metrics_snapshot", "version": {"number": 2, "when": "2026-07-19T00:00:00.000Z"}},
    )
    node = ConfluenceNode.model_validate(
        _confluence_node_json(
            operation="set_property",
            pageId="123",
            propertyKey="metrics_snapshot",
            propertyValue={"total": 3},
        )
    )
    delta = await ConfluenceExecutor(node).arun(initial_state())
    output = delta["variables"]["lastOutput"]
    assert output["key"] == "metrics_snapshot"


async def test_requires_credentials() -> None:
    node = ConfluenceNode.model_validate(
        _confluence_node_json(domain="", email="", apiToken="", operation="get_page", spaceKey="MB", title="X")
    )
    with pytest.raises(ConfluenceConfigError):
        await ConfluenceExecutor(node).arun(initial_state())


async def test_decrypts_stored_token_before_use(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    encrypted = encrypt_marked("real-secret-token")
    httpx_mock.add_response(method="GET", json={"results": []})  # pyright: ignore[reportUnknownMemberType]

    node = ConfluenceNode.model_validate(
        _confluence_node_json(apiToken=encrypted, operation="get_page", spaceKey="MB", title="X")
    )
    await ConfluenceExecutor(node).arun(initial_state())

    req = httpx_mock.get_request()  # pyright: ignore[reportUnknownMemberType]
    assert req is not None
    import base64

    auth_header = req.headers["Authorization"]
    assert auth_header.startswith("Basic ")
    decoded = base64.b64decode(auth_header[len("Basic "):]).decode()
    assert decoded == "test@example.com:real-secret-token"


async def test_non_2xx_non_404_raises_confluence_http_error(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    httpx_mock.add_response(method="GET", status_code=500, text="server error")  # pyright: ignore[reportUnknownMemberType]

    node = ConfluenceNode.model_validate(
        _confluence_node_json(operation="get_page", spaceKey="MB", title="X")
    )
    with pytest.raises(ConfluenceHttpError):
        await ConfluenceExecutor(node).arun(initial_state())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/executors/test_confluence_executor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.executors.confluence'`

- [ ] **Step 3: Implement the executor**

Create `src/executors/confluence.py`:

```python
"""ConfluenceExecutor — the `confluence` node type.

Fully deterministic (no LLM) — operation selector: create_or_update_page,
get_page, get_property, set_property. Uses Confluence Cloud REST API v1
(`/wiki/rest/api/content`), Basic auth (email + API token), same
encrypted-per-node credential pattern as the `jira` node — but the generic
encrypt_marked/decrypt_marked helpers (src/security/encryption.py) rather
than Jira's older bespoke ones, matching how vector-db's secret fields are
handled.
"""

import base64
from typing import Any

import httpx

from src.engine.state import WorkflowStateDict
from src.engine.workflow import ConfluenceNode
from src.executors.base import register_executor
from src.security.encryption import decrypt_marked
from src.variable_substitution import substitute, substitute_in_value


class ConfluenceConfigError(RuntimeError):
    """Raised when required fields for the node's operation are missing."""


class ConfluenceHttpError(RuntimeError):
    """Raised when a Confluence REST call fails unexpectedly (non-2xx, not
    a recognized 'not found' case)."""


def _headers(email: str, api_token: str) -> dict[str, str]:
    encoded = base64.b64encode(f"{email}:{api_token}".encode()).decode()
    return {
        "Authorization": f"Basic {encoded}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _content_url(domain: str, path: str = "") -> str:
    base = f"https://{domain}/wiki/rest/api/content"
    return f"{base}/{path}" if path else base


def _raise_for_unexpected_status(resp: httpx.Response) -> None:
    if resp.status_code >= 400:
        raise ConfluenceHttpError(
            f"Confluence API call failed (HTTP {resp.status_code}): {resp.text[:500]}"
        )


@register_executor("confluence")
class ConfluenceExecutor:
    def __init__(self, node: ConfluenceNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        domain = self.node.data.domain or ""
        email = self.node.data.email or ""
        api_token = decrypt_marked(self.node.data.api_token or "")
        if not all([domain, email, api_token]):
            raise ConfluenceConfigError(
                f"confluence node {self.node.id!r}: domain, email, and apiToken are all required."
            )
        headers = _headers(email, api_token)
        operation = self.node.data.operation

        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
            if operation == "create_or_update_page":
                output = await self._create_or_update_page(client, domain, headers, state)
            elif operation == "get_page":
                output = await self._get_page(client, domain, headers, state)
            elif operation == "get_property":
                output = await self._get_property(client, domain, headers, state)
            else:
                output = await self._set_property(client, domain, headers, state)

        return {
            "variables": {"lastOutput": output},
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "input": {"operation": operation},
                    "output": output,
                }
            },
        }

    async def _find_page(
        self,
        client: httpx.AsyncClient,
        domain: str,
        headers: dict[str, str],
        space_key: str,
        title: str,
    ) -> dict[str, Any] | None:
        resp = await client.get(
            _content_url(domain),
            headers=headers,
            params={"spaceKey": space_key, "title": title, "expand": "body.storage,version"},
        )
        _raise_for_unexpected_status(resp)
        results = resp.json().get("results", [])
        return results[0] if results else None

    async def _create_or_update_page(
        self,
        client: httpx.AsyncClient,
        domain: str,
        headers: dict[str, str],
        state: WorkflowStateDict,
    ) -> dict[str, Any]:
        data = self.node.data
        space_key = substitute(data.space_key or "", state)
        title = substitute(data.title or "", state)
        body_html = substitute(data.body_storage_html or "", state)
        parent_page_id = substitute(data.parent_page_id, state) if data.parent_page_id else None
        labels = data.labels or []
        if not all([space_key, title]):
            raise ConfluenceConfigError(
                f"confluence node {self.node.id!r}: create_or_update_page requires "
                "spaceKey and title."
            )

        existing = await self._find_page(client, domain, headers, space_key, title)
        if existing is None:
            body: dict[str, Any] = {
                "type": "page",
                "title": title,
                "space": {"key": space_key},
                "body": {"storage": {"value": body_html, "representation": "storage"}},
            }
            if parent_page_id:
                body["ancestors"] = [{"id": parent_page_id}]
            resp = await client.post(_content_url(domain), headers=headers, json=body)
            _raise_for_unexpected_status(resp)
            created = resp.json()
            page_id = created["id"]
            version = created["version"]["number"]
            was_created = True
        else:
            page_id = existing["id"]
            next_version = existing["version"]["number"] + 1
            body = {
                "id": page_id,
                "type": "page",
                "title": title,
                "space": {"key": space_key},
                "version": {"number": next_version},
                "body": {"storage": {"value": body_html, "representation": "storage"}},
            }
            resp = await client.put(_content_url(domain, page_id), headers=headers, json=body)
            _raise_for_unexpected_status(resp)
            updated = resp.json()
            page_id = updated["id"]
            version = updated["version"]["number"]
            was_created = False

        await self._reconcile_labels(client, domain, headers, page_id, labels)

        return {
            "pageId": page_id,
            "version": version,
            "url": f"https://{domain}/wiki/spaces/{space_key}/pages/{page_id}",
            "created": was_created,
        }

    async def _reconcile_labels(
        self,
        client: httpx.AsyncClient,
        domain: str,
        headers: dict[str, str],
        page_id: str,
        labels: list[str],
    ) -> None:
        resp = await client.get(_content_url(domain, f"{page_id}/label"), headers=headers)
        _raise_for_unexpected_status(resp)
        current = {entry["name"] for entry in resp.json().get("results", [])}
        desired = set(labels)

        for name in current - desired:
            del_resp = await client.delete(
                _content_url(domain, f"{page_id}/label/{name}"), headers=headers
            )
            _raise_for_unexpected_status(del_resp)

        to_add = desired - current
        if to_add:
            add_resp = await client.post(
                _content_url(domain, f"{page_id}/label"),
                headers=headers,
                json=[{"prefix": "global", "name": name} for name in to_add],
            )
            _raise_for_unexpected_status(add_resp)

    async def _get_page(
        self,
        client: httpx.AsyncClient,
        domain: str,
        headers: dict[str, str],
        state: WorkflowStateDict,
    ) -> dict[str, Any]:
        data = self.node.data
        space_key = substitute(data.space_key or "", state)
        title = substitute(data.title or "", state)
        if not all([space_key, title]):
            raise ConfluenceConfigError(
                f"confluence node {self.node.id!r}: get_page requires spaceKey and title."
            )
        page = await self._find_page(client, domain, headers, space_key, title)
        if page is None:
            return {"found": False, "pageId": None, "bodyStorageHtml": None, "version": None}
        return {
            "found": True,
            "pageId": page["id"],
            "bodyStorageHtml": page.get("body", {}).get("storage", {}).get("value", ""),
            "version": page["version"]["number"],
        }

    async def _get_property(
        self,
        client: httpx.AsyncClient,
        domain: str,
        headers: dict[str, str],
        state: WorkflowStateDict,
    ) -> dict[str, Any]:
        data = self.node.data
        page_id = substitute(data.page_id or "", state)
        property_key = substitute(data.property_key or "", state)
        if not all([page_id, property_key]):
            raise ConfluenceConfigError(
                f"confluence node {self.node.id!r}: get_property requires pageId and propertyKey."
            )
        resp = await client.get(
            _content_url(domain, f"{page_id}/property/{property_key}"), headers=headers
        )
        if resp.status_code == 404:
            return {"found": False, "value": None}
        _raise_for_unexpected_status(resp)
        return {"found": True, "value": resp.json().get("value")}

    async def _set_property(
        self,
        client: httpx.AsyncClient,
        domain: str,
        headers: dict[str, str],
        state: WorkflowStateDict,
    ) -> dict[str, Any]:
        data = self.node.data
        page_id = substitute(data.page_id or "", state)
        property_key = substitute(data.property_key or "", state)
        if not all([page_id, property_key]):
            raise ConfluenceConfigError(
                f"confluence node {self.node.id!r}: set_property requires pageId and propertyKey."
            )
        value = substitute_in_value(data.property_value, state)

        existing = await client.get(
            _content_url(domain, f"{page_id}/property/{property_key}"), headers=headers
        )
        if existing.status_code == 404:
            resp = await client.post(
                _content_url(domain, f"{page_id}/property"),
                headers=headers,
                json={"key": property_key, "value": value},
            )
        else:
            _raise_for_unexpected_status(existing)
            next_version = existing.json()["version"]["number"] + 1
            resp = await client.put(
                _content_url(domain, f"{page_id}/property/{property_key}"),
                headers=headers,
                json={"key": property_key, "value": value, "version": {"number": next_version}},
            )
        _raise_for_unexpected_status(resp)
        result = resp.json()
        return {"key": property_key, "updatedAt": result.get("version", {}).get("when")}


__all__ = [
    "ConfluenceConfigError",
    "ConfluenceExecutor",
    "ConfluenceHttpError",
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/executors/test_confluence_executor.py -v`
Expected: PASS

- [ ] **Step 5: Run the full backend test suite and type check**

Run: `uv run pytest && uv run pyright src tests`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/executors/confluence.py tests/unit/executors/test_confluence_executor.py
git commit -m "$(cat <<'EOF'
feat(confluence-node): implement the four deterministic operations

create_or_update_page (idempotent by space+title, reconciles labels),
get_page, get_property, set_property — all via Confluence Cloud REST API
v1's content and content-properties endpoints. No LLM anywhere in this
node.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Register the executor in the graph builder

**Files:**
- Modify: `src/engine/graph_builder.py:62-66` (insert alphabetically before `join_chunks`)

- [ ] **Step 1: Add the side-effect import**

In `src/engine/graph_builder.py`, insert this block right after the `data_transform` import (currently lines 36-38) and before `email` (currently lines 39-41) — keeping the existing alphabetical ordering:

```python
from src.executors import (
    confluence as _confluence_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
```

- [ ] **Step 2: Verify the node type is now buildable**

Run: `uv run pytest tests/unit/executors/test_confluence_executor.py tests/unit/engine/ -v`
Expected: PASS (no behavior change from this task alone — this just wires registration into the graph compiler's import chain, verified indirectly by the existing test suite still passing since nothing regressed)

- [ ] **Step 3: Run the full backend test suite**

Run: `uv run pytest && uv run pyright src tests`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/engine/graph_builder.py
git commit -m "$(cat <<'EOF'
feat(confluence-node): register executor in the graph builder import chain

Mirrors every other node type's side-effect-import registration pattern —
without this, build_executor() would raise NotImplementedError for any
workflow containing a confluence node.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Designer panel

**Files:**
- Create: `frontend/components/composer/canvas/node-panels/confluence.tsx`
- Test: `frontend/components/composer/canvas/node-panels/confluence.test.tsx` (new)

- [ ] **Step 1: Write the failing test**

Create `frontend/components/composer/canvas/node-panels/confluence.test.tsx`:

```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import ConfluencePanel from "./confluence";

describe("ConfluencePanel", () => {
  it("defaults to create_or_update_page and shows its fields", () => {
    render(<ConfluencePanel data={{}} onChange={vi.fn()} />);
    expect(screen.getByLabelText("Space key")).toBeInTheDocument();
    expect(screen.queryByLabelText("Page ID")).not.toBeInTheDocument();
  });

  it("switching operation to get_property reveals pageId/propertyKey fields", () => {
    render(
      <ConfluencePanel data={{ operation: "get_property" }} onChange={vi.fn()} />
    );
    expect(screen.getByLabelText("Page ID")).toBeInTheDocument();
    expect(screen.getByLabelText("Property key")).toBeInTheDocument();
    expect(screen.queryByLabelText("Space key")).not.toBeInTheDocument();
  });

  it("editing spaceKey calls onChange with spaceKey", () => {
    const onChange = vi.fn();
    render(<ConfluencePanel data={{}} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText("Space key"), {
      target: { value: "MB" },
    });
    expect(onChange).toHaveBeenCalledWith({ spaceKey: "MB" });
  });

  it("editing the comma-separated labels list calls onChange with an array", () => {
    const onChange = vi.fn();
    render(<ConfluencePanel data={{}} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText("Labels"), {
      target: { value: "weekly-report, adobe-target" },
    });
    expect(onChange).toHaveBeenCalledWith({ labels: ["weekly-report", "adobe-target"] });
  });

  it("editing propertyKey in get_property mode calls onChange with propertyKey", () => {
    const onChange = vi.fn();
    render(
      <ConfluencePanel data={{ operation: "get_property" }} onChange={onChange} />
    );
    fireEvent.change(screen.getByLabelText("Property key"), {
      target: { value: "metrics_snapshot" },
    });
    expect(onChange).toHaveBeenCalledWith({ propertyKey: "metrics_snapshot" });
  });

  it("switching operation to set_property reveals pageId/propertyKey/propertyValue and hides spaceKey/title", () => {
    render(
      <ConfluencePanel data={{ operation: "set_property" }} onChange={vi.fn()} />
    );
    expect(screen.getByLabelText("Page ID")).toBeInTheDocument();
    expect(screen.getByLabelText("Property key")).toBeInTheDocument();
    expect(
      screen.getByLabelText("Property value (JSON or text)")
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("Space key")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Title")).not.toBeInTheDocument();
  });

  it("propertyValue round-trips real JSON as a parsed object, not a string", () => {
    const onChange = vi.fn();
    render(
      <ConfluencePanel data={{ operation: "set_property" }} onChange={onChange} />
    );
    fireEvent.change(screen.getByLabelText("Property value (JSON or text)"), {
      target: { value: '{"total": 42}' },
    });
    expect(onChange).toHaveBeenCalledWith({ propertyValue: { total: 42 } });
  });

  it("preserves a non-JSON template reference as a plain string in propertyValue", () => {
    const onChange = vi.fn();
    render(
      <ConfluencePanel data={{ operation: "set_property" }} onChange={onChange} />
    );
    fireEvent.change(screen.getByLabelText("Property value (JSON or text)"), {
      target: { value: "{{compute_metrics.output}}" },
    });
    expect(onChange).toHaveBeenCalledWith({
      propertyValue: "{{compute_metrics.output}}",
    });
  });

  it("resyncs Labels text when switching to a different node, not just on operation change", () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <ConfluencePanel
        data={{ labels: ["summary", "status"] }}
        onChange={onChange}
        currentNodeId="node-a"
      />
    );
    expect(screen.getByLabelText("Labels")).toHaveValue("summary, status");

    rerender(
      <ConfluencePanel
        data={{ labels: ["priority"] }}
        onChange={onChange}
        currentNodeId="node-b"
      />
    );
    expect(screen.getByLabelText("Labels")).toHaveValue("priority");
  });
});
```

Note: `data.labels`/`data.propertyValue` are kept in local component state
(`labelsText`/`propertyValueText`) and re-synced from props via a
`useEffect` keyed on `[operation, currentNodeId]` — NOT re-derived from the
parsed value on every keystroke. See the Step 3 code and its inline
comments for why (a controlled input that re-derives its display text from
a parsed array/JSON value corrupts mid-typing input, and this panel stays
mounted across node selection so a plain `[operation]` dependency would
leave a previous node's stale text on screen after switching nodes).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run components/composer/canvas/node-panels/confluence.test.tsx`
Expected: FAIL — `Cannot find module './confluence'`

- [ ] **Step 3: Create the panel**

Create `frontend/components/composer/canvas/node-panels/confluence.tsx`:

```tsx
"use client";

import { useState, useEffect } from "react";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { NativeSelect } from "@/components/ui/native-select";

// Must match REDACTED_MARKER in src/security/encryption.py — the backend
// never returns the real token once it's saved, only this marker.
const TOKEN_REDACTED_MARKER = "••••••••";

// propertyValue is typed `Any` on the backend (src/engine/workflow.py) — it
// holds arbitrary structured data (get_property/set_property exist to
// store/retrieve JSON baseline snapshots on a Confluence page property, per
// Component 3 above), not just plain text. Display must stringify an
// already-structured value for the textarea, and edits must be parsed back
// to real JSON when they look like JSON — otherwise the value only ever
// round-trips as a literal string, defeating the whole point of the
// operation and corrupting any already-structured value the moment the
// node is reopened in the Designer. Mirrors arcade.tsx's
// stringifyInput/parseInput pattern for its `args` field. (An earlier draft
// of this code block did a raw passthrough — `value={propertyValue}` /
// `onChange={(e) => onChange({ propertyValue: e.target.value })}` — do not
// reintroduce that; it was a Critical bug caught in code review.)
function stringifyPropertyValue(value: unknown): string {
  if (value === undefined || value === null) return "";
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

// Double-brace text ("{{node.output}}") is this codebase's templating
// syntax, not JSON, even though it starts with "{" — never flag it as
// broken JSON. Single-brace/bracket text that fails to parse is presumed to
// be an attempted JSON object/array with a typo, so it gets a visible error;
// anything else that fails to parse (a bare word, a literal, a template
// reference) is treated as an intentional plain string.
function looksLikeIntendedJson(text: string): boolean {
  const trimmed = text.trim();
  if (trimmed.startsWith("{{")) return false;
  return trimmed.startsWith("{") || trimmed.startsWith("[");
}

function parsePropertyValue(text: string): { value: unknown; error?: string } {
  if (!text.trim()) return { value: "" };
  try {
    return { value: JSON.parse(text) };
  } catch {
    if (looksLikeIntendedJson(text)) {
      return { value: text, error: "Invalid JSON — will be saved as plain text." };
    }
    return { value: text };
  }
}

const OPERATION_OPTIONS = [
  { value: "create_or_update_page", label: "Create or update page" },
  { value: "get_page", label: "Get page" },
  { value: "get_property", label: "Get property" },
  { value: "set_property", label: "Set property" },
];

export default function ConfluencePanel({
  data,
  onChange,
  currentNodeId,
}: {
  data: Record<string, unknown>;
  onChange: (patch: Record<string, unknown>) => void;
  currentNodeId?: string;
}) {
  const operation = (data.operation as string) || "create_or_update_page";
  const rawApiToken = (data.apiToken as string) ?? "";
  const apiTokenIsRedacted = rawApiToken === TOKEN_REDACTED_MARKER;

  const spaceKey = (data.spaceKey as string) ?? "";
  const parentPageId = (data.parentPageId as string) ?? "";
  const title = (data.title as string) ?? "";
  const bodyStorageHtml = (data.bodyStorageHtml as string) ?? "";
  const pageId = (data.pageId as string) ?? "";
  const propertyKey = (data.propertyKey as string) ?? "";

  // Labels is a free-typed comma-separated list. Its displayed text must be
  // kept in local state — NOT re-derived from the parsed `labels` array on
  // every keystroke — because the parsed array drops empty tail tokens
  // (e.g. the trailing comma while typing "weekly-report, "), which would
  // snap the controlled value back and silently merge the next character
  // into the previous entry. Only re-seed from the prop when the mode
  // actually changes externally OR when the selected node changes —
  // ConfluencePanel stays mounted across node selection (no key={node.id}),
  // so a plain [operation] dependency would leave the previous node's stale
  // Labels text on screen when switching to another Confluence node,
  // corrupting the new node's labels on the next edit. Matches the
  // [currentNodeId] convention used by jira.tsx/extract.tsx/http.tsx for
  // this same class of bug.
  const [labelsText, setLabelsText] = useState(() =>
    ((data.labels as string[] | undefined) ?? []).join(", ")
  );
  useEffect(() => {
    setLabelsText(((data.labels as string[] | undefined) ?? []).join(", "));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [operation, currentNodeId]);

  // Same local-buffer-plus-resync treatment as Labels above, but for a
  // value that can be either a JSON-parsed object/array/number/boolean or a
  // plain string/template reference. See stringifyPropertyValue/
  // parsePropertyValue above for the round-trip rules.
  const [propertyValueText, setPropertyValueText] = useState(() =>
    stringifyPropertyValue(data.propertyValue)
  );
  const [propertyValueError, setPropertyValueError] = useState<string | null>(
    null
  );
  useEffect(() => {
    setPropertyValueText(stringifyPropertyValue(data.propertyValue));
    setPropertyValueError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [operation, currentNodeId]);

  function handlePropertyValueChange(text: string) {
    setPropertyValueText(text);
    const { value, error } = parsePropertyValue(text);
    setPropertyValueError(error ?? null);
    onChange({ propertyValue: value });
  }

  const needsSpaceAndTitle = operation === "create_or_update_page" || operation === "get_page";
  const needsPageId = operation === "get_property" || operation === "set_property";

  return (
    <div className="space-y-4">
      {/* Credentials */}
      <div className="rounded-md border bg-muted/20 p-3">
        <p className="mb-2 text-xs font-semibold text-muted-foreground">
          Confluence Connection
        </p>
        <div className="space-y-2">
          <div className="space-y-1">
            <Label htmlFor="confluence-domain" className="text-xs">
              Domain
            </Label>
            <Input
              id="confluence-domain"
              value={(data.domain as string) ?? ""}
              onChange={(e) => onChange({ domain: e.target.value })}
              placeholder="your-org.atlassian.net"
              className="font-mono text-xs"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="confluence-email" className="text-xs">
              Email
            </Label>
            <Input
              id="confluence-email"
              value={(data.email as string) ?? ""}
              onChange={(e) => onChange({ email: e.target.value })}
              placeholder="you@example.com"
              className="font-mono text-xs"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="confluence-api-token" className="text-xs">
              API Token
            </Label>
            <Input
              id="confluence-api-token"
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
        <Label htmlFor="confluence-operation">Operation</Label>
        <NativeSelect
          id="confluence-operation"
          value={operation}
          onValueChange={(v) => onChange({ operation: v })}
          options={OPERATION_OPTIONS}
        />
      </div>

      {needsSpaceAndTitle && (
        <>
          <div className="space-y-2">
            <Label htmlFor="confluence-space-key">Space key</Label>
            <Input
              id="confluence-space-key"
              value={spaceKey}
              onChange={(e) => onChange({ spaceKey: e.target.value })}
              placeholder="MB"
              className="font-mono text-xs"
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="confluence-title">Title</Label>
            <Input
              id="confluence-title"
              value={title}
              onChange={(e) => onChange({ title: e.target.value })}
              placeholder="{{project_name}} - Weekly Delivery Report - {{reporting_date}}"
              className="font-mono text-xs"
            />
          </div>
        </>
      )}

      {operation === "create_or_update_page" && (
        <>
          <div className="space-y-2">
            <Label htmlFor="confluence-parent-page-id">
              Parent page ID{" "}
              <span className="font-normal text-muted-foreground">(optional)</span>
            </Label>
            <Input
              id="confluence-parent-page-id"
              value={parentPageId}
              onChange={(e) => onChange({ parentPageId: e.target.value || null })}
              placeholder="123456"
              className="font-mono text-xs"
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="confluence-body">Body (storage format HTML)</Label>
            <Textarea
              id="confluence-body"
              value={bodyStorageHtml}
              onChange={(e) => onChange({ bodyStorageHtml: e.target.value })}
              rows={6}
              placeholder="<h1>Weekly Delivery Summary</h1><p>{{narrative}}</p>"
              className="font-mono text-xs"
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="confluence-labels">Labels</Label>
            <Input
              id="confluence-labels"
              value={labelsText}
              onChange={(e) => {
                const text = e.target.value;
                setLabelsText(text);
                onChange({
                  labels: text
                    .split(",")
                    .map((s) => s.trim())
                    .filter(Boolean),
                });
              }}
              placeholder="weekly-report, adobe-target"
              className="font-mono text-xs"
            />
            <p className="text-xs text-amber-700">
              Comma-separated. Reconciled exactly to this set on every run —
              labels not listed here are removed.
            </p>
          </div>
        </>
      )}

      {needsPageId && (
        <>
          <div className="space-y-2">
            <Label htmlFor="confluence-page-id">Page ID</Label>
            <Input
              id="confluence-page-id"
              value={pageId}
              onChange={(e) => onChange({ pageId: e.target.value })}
              placeholder="{{find_last_week.pageId}}"
              className="font-mono text-xs"
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="confluence-property-key">Property key</Label>
            <Input
              id="confluence-property-key"
              value={propertyKey}
              onChange={(e) => onChange({ propertyKey: e.target.value })}
              placeholder="metrics_snapshot"
              className="font-mono text-xs"
            />
          </div>
        </>
      )}

      {operation === "set_property" && (
        <div className="space-y-2">
          <Label htmlFor="confluence-property-value">
            Property value (JSON or text)
          </Label>
          <Textarea
            id="confluence-property-value"
            value={propertyValueText}
            onChange={(e) => handlePropertyValueChange(e.target.value)}
            rows={4}
            placeholder='{{compute_metrics.output}}  or  {"total": 42}'
            className="font-mono text-xs"
            aria-invalid={propertyValueError !== null}
          />
          {propertyValueError && (
            <p className="text-xs text-destructive">{propertyValueError}</p>
          )}
          <p className="text-xs text-muted-foreground">
            JSON objects/arrays are stored as structured data; anything else
            (including <code className="font-mono">{"{{template}}"}</code>{" "}
            references) is stored as plain text.
          </p>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run components/composer/canvas/node-panels/confluence.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/components/composer/canvas/node-panels/confluence.tsx frontend/components/composer/canvas/node-panels/confluence.test.tsx
git commit -m "$(cat <<'EOF'
feat(confluence-panel): add the Designer property panel

Operation selector switches between create_or_update_page/get_page's
space+title+body+labels fields and get_property/set_property's
pageId+propertyKey(+value) fields, per the backend schema from Task 1.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Register the node type in the canvas (palette, panel map, visuals)

**Files:**
- Modify: `frontend/components/composer/canvas/property-panel.tsx`
- Modify: `frontend/components/composer/canvas/tools-palette.tsx`
- Modify: `frontend/components/composer/canvas/node-visuals.ts`
- Modify: `frontend/components/composer/canvas/workflow-canvas.tsx`

- [ ] **Step 1: Register the panel in `property-panel.tsx`**

Add the import (alphabetically, after `JiraPanel`'s import at line 27):

```tsx
import ConfluencePanel from "./node-panels/confluence";
```

Add to `PANEL_MAP` (after `jira: JiraPanel,` at line 68):

```tsx
  confluence: ConfluencePanel,
```

Add to `TYPE_LABELS` (after `jira: "Jira",` at line 93):

```tsx
  confluence: "Confluence",
```

- [ ] **Step 2: Register in the palette — `tools-palette.tsx`**

Add to `COMPOSER_NODE_PALETTE` (after `{ nodeType: "jira", label: "Jira" },` at line 41):

```tsx
  { nodeType: "confluence", label: "Confluence" },
```

- [ ] **Step 3: Add a visual identity — `node-visuals.ts`**

Add `BookOpen` to the `lucide-react` import list (alongside `Bot`, `CheckCircle2`, etc.):

```ts
  BookOpen,
```

Add an entry to `NODE_VISUALS` (after the `jira` entry at lines 177-182):

```ts
  confluence: {
    icon: BookOpen,
    iconWrapClass: "bg-sky-100 text-sky-700",
    accent: "text-sky-700",
    label: "Confluence",
  },
```

- [ ] **Step 4: Register in `workflow-canvas.tsx`**

Add to `COMPOSER_NODE_TYPES` (after `jira: InnerNode,` at line 226):

```tsx
  confluence: InnerNode,
```

- [ ] **Step 5: Run the full frontend test suite and type check**

Run: `cd frontend && npx vitest run && npx tsc --noEmit`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add frontend/components/composer/canvas/property-panel.tsx frontend/components/composer/canvas/tools-palette.tsx frontend/components/composer/canvas/node-visuals.ts frontend/components/composer/canvas/workflow-canvas.tsx
git commit -m "$(cat <<'EOF'
feat(confluence-node): register the node type on the canvas

Palette entry, property-panel dispatch, visual identity (BookOpen icon,
sky accent), and ReactFlow node-type registration — the confluence node
is now fully usable from the Designer, not just via raw workflow JSON.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-review notes

- **Spec coverage**: Component 2 of the spec (Confluence node — 4 deterministic operations, encrypted credentials, idempotent create-or-update, content-properties for get/set) is fully covered by Tasks 1-6. Component 3 (baseline pattern) deliberately isn't built here — it's a usage pattern for a future Macy's-flow plan to apply, consuming `get_page`/`get_property`/`set_property` as workflow configuration.
- **Placeholder scan**: none found — every step has complete code, including the label-reconciliation DELETE/POST logic that a shortcut version might have skipped.
- **Type consistency**: `space_key`/`parent_page_id`/`body_storage_html`/`page_id`/`property_key`/`property_value` (Python) match `spaceKey`/`parentPageId`/`bodyStorageHtml`/`pageId`/`propertyKey`/`propertyValue` (the camelCase aliases the frontend panel in Task 5 actually sends) consistently across Tasks 1, 3, and 5. `ConfluenceConfigError`/`ConfluenceHttpError` names match between Task 3's implementation and its tests.
- **Platform/customer-flow boundary**: verified no Macy's-specific space key, page title convention, or property key appears anywhere in this plan's code — confirmed against [[feedback_platform_vs_customer_flow]].
