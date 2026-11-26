"""The lifespan's database-facing startup runs under one deadline (startup-hang fix, 2026-11-26).

Uvicorn binds the port only after startup; a step stuck on a silent database used to hang the
instance until Cloud Run's 240 s startup probe killed it.
"""

# pyright: reportPrivateUsage=false

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

import pytest
from fastapi import FastAPI

from src import main

entered: list[str] = []
exited: list[str] = []


def _fake_lifespan(name: str, *, hang: bool = False, error: BaseException | None = None) -> Any:
    @asynccontextmanager
    async def _cm(app: FastAPI) -> AsyncGenerator[None]:
        if hang:
            await asyncio.Event().wait()
        if error is not None:
            raise error
        entered.append(name)
        if name == "sqlalchemy":
            app.state.db_sessionmaker = _FakeSessionmaker()
        try:
            yield
        finally:
            exited.append(name)

    return _cm


class _FakeSessionmaker:
    @asynccontextmanager
    async def __call__(self) -> AsyncGenerator[object]:
        yield object()


@pytest.fixture(autouse=True)
def _fakes(monkeypatch: pytest.MonkeyPatch) -> None:  # pyright: ignore[reportUnusedFunction]
    entered.clear()
    exited.clear()
    for name in ("sqlalchemy", "checkpointer", "notifications"):
        monkeypatch.setattr(main, f"{name}_lifespan", _fake_lifespan(name))

    def _store(_sessionmaker: object) -> object:
        return object()

    monkeypatch.setattr(main, "PostgresEventStore", _store)
    monkeypatch.setattr(main, "PostgresRateLimiter", _store)

    async def _sync(_session: object) -> dict[str, str]:
        return {}

    monkeypatch.setattr(main, "sync_llm_keys_from_db", _sync)


async def _start(timeout: float = 5.0) -> FastAPI:
    app = FastAPI()
    async with AsyncExitStack() as stack:
        await main._start_database_services(app, stack, timeout)
    return app


async def test_all_steps_start_in_order_log_progress_and_unwind_in_reverse(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="src.main")
    await _start()
    assert entered == ["sqlalchemy", "checkpointer", "notifications"]
    assert exited == ["notifications", "checkpointer", "sqlalchemy"]
    assert [r.getMessage() for r in caplog.records if r.name == "src.main"] == [
        "startup: sqlalchemy ready",
        "startup: checkpointer ready",
        "startup: notifications ready",
        "startup: llm key sync done",
    ]


@pytest.mark.parametrize("stuck", ["sqlalchemy", "checkpointer", "notifications"])
async def test_a_stuck_step_hits_the_deadline_names_it_and_unwinds_the_rest(
    monkeypatch: pytest.MonkeyPatch, stuck: str
) -> None:
    monkeypatch.setattr(main, f"{stuck}_lifespan", _fake_lifespan(stuck, hang=True))
    with pytest.raises(TimeoutError, match=rf"within 0.05s \(stuck in: {stuck}\)"):
        await _start(timeout=0.05)
    assert exited == list(reversed(entered))  # everything entered was exited


async def test_a_stuck_llm_key_sync_hits_the_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _stuck(_session: object) -> dict[str, str]:
        await asyncio.Event().wait()
        return {}

    monkeypatch.setattr(main, "sync_llm_keys_from_db", _stuck)
    with pytest.raises(TimeoutError, match=r"stuck in: llm key sync"):
        await _start(timeout=0.05)
    assert exited == ["notifications", "checkpointer", "sqlalchemy"]


async def test_a_steps_own_timeout_propagates_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    own = TimeoutError("checkpointer schema check timed out")
    monkeypatch.setattr(main, "checkpointer_lifespan", _fake_lifespan("checkpointer", error=own))
    with pytest.raises(TimeoutError) as info:
        await _start(timeout=5.0)
    assert info.value is own
    assert exited == ["sqlalchemy"]


def test_default_deadline_is_well_under_the_startup_probe() -> None:
    from src.config import Settings

    assert Settings().startup_timeout_seconds == 60.0  # Cloud Run's TCP startup probe: 240 s
