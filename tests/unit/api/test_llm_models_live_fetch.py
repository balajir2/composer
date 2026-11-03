"""Behavioral tests for src/api/llm_models_live.py: parsers, upstream fetch
failures, probe filtering, cache semantics, DB fallback and authn."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient

from src.api import llm_models_live as mod
from src.api.admin_llm_models_verify import ModelVerifyResult, VerifyStatus
from src.main import create_app

if TYPE_CHECKING:
    from collections.abc import Iterator

    from pytest_httpx import HTTPXMock

_SECRET = "sk-live-super-secret-key"


@pytest.fixture(autouse=True)
def _clear_cache() -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
    mod._cache.clear()  # pyright: ignore[reportPrivateUsage]
    yield
    mod._cache.clear()  # pyright: ignore[reportPrivateUsage]


class _FakeVerify:
    """Named fake for run_model_verify keyed by model id."""

    def __init__(self, outcomes: dict[str, VerifyStatus], default: VerifyStatus = "ok") -> None:
        self.outcomes: dict[str, VerifyStatus] = outcomes
        self.default: VerifyStatus = default
        self.calls: list[tuple[str, str, str]] = []

    async def __call__(self, provider: str, model_id: str, key: str) -> ModelVerifyResult:
        self.calls.append((provider, model_id, key))
        return ModelVerifyResult(
            status=self.outcomes.get(model_id, self.default), http_status=None, message="m"
        )


def _patch_settings(monkeypatch: pytest.MonkeyPatch, **fields: str) -> None:
    monkeypatch.setattr(mod, "get_settings", lambda: SimpleNamespace(**fields))


# ─── parsers ────────────────────────────────────────────────────────────────


def test_parse_anthropic_skips_malformed_entries_and_keeps_labels() -> None:
    data: dict[str, Any] = {
        "data": [
            {"id": "claude-a", "display_name": "Claude A"},
            {"id": "claude-b", "display_name": 5},
            {"id": ""},
            {"no_id": True},
            "not-a-dict",
        ]
    }
    out = mod._parse_anthropic(data)  # pyright: ignore[reportPrivateUsage]
    assert [(m.model_id, m.label) for m in out] == [("claude-a", "Claude A"), ("claude-b", None)]
    assert all(m.source == "live" for m in out)


def test_parse_openai_compat_filters_non_chat_models() -> None:
    data: dict[str, Any] = {
        "data": [
            {"id": "gpt-4o"},
            {"id": "text-embedding-3-small"},
            {"id": "Whisper-1"},
            {"id": "tts-1"},
            {"id": "dall-e-3"},
            {"id": "omni-moderation-latest"},
            {"id": 7},
            None,
        ]
    }
    out = mod._parse_openai_compat(data)  # pyright: ignore[reportPrivateUsage]
    assert [m.model_id for m in out] == ["gpt-4o"]


def test_parse_google_keeps_only_generate_content_and_strips_prefix() -> None:
    data: dict[str, Any] = {
        "models": [
            {
                "name": "models/gemini-pro",
                "displayName": "Gemini Pro",
                "supportedGenerationMethods": ["generateContent"],
            },
            {"name": "models/embed", "supportedGenerationMethods": ["embedContent"]},
            {"name": "bare-name", "supportedGenerationMethods": ["generateContent"]},
            {"name": ""},
            "junk",
        ]
    }
    out = mod._parse_google(data)  # pyright: ignore[reportPrivateUsage]
    assert [(m.model_id, m.label) for m in out] == [
        ("gemini-pro", "Gemini Pro"),
        ("bare-name", None),
    ]


# ─── _fetch_live ────────────────────────────────────────────────────────────


async def test_fetch_live_raises_when_api_key_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, openai_api_key="")
    with pytest.raises(RuntimeError, match="OPENAI API key not configured"):
        await mod._fetch_live("openai")  # pyright: ignore[reportPrivateUsage]


async def test_fetch_live_upstream_error_raises_without_leaking_key(
    monkeypatch: pytest.MonkeyPatch, httpx_mock: HTTPXMock
) -> None:
    _patch_settings(monkeypatch, openai_api_key=_SECRET)
    httpx_mock.add_response(url="https://api.openai.com/v1/models", status_code=503, text="down")
    with pytest.raises(RuntimeError, match="HTTP 503") as info:
        await mod._fetch_live("openai")  # pyright: ignore[reportPrivateUsage]
    assert _SECRET not in str(info.value)


async def test_fetch_live_malformed_json_propagates(
    monkeypatch: pytest.MonkeyPatch, httpx_mock: HTTPXMock
) -> None:
    _patch_settings(monkeypatch, groq_api_key=_SECRET)
    httpx_mock.add_response(url="https://api.groq.com/openai/v1/models", text="<html>not json")
    with pytest.raises(ValueError):
        await mod._fetch_live("groq")  # pyright: ignore[reportPrivateUsage]


async def test_fetch_live_timeout_propagates(
    monkeypatch: pytest.MonkeyPatch, httpx_mock: HTTPXMock
) -> None:
    _patch_settings(monkeypatch, groq_api_key=_SECRET)
    httpx_mock.add_exception(httpx.ReadTimeout("slow"))
    with pytest.raises(httpx.ReadTimeout):
        await mod._fetch_live("groq")  # pyright: ignore[reportPrivateUsage]


async def test_fetch_live_sends_anthropic_headers_sorts_and_drops_unavailable_models(
    monkeypatch: pytest.MonkeyPatch, httpx_mock: HTTPXMock
) -> None:
    _patch_settings(monkeypatch, anthropic_api_key=_SECRET)
    fake = _FakeVerify({"retired": "unavailable"})
    monkeypatch.setattr("src.api.admin_llm_models_verify.run_model_verify", fake)
    httpx_mock.add_response(
        url="https://api.anthropic.com/v1/models",
        json={"data": [{"id": "zeta"}, {"id": "Alpha"}, {"id": "retired"}]},
    )
    out = await mod._fetch_live("anthropic")  # pyright: ignore[reportPrivateUsage]
    assert [m.model_id for m in out] == ["Alpha", "zeta"]
    req = httpx_mock.get_requests()[0]
    assert req.headers["x-api-key"] == _SECRET
    assert req.headers["anthropic-version"] == "2023-06-01"
    assert {c[1] for c in fake.calls} == {"zeta", "Alpha", "retired"}


async def test_fetch_live_keeps_models_when_probe_is_inconclusive(
    monkeypatch: pytest.MonkeyPatch, httpx_mock: HTTPXMock
) -> None:
    _patch_settings(monkeypatch, openai_api_key=_SECRET)
    fake = _FakeVerify({}, default="auth_error")
    monkeypatch.setattr("src.api.admin_llm_models_verify.run_model_verify", fake)
    httpx_mock.add_response(
        url="https://api.openai.com/v1/models", json={"data": [{"id": "a"}, {"id": "b"}]}
    )
    out = await mod._fetch_live("openai")  # pyright: ignore[reportPrivateUsage]
    assert [m.model_id for m in out] == ["a", "b"]


async def test_fetch_live_google_passes_key_as_query_and_no_auth_header(
    monkeypatch: pytest.MonkeyPatch, httpx_mock: HTTPXMock
) -> None:
    _patch_settings(monkeypatch, google_api_key=_SECRET)
    monkeypatch.setattr("src.api.admin_llm_models_verify.run_model_verify", _FakeVerify({}))
    httpx_mock.add_response(
        json={"models": [{"name": "models/g1", "supportedGenerationMethods": ["generateContent"]}]}
    )
    out = await mod._fetch_live("google")  # pyright: ignore[reportPrivateUsage]
    assert [m.model_id for m in out] == ["g1"]
    req = httpx_mock.get_requests()[0]
    assert req.url.params["key"] == _SECRET
    assert "authorization" not in req.headers


async def test_fetch_live_empty_catalog_skips_probe(
    monkeypatch: pytest.MonkeyPatch, httpx_mock: HTTPXMock
) -> None:
    _patch_settings(monkeypatch, deepseek_api_key=_SECRET)
    fake = _FakeVerify({})
    monkeypatch.setattr("src.api.admin_llm_models_verify.run_model_verify", fake)
    httpx_mock.add_response(url="https://api.deepseek.com/models", json={"data": []})
    assert await mod._fetch_live("deepseek") == []  # pyright: ignore[reportPrivateUsage]
    assert fake.calls == []


# ─── endpoint ───────────────────────────────────────────────────────────────


def _client(
    monkeypatch: pytest.MonkeyPatch, environment: str = "development"
) -> tuple[TestClient, MagicMock]:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", environment)
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()
    db = MagicMock()
    rows = [SimpleNamespace(modelId="db-model", label="DB Model")]
    db.llmmodel.find_many = AsyncMock(return_value=rows)
    app.state.db = db
    app.state.checkpointer = MagicMock()
    return TestClient(app), db


class _CountingFetch:
    def __init__(self, models: list[str] | Exception) -> None:
        self.models = models
        self.calls = 0

    async def __call__(self, provider: str) -> list[mod.LiveModel]:
        self.calls += 1
        if isinstance(self.models, Exception):
            raise self.models
        return [mod.LiveModel(modelId=m, source="live") for m in self.models]


def test_available_models_requires_authentication_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = _client(monkeypatch, environment="production")
    resp = client.get("/llm-models/available", params={"provider": "openai"})
    assert resp.status_code == 401


def test_available_models_rejects_unsupported_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client(monkeypatch)
    resp = client.get("/llm-models/available", params={"provider": "nope"})
    assert resp.status_code == 422
    assert "Unsupported provider" in resp.json()["detail"]


def test_available_models_second_call_is_served_from_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch = _CountingFetch(["m1"])
    monkeypatch.setattr(mod, "_fetch_live", fetch)
    client, _ = _client(monkeypatch)
    first = client.get("/llm-models/available", params={"provider": "openai"}).json()
    second = client.get("/llm-models/available", params={"provider": "openai"}).json()
    assert first == second
    assert first["models"][0]["modelId"] == "m1"
    assert fetch.calls == 1


def test_available_models_refresh_bypasses_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    fetch = _CountingFetch(["m1"])
    monkeypatch.setattr(mod, "_fetch_live", fetch)
    client, _ = _client(monkeypatch)
    client.get("/llm-models/available", params={"provider": "openai"})
    client.get("/llm-models/available", params={"provider": "openai", "refresh": "true"})
    assert fetch.calls == 2


def test_available_models_upstream_failure_falls_back_to_db_and_is_not_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch = _CountingFetch(RuntimeError(f"boom {_SECRET}"))
    monkeypatch.setattr(mod, "_fetch_live", fetch)
    client, db = _client(monkeypatch)
    resp = client.get("/llm-models/available", params={"provider": "openai"})
    assert resp.status_code == 200
    body = cast("dict[str, Any]", resp.json())
    assert body["models"] == [{"modelId": "db-model", "label": "DB Model", "source": "db"}]
    assert _SECRET not in resp.text
    client.get("/llm-models/available", params={"provider": "openai"})
    assert fetch.calls == 2  # failure was not cached
    where = db.llmmodel.find_many.await_args.kwargs["where"]
    assert where == {"provider": "openai", "enabled": True}


def test_available_models_expired_cache_entry_is_refetched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch = _CountingFetch(["fresh"])
    monkeypatch.setattr(mod, "_fetch_live", fetch)
    mod._cache["openai"] = (  # pyright: ignore[reportPrivateUsage]
        -10_000.0,
        [mod.LiveModel(modelId="stale", source="live")],
    )
    client, _ = _client(monkeypatch)
    body = client.get("/llm-models/available", params={"provider": "openai"}).json()
    assert body["models"][0]["modelId"] == "fresh"
