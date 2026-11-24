from typing import Any

import pytest

from src.builder.diagnostics import REDACT_MARKER, build_diagnosis
from src.db.repos import executions as executions_repo

_OPEN_SESSIONS: list[int] = []


class _FakeSession:
    async def __aenter__(self) -> "_FakeSession":
        _OPEN_SESSIONS.append(1)
        return self

    async def __aexit__(self, *exc: Any) -> None:
        _OPEN_SESSIONS.pop()


def _sessionmaker() -> Any:
    return lambda: _FakeSession()


def _repo_returning(monkeypatch: pytest.MonkeyPatch, row: Any) -> None:
    """The repo read is faked; the builder's own visibility filter is what these tests pin."""

    async def _get(_session: Any, execution_id: str) -> Any:
        return row if row is not None and row.id == execution_id else None

    monkeypatch.setattr(executions_repo, "get", _get)


def _row(**overrides: Any) -> Any:
    attrs: dict[str, Any] = {
        "id": "exec-1",
        "workflow_id": "wf-1",
        "user_id": "alice",
        "status": "failed",
        "error": "boom",
        "node_results": {"a": {"output": "x"}},
        "variables": {"apiKey": "SECRET", "safe": "ok"},
    }
    attrs.update(overrides)
    return type("Row", (), attrs)()


async def test_build_diagnosis_redacts_secret_keys(monkeypatch: pytest.MonkeyPatch):
    _repo_returning(monkeypatch, _row())
    diag = await build_diagnosis(_sessionmaker(), "exec-1", workflow_id="wf-1", user_id="alice")
    assert diag is not None
    assert diag.status == "failed"
    assert diag.error == "boom"
    assert diag.variables_tail["apiKey"] == REDACT_MARKER
    assert diag.variables_tail["safe"] == "ok"


async def test_build_diagnosis_returns_none_for_missing_row(monkeypatch: pytest.MonkeyPatch):
    _repo_returning(monkeypatch, None)
    assert (
        await build_diagnosis(_sessionmaker(), "nope", workflow_id="wf-1", user_id="alice") is None
    )


async def test_other_users_execution_is_not_diagnosed(monkeypatch: pytest.MonkeyPatch):
    _repo_returning(monkeypatch, _row(user_id="alice"))
    assert (
        await build_diagnosis(_sessionmaker(), "exec-1", workflow_id="wf-1", user_id="bob") is None
    )


async def test_own_execution_of_a_different_workflow_is_not_diagnosed(
    monkeypatch: pytest.MonkeyPatch,
):
    _repo_returning(monkeypatch, _row(workflow_id="wf-other", user_id="alice"))
    assert (
        await build_diagnosis(_sessionmaker(), "exec-1", workflow_id="wf-1", user_id="alice")
        is None
    )


async def test_admin_sees_other_users_run_of_same_workflow(monkeypatch: pytest.MonkeyPatch):
    _repo_returning(monkeypatch, _row(user_id="alice"))
    diag = await build_diagnosis(
        _sessionmaker(), "exec-1", workflow_id="wf-1", user_id="root", role="admin"
    )
    assert diag is not None


async def test_admin_still_limited_to_the_workflow_being_built(monkeypatch: pytest.MonkeyPatch):
    _repo_returning(monkeypatch, _row(workflow_id="wf-other", user_id="alice"))
    assert (
        await build_diagnosis(
            _sessionmaker(), "exec-1", workflow_id="wf-1", user_id="root", role="admin"
        )
        is None
    )


async def test_the_read_session_is_closed_before_the_diagnosis_is_returned(
    monkeypatch: pytest.MonkeyPatch,
):
    """The builder runs its LLM loop right after: no session may still be open then."""
    _repo_returning(monkeypatch, _row())
    assert _OPEN_SESSIONS == []
    diag = await build_diagnosis(_sessionmaker(), "exec-1", workflow_id="wf-1", user_id="alice")
    assert diag is not None
    assert _OPEN_SESSIONS == []
