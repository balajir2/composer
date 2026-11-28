# pyright: reportPrivateUsage=false
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from src.api.execution_observability import (
    _attempt,
    _failure_guidance,
    _recovery_state,
    _to_admin_observability,
    _to_owner_observability,
)
from src.db.models import AttemptResult, AttemptTriggerKind
from src.db.repos.execution_attempts import AttemptRecord
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
        "failure_code": "execution_timeout",
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
    assert _recovery_state("canceled").value == "ineligible_canceled"
    assert all(_recovery_state(s).value == "not_applicable" for s in ("running", "completed"))


@pytest.mark.parametrize(
    ("code", "message"),
    [
        (
            "execution_timeout",
            "The execution timed out. Try again later or contact an administrator.",
        ),
        (
            "execution_error",
            "The execution failed. Contact an administrator if the problem continues.",
        ),
        (
            "lease_expired",
            "Processing stopped after its lease expired. Contact an administrator if the problem continues.",
        ),
        (
            "orphaned_running",
            "Processing stopped unexpectedly. Contact an administrator if the problem continues.",
        ),
        ("checkpoint_missing", "Execution state is unavailable. Contact an administrator."),
        ("checkpoint_sync_failed", "Execution state could not be read. Contact an administrator."),
        (
            "checkpoint_incompatible",
            "The workflow was changed while this run was paused, so it cannot continue. Start a new run.",
        ),
    ],
)
def test_controlled_failure_codes_have_fixed_safe_guidance(code: str, message: str) -> None:
    assert _failure_guidance(code) == message


def test_absent_and_unknown_failure_codes_use_generic_guidance() -> None:
    assert (
        _failure_guidance(None)
        == "This execution failed. Contact an administrator if the problem continues."
    )
    assert (
        _failure_guidance("unknown")
        == "This execution failed. Contact an administrator if the problem continues."
    )
    assert _to_owner_observability(_execution(failure_code=None), 0, False).failure_guidance == (
        "This execution failed. Contact an administrator if the problem continues."
    )


def _attempt_record() -> AttemptRecord:
    instant = datetime(2026, 1, 1, tzinfo=UTC)
    return AttemptRecord(
        id="attempt-id",
        execution_id="e1",
        ordinal=2,
        started_at=instant,
        ended_at=instant,
        result=AttemptResult.FAILED,
        failure_code="execution_error",
        lease_owner="lease-secret",
        lease_expires_at=instant,
        trigger_kind=AttemptTriggerKind.RECOVERY,
        recovery_source_attempt_id="source-secret",
        approval_id="approval-secret",
        actor_user_id="actor-secret",
        reason_ref="reason-secret",
    )


def test_owner_attempt_projection_has_only_safe_allowlisted_fields() -> None:
    result = _attempt(_attempt_record(), admin=False)
    body = result.model_dump(by_alias=True, exclude_unset=True)
    assert set(body) == {
        "id",
        "ordinal",
        "startedAt",
        "endedAt",
        "result",
        "triggerKind",
        "failureGuidance",
        "hasApproval",
    }
    assert body["hasApproval"] is True
    assert "secret" not in str(body)
    assert (
        _attempt(
            replace(_attempt_record(), result=AttemptResult.COMPLETED), admin=False
        ).failure_guidance
        is None
    )


def test_admin_attempt_projection_includes_operational_fields() -> None:
    body = _attempt(_attempt_record(), admin=True).model_dump(by_alias=True, exclude_unset=True)
    assert body["leaseOwner"] == "lease-secret"
    assert body["failureCode"] == "execution_error"
    assert body["recoverySourceAttemptId"] == "source-secret"
    assert body["approvalId"] == "approval-secret"
    assert body["actorUserId"] == "actor-secret"
    assert body["reasonRef"] == "reason-secret"
