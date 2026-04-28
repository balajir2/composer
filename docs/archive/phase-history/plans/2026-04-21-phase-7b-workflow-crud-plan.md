# Phase 7b — Workflow CRUD: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development`. Steps use checkbox (`- [ ]`).

**Goal:** Complete workflow API parity with OAB: list, search, fetch-by-id, update, delete + executions list.

**Architecture.** Extends `src/api/workflows.py` + `src/api/executions.py`. Owner-only PUT/DELETE (403 on mismatch). Hard-delete with cascade via existing Prisma schema.

**Tech Stack:** FastAPI Query params, Prisma Python, existing auth dependency.

**Spec:** [`docs/superpowers/specs/2026-04-21-phase-7b-workflow-crud-design.md`](../specs/2026-04-21-phase-7b-workflow-crud-design.md)

---

## Sequencing + discipline

6 tasks, one commit each. Every task ends green on:

```bash
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m ruff format --check src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

Integration test (Task 6) runs at phase-exit against real Neon.

**⚠️ Forbidden files:** `pyproject.toml`, `.github/workflows/*`, `CLAUDE.md` (except Task 6), `docs/design/*`, `docs/superpowers/plans/*`, `docs/superpowers/specs/*`, Prisma schema + migrations (no schema changes — existing tables are complete).

Commit footer:
```
Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
```

Stay on `main`.

---

## Task 1: `GET /workflows` list + `GET /workflows/search`

**Files:**
- Modify: `src/api/workflows.py` — add list + search endpoints + `WorkflowListResponse`
- Create: `tests/unit/api/test_workflows_crud.py` — ~6 list/search tests

- [ ] **Step 1: Add `WorkflowListResponse` model to `src/api/workflows.py`**

Append after the existing `WorkflowRead` class:

```python
class WorkflowListResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    total: int
    items: list[WorkflowRead]
    limit: int
    offset: int
```

- [ ] **Step 2: Add `GET /workflows` endpoint**

```python
@router.get("/workflows", response_model=WorkflowListResponse)
async def list_workflows(
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    user_id: str = Depends(get_current_user_id),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    is_template: bool | None = Query(default=None, alias="isTemplate"),
    is_public: bool | None = Query(default=None, alias="isPublic"),
    category: str | None = Query(default=None),
    mine: bool | None = Query(default=None),
) -> WorkflowListResponse:  # pyright: ignore[reportUnusedFunction]
    where: dict[str, Any] = {}
    if is_template is not None:
        where["isTemplate"] = is_template
    if is_public is not None:
        where["isPublic"] = is_public
    if category is not None:
        where["category"] = category
    if mine:
        where["userId"] = user_id

    total = await db.workflow.count(where=where)  # pyright: ignore[reportAttributeAccessIssue]
    rows = await db.workflow.find_many(  # pyright: ignore[reportAttributeAccessIssue]
        where=where,
        take=limit,
        skip=offset,
        order={"updatedAt": "desc"},
    )
    items = [WorkflowRead.model_validate(row) for row in rows]
    return WorkflowListResponse(total=total, items=items, limit=limit, offset=offset)
```

Import `Query` from `fastapi` at the top.

- [ ] **Step 3: Add `GET /workflows/search` endpoint**

Register this BEFORE the `/workflows/{workflow_id}` route (FastAPI matches routes in order; literal path must precede the path-param).

```python
@router.get("/workflows/search", response_model=WorkflowListResponse)
async def search_workflows(
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    _user_id: str = Depends(get_current_user_id),
    q: str = Query(..., min_length=1),
    limit: int = Query(default=50, ge=1, le=100),
) -> WorkflowListResponse:  # pyright: ignore[reportUnusedFunction]
    where: dict[str, Any] = {
        "OR": [
            {"name": {"contains": q, "mode": "insensitive"}},
            {"description": {"contains": q, "mode": "insensitive"}},
        ]
    }
    total = await db.workflow.count(where=where)  # pyright: ignore[reportAttributeAccessIssue]
    rows = await db.workflow.find_many(  # pyright: ignore[reportAttributeAccessIssue]
        where=where,
        take=limit,
        order={"updatedAt": "desc"},
    )
    items = [WorkflowRead.model_validate(row) for row in rows]
    return WorkflowListResponse(total=total, items=items, limit=limit, offset=0)
```

- [ ] **Step 4: Update `__all__`** to include `WorkflowListResponse`.

- [ ] **Step 5: Create `tests/unit/api/test_workflows_crud.py`**

```python
"""Tests for workflow list + search endpoints (Phase 7b)."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.main import create_app


def _wf_row(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": "w1",
        "userId": "user-42",
        "name": "Sample",
        "description": "A test workflow",
        "category": "utilities",
        "tags": ["x"],
        "difficulty": None,
        "estimatedTime": None,
        "nodes": [],
        "edges": [],
        "version": None,
        "isTemplate": False,
        "isPublic": False,
        "createdAt": "2026-04-21T00:00:00Z",
        "updatedAt": "2026-04-21T01:00:00Z",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _client(monkeypatch: pytest.MonkeyPatch, rows: list[Any], total: int) -> TestClient:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()

    db = MagicMock()
    db.workflow = MagicMock()
    db.workflow.count = AsyncMock(return_value=total)
    db.workflow.find_many = AsyncMock(return_value=rows)
    app.state.db = db
    app.state.checkpointer = MagicMock()
    from src.engine.events import ExecutionEventBus

    app.state.event_bus = ExecutionEventBus()
    return TestClient(app)


def test_list_workflows_default(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, [_wf_row(), _wf_row(id="w2", name="Another")], total=2)
    resp = client.get("/workflows")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2
    assert body["limit"] == 50
    assert body["offset"] == 0


def test_list_workflows_filters_by_is_template(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, [_wf_row(isTemplate=True)], total=1)
    resp = client.get("/workflows?isTemplate=true")
    assert resp.status_code == 200
    # Check the where clause passed to find_many
    db = client.app.state.db  # pyright: ignore[reportAttributeAccessIssue]
    assert db.workflow.find_many.await_args.kwargs["where"].get("isTemplate") is True


def test_list_workflows_mine_filters_by_user(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, [_wf_row(userId="dev")], total=1)
    resp = client.get("/workflows?mine=true")
    assert resp.status_code == 200
    db = client.app.state.db  # pyright: ignore[reportAttributeAccessIssue]
    # dev-mode fallback (ADR-0015) sets user_id='dev'
    assert db.workflow.find_many.await_args.kwargs["where"].get("userId") == "dev"


def test_list_workflows_limit_over_100_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, [], total=0)
    resp = client.get("/workflows?limit=150")
    assert resp.status_code == 422


def test_search_workflows_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, [_wf_row(name="SearchTest")], total=1)
    resp = client.get("/workflows/search?q=search")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "SearchTest"
    db = client.app.state.db  # pyright: ignore[reportAttributeAccessIssue]
    where = db.workflow.find_many.await_args.kwargs["where"]
    assert "OR" in where


def test_search_workflows_empty_q_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, [], total=0)
    resp = client.get("/workflows/search?q=")
    assert resp.status_code == 422
```

- [ ] **Step 6: Run + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/api/test_workflows_crud.py -v
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

Expected: 6 new tests pass. Overall ~515 (from 509 at Phase 6e exit).

```bash
git add src/api/workflows.py tests/unit/api/test_workflows_crud.py
git commit -m "feat(api): GET /workflows list + GET /workflows/search (Phase 7b)

List endpoint returns paginated envelope {total, items, limit, offset}.
Filters: isTemplate, isPublic, category, mine (restricts to caller's
userId).  Limit capped at 100, default 50.  Ordered by updatedAt DESC.

Search endpoint uses Prisma contains+mode:insensitive on name+description.
Empty q is 422.

See Phase 7b spec §4, §5.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `GET /workflows/{workflow_id}`

**Files:**
- Modify: `src/api/workflows.py` — add fetch-by-id endpoint
- Modify: `tests/unit/api/test_workflows_crud.py` — append 2 tests

- [ ] **Step 1: Add endpoint**

After `GET /workflows/search` (which must register FIRST for path matching):

```python
@router.get("/workflows/{workflow_id}", response_model=WorkflowRead)
async def get_workflow(
    workflow_id: str,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    _user_id: str = Depends(get_current_user_id),
) -> WorkflowRead:  # pyright: ignore[reportUnusedFunction]
    row = await db.workflow.find_unique(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": workflow_id}
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow {workflow_id!r} not found.",
        )
    return WorkflowRead.model_validate(row)
```

- [ ] **Step 2: Add tests**

Append to `tests/unit/api/test_workflows_crud.py`:

```python
def test_get_workflow_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _wf_row(id="wx", name="Hello")
    client = _client_fetch(monkeypatch, row)
    resp = client.get("/workflows/wx")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Hello"


def test_get_workflow_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client_fetch(monkeypatch, None)
    resp = client.get("/workflows/ghost")
    assert resp.status_code == 404


def _client_fetch(monkeypatch: pytest.MonkeyPatch, row: Any | None) -> TestClient:
    """Helper: client stubbed with a find_unique return."""
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()

    db = MagicMock()
    db.workflow = MagicMock()
    db.workflow.find_unique = AsyncMock(return_value=row)
    app.state.db = db
    app.state.checkpointer = MagicMock()
    from src.engine.events import ExecutionEventBus

    app.state.event_bus = ExecutionEventBus()
    return TestClient(app)
```

- [ ] **Step 3: Run + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/api/test_workflows_crud.py -v
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

Expected: 2 new tests pass. Overall ~517.

```bash
git add src/api/workflows.py tests/unit/api/test_workflows_crud.py
git commit -m "feat(api): GET /workflows/{id} (Phase 7b)

Fetch a single workflow by id.  Returns WorkflowRead; 404 if not
found.  Any authenticated user can read (no ownership check).

See Phase 7b spec §6.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `PUT /workflows/{workflow_id}` — update with ownership check

**Files:**
- Modify: `src/api/workflows.py`
- Modify: `tests/unit/api/test_workflows_crud.py` — append 4 tests

- [ ] **Step 1: Add endpoint**

```python
@router.put("/workflows/{workflow_id}", response_model=WorkflowRead)
async def update_workflow(
    workflow_id: str,
    payload: WorkflowCreate,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    user_id: str = Depends(get_current_user_id),
) -> WorkflowRead:  # pyright: ignore[reportUnusedFunction]
    existing = await db.workflow.find_unique(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": workflow_id}
    )
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow {workflow_id!r} not found.",
        )
    if existing.userId != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: not workflow owner.",
        )

    # Re-validate shape (matches create path)
    workflow = Workflow.model_validate(payload.model_dump(by_alias=True))
    try:
        validate_workflow_shape(workflow)
    except WorkflowValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    nodes_json = [node.model_dump(by_alias=True) for node in workflow.nodes]  # pyright: ignore[reportAttributeAccessIssue]
    edges_json = [edge.model_dump(by_alias=True) for edge in workflow.edges]
    updated = await db.workflow.update(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": workflow_id},
        data={
            "name": payload.name,
            "description": payload.description,
            "category": payload.category,
            "tags": payload.tags,
            "difficulty": payload.difficulty,
            "estimatedTime": payload.estimated_time,
            "nodes": Json(nodes_json),
            "edges": Json(edges_json),
            "version": payload.version,
            "isTemplate": payload.is_template,
            "isPublic": payload.is_public,
        },
    )
    return WorkflowRead.model_validate(updated)
```

- [ ] **Step 2: Tests**

```python
def _client_put(
    monkeypatch: pytest.MonkeyPatch,
    existing: Any | None,
    updated: Any | None,
) -> TestClient:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()

    db = MagicMock()
    db.workflow = MagicMock()
    db.workflow.find_unique = AsyncMock(return_value=existing)
    db.workflow.update = AsyncMock(return_value=updated)
    app.state.db = db
    app.state.checkpointer = MagicMock()
    from src.engine.events import ExecutionEventBus

    app.state.event_bus = ExecutionEventBus()
    return TestClient(app)


_VALID_BODY: dict[str, Any] = {
    "name": "Updated",
    "nodes": [
        {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
        {"id": "e", "type": "end", "position": {"x": 100, "y": 0}, "data": {"label": "E"}},
    ],
    "edges": [{"id": "e1", "source": "s", "target": "e"}],
}


def test_put_workflow_owner_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    existing = _wf_row(id="w1", userId="dev")  # dev-mode fallback user
    updated = _wf_row(id="w1", userId="dev", name="Updated")
    client = _client_put(monkeypatch, existing, updated)
    resp = client.put("/workflows/w1", json=_VALID_BODY)
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "Updated"


def test_put_workflow_not_owner_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    existing = _wf_row(id="w1", userId="someone-else")
    client = _client_put(monkeypatch, existing, None)
    resp = client.put("/workflows/w1", json=_VALID_BODY)
    assert resp.status_code == 403


def test_put_workflow_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client_put(monkeypatch, None, None)
    resp = client.put("/workflows/ghost", json=_VALID_BODY)
    assert resp.status_code == 404


def test_put_workflow_invalid_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """No start node → validator raises → 422."""
    existing = _wf_row(id="w1", userId="dev")
    client = _client_put(monkeypatch, existing, None)
    bad_body: dict[str, Any] = {
        "name": "Bad",
        "nodes": [
            {"id": "e", "type": "end", "position": {"x": 0, "y": 0}, "data": {"label": "E"}},
        ],
        "edges": [],
    }
    resp = client.put("/workflows/w1", json=bad_body)
    assert resp.status_code == 422
```

- [ ] **Step 3: Run + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/api/test_workflows_crud.py -v
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

Expected: 4 new tests pass. Overall ~521.

```bash
git add src/api/workflows.py tests/unit/api/test_workflows_crud.py
git commit -m "feat(api): PUT /workflows/{id} — owner-only update (Phase 7b)

Full replacement (not PATCH).  Body shape matches POST /workflows.
Ownership check: 403 if workflow.userId != current_user_id.  404
if unknown.  422 if shape invalid (validate_workflow_shape).

See Phase 7b spec §7.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: `DELETE /workflows/{workflow_id}` — owner-only delete

**Files:**
- Modify: `src/api/workflows.py`
- Modify: `tests/unit/api/test_workflows_crud.py`

- [ ] **Step 1: Add endpoint**

```python
@router.delete("/workflows/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workflow(
    workflow_id: str,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    user_id: str = Depends(get_current_user_id),
) -> None:  # pyright: ignore[reportUnusedFunction]
    existing = await db.workflow.find_unique(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": workflow_id}
    )
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow {workflow_id!r} not found.",
        )
    if existing.userId != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: not workflow owner.",
        )
    await db.workflow.delete(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": workflow_id}
    )
```

- [ ] **Step 2: Tests**

```python
def _client_delete(
    monkeypatch: pytest.MonkeyPatch, existing: Any | None
) -> tuple[TestClient, MagicMock]:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()

    db = MagicMock()
    db.workflow = MagicMock()
    db.workflow.find_unique = AsyncMock(return_value=existing)
    db.workflow.delete = AsyncMock()
    app.state.db = db
    app.state.checkpointer = MagicMock()
    from src.engine.events import ExecutionEventBus

    app.state.event_bus = ExecutionEventBus()
    return TestClient(app), db


def test_delete_workflow_owner_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    existing = _wf_row(id="w1", userId="dev")
    client, db = _client_delete(monkeypatch, existing)
    resp = client.delete("/workflows/w1")
    assert resp.status_code == 204
    db.workflow.delete.assert_awaited_once()


def test_delete_workflow_not_owner_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    existing = _wf_row(id="w1", userId="other")
    client, _ = _client_delete(monkeypatch, existing)
    resp = client.delete("/workflows/w1")
    assert resp.status_code == 403


def test_delete_workflow_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client_delete(monkeypatch, None)
    resp = client.delete("/workflows/ghost")
    assert resp.status_code == 404
```

- [ ] **Step 3: Run + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/api/test_workflows_crud.py -v
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

Expected: 3 new tests pass. Overall ~524.

```bash
git add src/api/workflows.py tests/unit/api/test_workflows_crud.py
git commit -m "feat(api): DELETE /workflows/{id} — owner-only delete (Phase 7b)

Hard-delete.  Prisma schema has ON DELETE CASCADE on
WorkflowExecution.workflowId, so executions + their checkpoints are
removed automatically.  403 if not owner; 404 if unknown; 204 on
success.

See Phase 7b spec §8.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: `GET /executions` list

**Files:**
- Modify: `src/api/executions.py`
- Create: `tests/unit/api/test_executions_list.py`

- [ ] **Step 1: Add `ExecutionListResponse` model** in `src/api/executions.py`

```python
class ExecutionListResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    total: int
    items: list[ExecutionRead]
    limit: int
    offset: int
```

- [ ] **Step 2: Add endpoint** (register before `/executions/{id}` for path matching — actually, FastAPI handles `/executions` literal separately from `/executions/{id}`, so order doesn't matter)

```python
@router.get("/executions", response_model=ExecutionListResponse)
async def list_executions(
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    _user_id: str = Depends(get_current_user_id),
    workflow_id: str | None = Query(default=None, alias="workflowId"),
    user_id_filter: str | None = Query(default=None, alias="userId"),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ExecutionListResponse:  # pyright: ignore[reportUnusedFunction]
    where: dict[str, Any] = {}
    if workflow_id is not None:
        where["workflowId"] = workflow_id
    if user_id_filter is not None:
        where["userId"] = user_id_filter
    if status_filter is not None:
        where["status"] = status_filter

    total = await db.workflowexecution.count(where=where)  # pyright: ignore[reportAttributeAccessIssue]
    rows = await db.workflowexecution.find_many(  # pyright: ignore[reportAttributeAccessIssue]
        where=where,
        take=limit,
        skip=offset,
        order={"startedAt": "desc"},
    )
    items = [ExecutionRead.model_validate(row) for row in rows]
    return ExecutionListResponse(total=total, items=items, limit=limit, offset=offset)
```

Import `Query` from `fastapi` at the top.
Update `__all__` to include `ExecutionListResponse`.

- [ ] **Step 3: Tests**

Create `tests/unit/api/test_executions_list.py`:

```python
"""Tests for GET /executions (list) — Phase 7b."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.main import create_app


def _exec_row(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": "e1",
        "workflowId": "w1",
        "userId": "dev",
        "status": "completed",
        "currentNodeId": None,
        "nodeResults": {},
        "variables": {},
        "input": None,
        "output": None,
        "error": None,
        "startedAt": "2026-04-21T00:00:00Z",
        "completedAt": "2026-04-21T00:00:10Z",
        "threadId": "t1",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _client(monkeypatch: pytest.MonkeyPatch, rows: list[Any], total: int) -> TestClient:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()

    db = MagicMock()
    db.workflowexecution = MagicMock()
    db.workflowexecution.count = AsyncMock(return_value=total)
    db.workflowexecution.find_many = AsyncMock(return_value=rows)
    app.state.db = db
    app.state.checkpointer = MagicMock()
    from src.engine.events import ExecutionEventBus

    app.state.event_bus = ExecutionEventBus()
    return TestClient(app)


def test_list_executions_default(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, [_exec_row(), _exec_row(id="e2")], total=2)
    resp = client.get("/executions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2


def test_list_executions_filter_by_workflow(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, [_exec_row(workflowId="wX")], total=1)
    resp = client.get("/executions?workflowId=wX")
    assert resp.status_code == 200
    db = client.app.state.db  # pyright: ignore[reportAttributeAccessIssue]
    where = db.workflowexecution.find_many.await_args.kwargs["where"]
    assert where["workflowId"] == "wX"


def test_list_executions_filter_by_status(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, [_exec_row(status="failed")], total=1)
    resp = client.get("/executions?status=failed")
    assert resp.status_code == 200
    db = client.app.state.db  # pyright: ignore[reportAttributeAccessIssue]
    where = db.workflowexecution.find_many.await_args.kwargs["where"]
    assert where["status"] == "failed"


def test_list_executions_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, [], total=100)
    resp = client.get("/executions?limit=10&offset=20")
    assert resp.status_code == 200
    body = resp.json()
    assert body["limit"] == 10
    assert body["offset"] == 20


def test_list_executions_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, [], total=0)
    resp = client.get("/executions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["items"] == []
```

- [ ] **Step 4: Run + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/api/test_executions_list.py -v
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

Expected: 5 new tests pass. Overall ~529.

```bash
git add src/api/executions.py tests/unit/api/test_executions_list.py
git commit -m "feat(api): GET /executions (list) — Phase 7b

Paginated list with filters: workflowId, userId, status.  Envelope
{total, items, limit, offset}.  limit capped at 100, default 50.
Ordered by startedAt DESC.

See Phase 7b spec §9.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Integration test + phase-exit docs

**Files:**
- Create: `tests/integration/test_workflow_crud.py`
- Modify: `CHANGELOG.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Integration test**

```python
"""Integration — full workflow CRUD cycle against real Neon (Phase 7b)."""

from typing import Any

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


_MINIMAL_BODY: dict[str, Any] = {
    "name": "Phase 7b CRUD cycle",
    "description": "Integration test",
    "nodes": [
        {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
        {"id": "e", "type": "end", "position": {"x": 100, "y": 0}, "data": {"label": "E"}},
    ],
    "edges": [{"id": "e1", "source": "s", "target": "e"}],
}


async def test_workflow_crud_cycle(client: AsyncClient) -> None:
    # Create
    r = await client.post("/workflows", json=_MINIMAL_BODY)
    assert r.status_code == 201, r.text
    wf_id = r.json()["id"]

    # Get
    r = await client.get(f"/workflows/{wf_id}")
    assert r.status_code == 200
    assert r.json()["name"] == _MINIMAL_BODY["name"]

    # List — new workflow appears somewhere
    r = await client.get("/workflows?mine=true&limit=100")
    assert r.status_code == 200
    ids = [w["id"] for w in r.json()["items"]]
    assert wf_id in ids

    # Search
    r = await client.get("/workflows/search?q=Phase+7b")
    assert r.status_code == 200
    assert any(w["id"] == wf_id for w in r.json()["items"])

    # Update
    updated = {**_MINIMAL_BODY, "name": "Updated name"}
    r = await client.put(f"/workflows/{wf_id}", json=updated)
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Updated name"

    # Confirm the update
    r = await client.get(f"/workflows/{wf_id}")
    assert r.json()["name"] == "Updated name"

    # Delete
    r = await client.delete(f"/workflows/{wf_id}")
    assert r.status_code == 204

    # Confirm 404
    r = await client.get(f"/workflows/{wf_id}")
    assert r.status_code == 404
```

- [ ] **Step 2: Quality gates**

```bash
.venv/Scripts/python -m ruff check tests/integration/test_workflow_crud.py
.venv/Scripts/python -m ruff format tests/integration/test_workflow_crud.py
.venv/Scripts/python -m pyright tests/integration/test_workflow_crud.py
.venv/Scripts/python -m pytest tests/integration/test_workflow_crud.py --collect-only -q
```

- [ ] **Step 3: Commit integration test**

```bash
git add tests/integration/test_workflow_crud.py
git commit -m "test(integration): workflow CRUD cycle (Phase 7b)

Real Neon.  Create → get → list (mine=true) → search → update →
get-confirms-update → delete → get-confirms-404.  One round-trip
exercises all six Phase 7b endpoints.

See Phase 7b spec §12.2.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 4: Run real-Neon integration**

Controller runs (not subagent):
```bash
.venv/Scripts/python -c "
from dotenv import load_dotenv
import os, subprocess, sys
load_dotenv('.env', override=True)
os.environ['TEST_DATABASE_URL'] = os.environ.get('DATABASE_URL', '')
r = subprocess.run(
    [sys.executable, '-m', 'pytest', '-v', '--tb=short', '--no-cov',
     '-m', 'integration',
     'tests/integration/test_workflow_crud.py'],
    cwd='.'
)
sys.exit(r.returncode)
"
```

- [ ] **Step 5: Update CHANGELOG.md**

Insert above Phase 6e:

```markdown
### Phase 7b — Workflow CRUD (2026-04-21)

#### Added
- [Phase 7b design spec](docs/superpowers/specs/2026-04-21-phase-7b-workflow-crud-design.md).
- `GET /workflows` — paginated list with filters (`isTemplate`, `isPublic`, `category`, `mine`). Envelope: `{total, items, limit, offset}`. Ordered by `updatedAt DESC`. Limit capped at 100.
- `GET /workflows/search?q=...` — name/description search via Prisma `contains` + case-insensitive. Empty `q` is 422.
- `GET /workflows/{id}` — fetch one; 404 if unknown.
- `PUT /workflows/{id}` — owner-only update. Full replacement (not PATCH). 403 if not owner; 404 if unknown; 422 if shape invalid.
- `DELETE /workflows/{id}` — owner-only hard-delete. Cascades to executions + checkpoints via existing Prisma `ON DELETE CASCADE`. 204 on success; 403/404 as above.
- `GET /executions` — paginated list with filters (`workflowId`, `userId`, `status`). Envelope same shape. Ordered by `startedAt DESC`.
- `WorkflowListResponse` + `ExecutionListResponse` Pydantic envelopes.
- Integration test: full CRUD cycle against real Neon.

#### Notes
- Phase 7 was scoped via spec to three sub-phases: **7b — Workflow CRUD (this phase)**, **7c — API auxiliaries** (deferred), **7d — Regression suite port from OAB** (deferred). Phase 7b closes the OAB API parity gap that blocks Phase 10 UI work.
- No new ADR — conventional REST CRUD extensions.
- `mine=true` on `GET /workflows` uses the current user id (dev-mode fallback to `'dev'` per ADR-0015 in development).

#### Verified
- 529/529 unit tests green (+20 from Phase 6e 509: 15 CRUD + 5 executions list).
- 1/1 integration test green against real Neon (full CRUD cycle).
- Pyright 0 errors, ruff + format clean.

### Phase 6e — Vector-DB (2026-04-21)
```

- [ ] **Step 6: Update CLAUDE.md phase table**

Change:
```markdown
| 7 — API parity + regression suite ported | ⏭ Next | port OAB's ~72 pytest tests (objective parity check) |
```
To:
```markdown
| 7b — Workflow CRUD | ✅ Complete | GET list/search, GET/PUT/DELETE by id, GET /executions list; owner-only PUT/DELETE; cascade via Prisma; verified against real Neon |
| 7c — API auxiliaries | ⏸ | vector-db test endpoint, config endpoint (optional) |
| 7d — Regression suite port from OAB | ⏭ Next | port behavioral tests from OAB's Playwright specs |
```

- [ ] **Step 7: Commit + push**

```bash
git add CHANGELOG.md CLAUDE.md
git commit -m "docs(phase-7b): mark Phase 7b complete

Workflow CRUD shipped.  GET list/search, GET/PUT/DELETE by id,
GET /executions list.  Owner-only PUT+DELETE with Prisma cascade on
delete.  529 unit tests + 1 real-Neon integration test.

Phase 7 split into three sub-phases per spec: 7b (CRUD — this),
7c (auxiliaries, deferred), 7d (regression port, next).

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"

git push origin main
```

---

## Spec coverage self-review

| Spec section | Tasks |
|---|---|
| §4 GET /workflows | Task 1 |
| §5 GET /workflows/search | Task 1 |
| §6 GET /workflows/{id} | Task 2 |
| §7 PUT /workflows/{id} | Task 3 |
| §8 DELETE /workflows/{id} | Task 4 |
| §9 GET /executions | Task 5 |
| §10 error model | Tasks 1-5 (per-endpoint tests) |
| §11 Pydantic envelopes | Tasks 1 + 5 |
| §12 test plan | Tasks 1-6 |
| §13 phase-exit | Task 6 |

No placeholders. Type consistency: `WorkflowListResponse` / `ExecutionListResponse` envelope shape identical. Ownership check pattern consistent between Tasks 3 + 4.

---

## Execution handoff

Plan saved. Controller proceeds to `superpowers:subagent-driven-development` for Tasks 1–6.
