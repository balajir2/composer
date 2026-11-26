"""API authorization and empty-history behavior for observability reads."""

import pytest

from tests.integration.sqla_phase4a.conftest import Db, ExecApi, Member

pytestmark = pytest.mark.integration


async def test_owner_legacy_history_and_page_validation(
    db: Db, member: Member, exec_api: ExecApi
) -> None:
    workflow_id = await db.seed_workflow(member)
    execution_id = await db.seed_execution(workflow_id, member)
    summary = await exec_api.client.get(
        f"/executions/{execution_id}/observability", headers=member.headers
    )
    assert summary.status_code == 200, summary.text
    assert summary.json()["historyAvailable"] is False
    attempts = await exec_api.client.get(
        f"/executions/{execution_id}/attempts", headers=member.headers
    )
    assert attempts.json() == {
        "historyAvailable": False,
        "total": 0,
        "limit": 50,
        "offset": 0,
        "items": [],
    }
    for query in ("limit=0", "limit=101", "offset=-1"):
        response = await exec_api.client.get(
            f"/executions/{execution_id}/attempts?{query}", headers=member.headers
        )
        assert response.status_code == 422


async def test_unrelated_member_cannot_read_observability(
    db: Db, member: Member, make_user, exec_api: ExecApi
) -> None:
    second_member = await make_user()
    workflow_id = await db.seed_workflow(member)
    execution_id = await db.seed_execution(workflow_id, member)
    response = await exec_api.client.get(
        f"/executions/{execution_id}/observability", headers=second_member.headers
    )
    assert response.status_code == 404
