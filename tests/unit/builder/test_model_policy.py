"""Tests for the admin-controlled set of models the builder chat may use."""

from datetime import UTC, datetime
from typing import Any

import pytest

from src.builder.model_policy import (
    DEFAULT_BUILDER_MODEL,
    ModelNotAllowedError,
    load_policy,
    save_policy,
)
from src.db.repos import deployment_settings as settings_repo
from src.db.repos import llm_models as models_repo
from src.db.repos.deployment_settings import SettingRecord
from src.db.repos.llm_models import LlmModelRecord
from tests.unit.db_fakes import FakeSession

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _db(
    monkeypatch: pytest.MonkeyPatch,
    settings: dict[str, str] | None = None,
    enabled: list[tuple[str, str, str | None]] | None = None,
) -> Any:
    """Fake the repository layer in memory; the 'session' is a stand-in object."""
    values = settings if settings is not None else {}
    models = enabled or [
        ("anthropic", "claude-sonnet-4-5", "Sonnet"),
        ("openai", "gpt-5", None),
        ("groq", "llama", None),
    ]

    async def get(_session: Any, key: str) -> SettingRecord | None:
        return SettingRecord(key=key, value=values[key], updated_at=_NOW) if key in values else None

    async def upsert(_session: Any, key: str, value: str) -> SettingRecord:
        values[key] = value
        return SettingRecord(key=key, value=value, updated_at=_NOW)

    async def list_models(
        _session: Any, *, provider: str | None = None, enabled_only: bool = False
    ) -> list[LlmModelRecord]:
        assert enabled_only and provider is None
        return [
            LlmModelRecord(
                id=f"{p}-{m}",
                provider=p,
                model_id=m,
                label=label,
                enabled=True,
                verification_status=None,
                verification_message=None,
                verified_at=None,
                created_at=_NOW,
                updated_at=_NOW,
            )
            for p, m, label in models
        ]

    monkeypatch.setattr(settings_repo, "get", get)
    monkeypatch.setattr(settings_repo, "upsert", upsert)
    monkeypatch.setattr(models_repo, "list_models", list_models)
    return FakeSession()


async def test_unconfigured_policy_allows_only_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    policy = await load_policy(_db(monkeypatch))

    assert policy.default == DEFAULT_BUILDER_MODEL
    assert policy.allowed_ids == [DEFAULT_BUILDER_MODEL]


async def test_admin_allowed_models_are_offered_alongside_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _db(
        monkeypatch,
        {
            "builder_model": "anthropic/claude-sonnet-4-5",
            "builder_allowed_models": '["openai/gpt-5"]',
        },
    )

    policy = await load_policy(db)

    assert policy.allowed_ids == ["anthropic/claude-sonnet-4-5", "openai/gpt-5"]


async def test_models_disabled_in_the_catalog_are_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _db(
        monkeypatch,
        {"builder_allowed_models": '["openai/gpt-5", "openai/retired"]'},
    )

    policy = await load_policy(db)

    assert "openai/retired" not in policy.allowed_ids
    assert "openai/gpt-5" in policy.allowed_ids


async def test_corrupt_allowed_setting_falls_back_to_default_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = await load_policy(_db(monkeypatch, {"builder_allowed_models": "not json"}))

    assert policy.allowed_ids == [DEFAULT_BUILDER_MODEL]


async def test_resolve_defaults_when_nothing_requested(monkeypatch: pytest.MonkeyPatch) -> None:
    policy = await load_policy(_db(monkeypatch))

    assert policy.resolve(None) == DEFAULT_BUILDER_MODEL


async def test_resolve_rejects_a_model_the_admin_did_not_allow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = await load_policy(_db(monkeypatch, {"builder_allowed_models": '["openai/gpt-5"]'}))

    assert policy.resolve("openai/gpt-5") == "openai/gpt-5"
    with pytest.raises(ModelNotAllowedError):
        policy.resolve("groq/llama")


async def test_labels_come_from_the_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    policy = await load_policy(_db(monkeypatch, {"builder_allowed_models": '["openai/gpt-5"]'}))

    labels = {m.id: m.label for m in policy.options}
    assert labels["anthropic/claude-sonnet-4-5"] == "Sonnet"
    assert labels["openai/gpt-5"] == "openai/gpt-5"  # no label -> falls back to the id


async def test_save_policy_validates_against_the_enabled_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _db(monkeypatch)

    with pytest.raises(ValueError, match="openai/missing"):
        await save_policy(db, default="anthropic/claude-sonnet-4-5", allowed=["openai/missing"])


async def test_save_policy_round_trips_and_keeps_the_default_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _db(monkeypatch)

    await save_policy(db, default="openai/gpt-5", allowed=["groq/llama"])
    policy = await load_policy(db)

    assert policy.default == "openai/gpt-5"
    assert policy.allowed_ids == ["openai/gpt-5", "groq/llama"]
