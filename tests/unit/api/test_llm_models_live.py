"""Unit tests for GET /llm-models/available.

Task 13: TypeSafe has no live /models discovery endpoint, so it must skip
the live-fetch path entirely and go straight to the DB-curated fallback via
`_DB_ONLY_PROVIDERS`.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.main import create_app


def _client_with_mock_db(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, MagicMock]:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()

    db = MagicMock()
    db.llmmodel = MagicMock()
    db.llmmodel.find_many = AsyncMock(return_value=[])
    # Dev-mode user_id='dev' has no user row by default -> role defaults to 'member'
    db.user = MagicMock()
    db.user.find_unique = AsyncMock(return_value=None)
    app.state.db = db
    app.state.checkpointer = MagicMock()
    return TestClient(app), db


def test_typesafe_skips_live_fetch_and_uses_db_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.api import llm_models_live as mod

    called = {"live": False}

    async def _fake_fetch_live(provider: str) -> list[Any]:
        called["live"] = True
        raise AssertionError("should not be called for a DB-only provider")

    monkeypatch.setattr(mod, "_fetch_live", _fake_fetch_live)

    client, _db = _client_with_mock_db(monkeypatch)
    resp = client.get("/llm-models/available", params={"provider": "typesafe"})

    assert resp.status_code == 200, resp.text
    assert called["live"] is False
    body = resp.json()
    assert body["provider"] == "typesafe"
    assert body["models"] == []


def test_typesafe_not_in_live_providers_table() -> None:
    from src.api.llm_models_live import (
        _DB_ONLY_PROVIDERS,  # pyright: ignore[reportPrivateUsage]
        _PROVIDERS,  # pyright: ignore[reportPrivateUsage]
    )

    assert "typesafe" not in _PROVIDERS  # pyright: ignore[reportPrivateUsage]
    assert "typesafe" in _DB_ONLY_PROVIDERS  # pyright: ignore[reportPrivateUsage]
