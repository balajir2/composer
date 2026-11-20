"""The agent refuses a model the admin catalog has verified as unavailable."""

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.db.repos import llm_models as models_repo
from src.db.repos.llm_models import LlmModelRecord
from src.db.runtime import register_sessionmaker
from src.executors import agent as agent_mod
from tests.unit.db_fakes import FakeSessionmaker

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _row(status: str | None, message: str | None = None) -> LlmModelRecord:
    return LlmModelRecord(
        id="m1",
        provider="google",
        model_id="gemini-x",
        label=None,
        enabled=True,
        verification_status=status,
        verification_message=message,
        verified_at=None,
        created_at=_NOW,
        updated_at=_NOW,
    )


@pytest.fixture
def sessionmaker() -> Iterator[FakeSessionmaker]:
    maker = FakeSessionmaker()
    register_sessionmaker(maker)  # pyright: ignore[reportArgumentType]
    yield maker
    register_sessionmaker(None)


async def test_unavailable_model_raises_with_the_stored_message(
    monkeypatch: pytest.MonkeyPatch, sessionmaker: FakeSessionmaker
) -> None:
    get = AsyncMock(return_value=_row("unavailable", "retired for new keys"))
    monkeypatch.setattr(models_repo, "get_by_provider_model", get)
    with pytest.raises(agent_mod.ModelUnavailableError, match="retired for new keys"):
        await agent_mod._ensure_model_available("google/gemini-x")  # pyright: ignore[reportPrivateUsage]
    get.assert_awaited_once_with(sessionmaker.session, "google", "gemini-x")


@pytest.mark.parametrize("row", [None, _row(None), _row("ok")])
async def test_other_catalog_states_are_allowed(
    monkeypatch: pytest.MonkeyPatch, sessionmaker: FakeSessionmaker, row: Any
) -> None:
    monkeypatch.setattr(models_repo, "get_by_provider_model", AsyncMock(return_value=row))
    await agent_mod._ensure_model_available("google/gemini-x")  # pyright: ignore[reportPrivateUsage]


async def test_no_registered_sessionmaker_skips_the_check(monkeypatch: pytest.MonkeyPatch) -> None:
    register_sessionmaker(None)
    get = AsyncMock()
    monkeypatch.setattr(models_repo, "get_by_provider_model", get)
    await agent_mod._ensure_model_available("google/gemini-x")  # pyright: ignore[reportPrivateUsage]
    get.assert_not_called()


async def test_model_string_without_provider_skips_the_check(
    monkeypatch: pytest.MonkeyPatch, sessionmaker: FakeSessionmaker
) -> None:
    get = AsyncMock()
    monkeypatch.setattr(models_repo, "get_by_provider_model", get)
    await agent_mod._ensure_model_available("gemini-x")  # pyright: ignore[reportPrivateUsage]
    get.assert_not_called()
