"""Regression tests: /internal/* authentication fails closed (audit P0-1).

`_verify_internal_oidc` used to `return` without authenticating whenever
CLOUD_TASKS_SERVICE_ACCOUNT was empty, in *any* environment — so a
production deployment whose service-account variable was missing or
accidentally cleared exposed claim-and-run, sweep and poll-file-triggers
to unauthenticated callers. The bypass is now allowed only in explicit
local development (`environment == "development"`).
"""

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.config import get_settings
from src.main import create_app

_SERVICE_ACCOUNT = "scheduler@test-project.iam.gserviceaccount.com"

# (path, json body) for every route guarded by _verify_internal_oidc.
_INTERNAL_ROUTES = [
    ("/internal/claim-and-run", {"executionId": "exec-1", "kind": "run"}),
    ("/internal/sweep", None),
    ("/internal/poll-file-triggers", None),
]


def _client() -> TestClient:
    # Call only after monkeypatch.setenv(): get_settings() is lru_cached and
    # `src.main` builds an app at import time, so the first test in a
    # process would otherwise see stale development settings.
    get_settings.cache_clear()
    app = create_app()
    app.state.db = MagicMock()
    app.state.checkpointer = MagicMock()
    app.state.event_bus = MagicMock()
    return TestClient(app)


@pytest.mark.parametrize("environment", ["production", "staging", "prod", ""])
@pytest.mark.parametrize(("path", "body"), _INTERNAL_ROUTES)
def test_internal_route_rejects_when_service_account_unset_outside_development(
    monkeypatch: pytest.MonkeyPatch,
    environment: str,
    path: str,
    body: dict[str, str] | None,
) -> None:
    """No service account + anything other than explicit development must
    be a 401 — including unrecognised environment names, so a typo cannot
    silently re-open the bypass."""
    monkeypatch.setenv("ENVIRONMENT", environment)
    monkeypatch.setenv("CLOUD_TASKS_SERVICE_ACCOUNT", "")

    resp = _client().post(path, json=body)

    assert resp.status_code == 401, resp.text


@pytest.mark.parametrize(("path", "body"), _INTERNAL_ROUTES)
def test_internal_route_rejects_unauthenticated_call_in_production_even_with_header_junk(
    monkeypatch: pytest.MonkeyPatch, path: str, body: dict[str, str] | None
) -> None:
    """An Authorization header must not make the empty-config path pass."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("CLOUD_TASKS_SERVICE_ACCOUNT", "")

    resp = _client().post(path, json=body, headers={"Authorization": "Bearer anything"})

    assert resp.status_code == 401, resp.text


def test_internal_route_rejects_token_with_wrong_audience(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The audience handed to Google's verifier is derived from this
    request's own path; a token minted for a different audience is
    rejected by the verifier (ValueError) and must surface as a 401."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("BACKEND_PUBLIC_URL", "https://api.example.com")
    monkeypatch.setenv("CLOUD_TASKS_SERVICE_ACCOUNT", _SERVICE_ACCOUNT)

    seen_audiences: list[str] = []

    def _verify(_token: str, _request: object, audience: str) -> dict[str, str]:
        seen_audiences.append(audience)
        raise ValueError("Token has wrong audience https://evil.example.com")

    monkeypatch.setattr("src.api.internal.id_token.verify_oauth2_token", _verify)

    resp = _client().post("/internal/sweep", headers={"Authorization": "Bearer some-token"})

    assert resp.status_code == 401
    assert seen_audiences == ["https://api.example.com/internal/sweep"]


def test_internal_route_still_skips_oidc_in_explicit_development(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Local dev/CI keep working: development + no service account means
    the OIDC check is skipped (the request proceeds past the dependency)."""
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("CLOUD_TASKS_SERVICE_ACCOUNT", "")

    async def _noop_sweep(*_args: object, **_kwargs: object) -> object:
        from src.maintenance.execution_sweeper import SweepResult

        return SweepResult(scanned=0, marked_failed=0)

    async def _noop_events(*_args: object, **_kwargs: object) -> int:
        return 0

    monkeypatch.setattr("src.api.internal.sweep_stuck_executions", _noop_sweep)
    monkeypatch.setattr("src.api.internal.sweep_expired_approvals", _noop_sweep)
    monkeypatch.setattr("src.api.internal.sweep_expired_leases", _noop_sweep)
    monkeypatch.setattr("src.api.internal.sweep_old_execution_events", _noop_events)

    resp = _client().post("/internal/sweep")

    assert resp.status_code == 200, resp.text
