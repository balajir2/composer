from typing import Any

from src.builder.diagnostics import REDACT_MARKER, build_diagnosis


class _FakeDb:
    """Emulates the Prisma ``where`` filter so the test fails if a filter is dropped."""

    def __init__(self, row: Any) -> None:
        self._row: Any = row
        self.workflowexecution = self
        self.where: dict[str, Any] | None = None

    async def find_first(self, **kwargs: Any) -> Any:
        where: dict[str, Any] = kwargs["where"]
        self.where = where
        row = self._row
        if row is None:
            return None
        for key, value in where.items():
            if getattr(row, key, None) != value:
                return None
        return row


def _row(**overrides: Any):
    attrs: dict[str, Any] = {
        "id": "exec-1",
        "workflowId": "wf-1",
        "userId": "alice",
        "status": "failed",
        "error": "boom",
        "nodeResults": {"a": {"output": "x"}},
        "variables": {"apiKey": "SECRET", "safe": "ok"},
    }
    attrs.update(overrides)
    return type("Row", (), attrs)()


async def test_build_diagnosis_redacts_secret_keys():
    diag = await build_diagnosis(_FakeDb(_row()), "exec-1", workflow_id="wf-1", user_id="alice")
    assert diag is not None
    assert diag.status == "failed"
    assert diag.error == "boom"
    assert diag.variables_tail["apiKey"] == REDACT_MARKER
    assert diag.variables_tail["safe"] == "ok"


async def test_build_diagnosis_returns_none_for_missing_row():
    assert await build_diagnosis(_FakeDb(None), "nope", workflow_id="wf-1", user_id="alice") is None


async def test_other_users_execution_is_not_diagnosed():
    db = _FakeDb(_row(userId="alice"))
    assert await build_diagnosis(db, "exec-1", workflow_id="wf-1", user_id="bob") is None
    assert db.where == {"id": "exec-1", "workflowId": "wf-1", "userId": "bob"}


async def test_own_execution_of_a_different_workflow_is_not_diagnosed():
    db = _FakeDb(_row(workflowId="wf-other", userId="alice"))
    assert await build_diagnosis(db, "exec-1", workflow_id="wf-1", user_id="alice") is None
    assert db.where is not None and db.where["workflowId"] == "wf-1"


async def test_admin_sees_other_users_run_of_same_workflow():
    db = _FakeDb(_row(userId="alice"))
    diag = await build_diagnosis(db, "exec-1", workflow_id="wf-1", user_id="root", role="admin")
    assert diag is not None
    assert db.where == {"id": "exec-1", "workflowId": "wf-1"}


async def test_admin_still_limited_to_the_workflow_being_built():
    db = _FakeDb(_row(workflowId="wf-other", userId="alice"))
    assert (
        await build_diagnosis(db, "exec-1", workflow_id="wf-1", user_id="root", role="admin")
        is None
    )
