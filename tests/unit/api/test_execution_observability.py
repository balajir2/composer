from datetime import UTC, datetime
from typing import Any

from src.api.execution_observability import (
    _failure_guidance,
    _recovery_state,
    _to_admin_observability,
    _to_owner_observability,
)
from src.db.repos.executions import ExecutionRecord


def _execution(**overrides: Any) -> ExecutionRecord:
    values: dict[str, Any] = {
        "id": "e1",
        "workflow_id": "w1",
        "user_id": "u1",
        "status": "failed",
        "current_node_id": "n1",
        "node_results": {},
        "variables": {},
        "input": None,
        "output": None,
        "error": "secret",
        "failure_code": "node_timeout",
        "started_at": datetime(2026, 1, 1, tzinfo=UTC),
        "completed_at": None,
        "thread_id": "thread-secret",
        "original_owner_email": None,
        "idempotency_key": None,
        "lease_owner": "worker-secret",
        "lease_expires_at": datetime(2026, 1, 1, tzinfo=UTC),
        "running_since": None,
        "delivery_attempts": 2,
    }
    values.update(overrides)
    return ExecutionRecord(**values)


def test_owner_projection_omits_admin_fields_and_unknown_error() -> None:
    result = _to_owner_observability(_execution(failure_code="not-controlled"), 3, True)
    body = result.model_dump(by_alias=True)
    assert (
        body["failureGuidance"]
        == "This execution failed. Contact an administrator if the problem continues."
    )
    assert "adminDiagnostics" not in body or body["adminDiagnostics"] is None
    assert "threadId" not in str(body) and "worker-secret" not in str(body)
    assert result.recovery_state.value == "assessment_required"


def test_admin_projection_and_recovery_states() -> None:
    result = _to_admin_observability(_execution(), 1, True)
    assert result.admin_diagnostics is not None
    assert result.admin_diagnostics.thread_id == "thread-secret"
    assert _failure_guidance("node_timeout") is not None
    assert _recovery_state("canceled").value == "ineligible_canceled"
    assert all(_recovery_state(s).value == "not_applicable" for s in ("running", "completed"))
