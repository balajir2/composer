"""A failure while copying a paused run's checkpoints forward must fail the run with its own
``checkpoint_sync_failed`` code, never run the graph, and leave the old-table rows untouched."""

import pytest

from src.checkpoints import sync
from tests.integration.sqla_phase4a.conftest import (
    HTTPX_MARK,
    Db,
    Member,
    approval_graph,
    install_email_recorder,
)
from tests.integration.sqla_phase4b.conftest import resume
from tests.integration.sqla_phase5.conftest import checkpoint_row_counts, running_app
from tests.integration.sqla_phase5.test_deliberate_changes import (
    NEW,
    OLD,
    _only,  # pyright: ignore[reportPrivateUsage]
    _seed_paused_on_old_tables,  # pyright: ignore[reportPrivateUsage]
)

pytestmark = [pytest.mark.integration, HTTPX_MARK]


async def test_failing_copy_fails_the_run_with_checkpoint_sync_failed(
    db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_email_recorder(monkeypatch)
    async with running_app(monkeypatch) as a:
        eid, thread = await _seed_paused_on_old_tables(db, member, a.sm, approval_graph)
        before = await db.must(eid)
        old_before = _only(await checkpoint_row_counts(a.sm, thread), OLD)
        assert old_before["langgraph_checkpoints"] > 0

        async def _boom(*_a: object, **_k: object) -> None:
            raise OSError("copy broke")

        monkeypatch.setattr(sync, "copy_thread", _boom)
        r = await resume(a.client, member, eid, "approved")
        assert r.status_code == 200, r.text

        row = await db.must(eid)
        assert (row.status, row.failure_code) == ("failed", "checkpoint_sync_failed")
        assert row.node_results == before.node_results  # the graph never ran
        after = await checkpoint_row_counts(a.sm, thread)
        assert _only(after, OLD) == old_before  # recoverable: old rows untouched
        assert _only(after, NEW) == dict.fromkeys(NEW, 0)
