"""Tests for the admin-controlled set of models the builder chat may use."""

from types import SimpleNamespace
from typing import Any

import pytest

from src.builder.model_policy import (
    DEFAULT_BUILDER_MODEL,
    ModelNotAllowedError,
    load_policy,
    save_policy,
)


class _Settings:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    async def find_unique(self, *, where: dict[str, str]) -> Any:
        key = where["key"]
        return SimpleNamespace(key=key, value=self.values[key]) if key in self.values else None

    async def upsert(self, *, where: dict[str, str], data: dict[str, Any]) -> None:
        self.values[where["key"]] = data["create"]["value"]


class _Models:
    def __init__(self, enabled: list[tuple[str, str, str | None]]) -> None:
        self.rows = [
            SimpleNamespace(provider=p, modelId=m, label=label, enabled=True)
            for p, m, label in enabled
        ]

    async def find_many(self, **_kwargs: Any) -> list[Any]:
        return self.rows


def _db(
    settings: dict[str, str] | None = None,
    enabled: list[tuple[str, str, str | None]] | None = None,
) -> Any:
    return SimpleNamespace(
        deploymentsetting=_Settings(settings or {}),
        llmmodel=_Models(
            enabled
            or [
                ("anthropic", "claude-sonnet-4-5", "Sonnet"),
                ("openai", "gpt-5", None),
                ("groq", "llama", None),
            ]
        ),
    )


async def test_unconfigured_policy_allows_only_the_default() -> None:
    policy = await load_policy(_db())

    assert policy.default == DEFAULT_BUILDER_MODEL
    assert policy.allowed_ids == [DEFAULT_BUILDER_MODEL]


async def test_admin_allowed_models_are_offered_alongside_the_default() -> None:
    db = _db(
        {
            "builder_model": "anthropic/claude-sonnet-4-5",
            "builder_allowed_models": '["openai/gpt-5"]',
        }
    )

    policy = await load_policy(db)

    assert policy.allowed_ids == ["anthropic/claude-sonnet-4-5", "openai/gpt-5"]


async def test_models_disabled_in_the_catalog_are_dropped() -> None:
    db = _db(
        {"builder_allowed_models": '["openai/gpt-5", "openai/retired"]'},
    )

    policy = await load_policy(db)

    assert "openai/retired" not in policy.allowed_ids
    assert "openai/gpt-5" in policy.allowed_ids


async def test_corrupt_allowed_setting_falls_back_to_default_only() -> None:
    policy = await load_policy(_db({"builder_allowed_models": "not json"}))

    assert policy.allowed_ids == [DEFAULT_BUILDER_MODEL]


async def test_resolve_defaults_when_nothing_requested() -> None:
    policy = await load_policy(_db())

    assert policy.resolve(None) == DEFAULT_BUILDER_MODEL


async def test_resolve_rejects_a_model_the_admin_did_not_allow() -> None:
    policy = await load_policy(_db({"builder_allowed_models": '["openai/gpt-5"]'}))

    assert policy.resolve("openai/gpt-5") == "openai/gpt-5"
    with pytest.raises(ModelNotAllowedError):
        policy.resolve("groq/llama")


async def test_labels_come_from_the_catalog() -> None:
    policy = await load_policy(_db({"builder_allowed_models": '["openai/gpt-5"]'}))

    labels = {m.id: m.label for m in policy.options}
    assert labels["anthropic/claude-sonnet-4-5"] == "Sonnet"
    assert labels["openai/gpt-5"] == "openai/gpt-5"  # no label -> falls back to the id


async def test_save_policy_validates_against_the_enabled_catalog() -> None:
    db = _db()

    with pytest.raises(ValueError, match="openai/missing"):
        await save_policy(db, default="anthropic/claude-sonnet-4-5", allowed=["openai/missing"])


async def test_save_policy_round_trips_and_keeps_the_default_allowed() -> None:
    db = _db()

    await save_policy(db, default="openai/gpt-5", allowed=["groq/llama"])
    policy = await load_policy(db)

    assert policy.default == "openai/gpt-5"
    assert policy.allowed_ids == ["openai/gpt-5", "groq/llama"]
