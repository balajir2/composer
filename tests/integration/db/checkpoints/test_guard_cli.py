"""`composer checkpoints guard` against the test database, then fixed by the copy tool."""

import os

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from src.checkpoints.legacy import LegacyCheckpointSaver
from src.cli.checkpoints import checkpoints_copy, checkpoints_guard
from src.config import get_settings
from src.engine.state import initial_state
from tests.integration.db.checkpoints.test_copy_round_trip import (
    compile_graph,
    lg,
    postgres,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from tests.integration.db.checkpoints.test_guard import (
    MakeRun,
    make_run,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from tests.integration.sqla_phase4a.conftest import approval_graph

pytestmark = pytest.mark.integration


async def test_guard_refuses_then_passes_after_copy(
    legacy: LegacyCheckpointSaver,
    postgres: AsyncPostgresSaver,  # noqa: F811
    make_run: MakeRun,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    eid, thread = await make_run("waiting_approval")
    await compile_graph(approval_graph, legacy).ainvoke(initial_state("x"), config=lg(thread))
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    get_settings.cache_clear()
    try:
        code = await checkpoints_guard()
        out = capsys.readouterr().out
        assert code == 1
        assert f"blocked: {eid} (waiting_approval)" in out
        assert "active runs: queued=" in out and "copy waiting runs with" in out
        assert thread not in out and "postgresql" not in out and "composer_test" not in out

        assert await checkpoints_copy("postgres", [eid], False, False) == 0
        capsys.readouterr()
        code = await checkpoints_guard()
        out = capsys.readouterr().out
        assert code == 0 and "ok:" in out and eid not in out
    finally:
        get_settings.cache_clear()
