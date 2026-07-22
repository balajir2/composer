"""Tests for /expressions test/preview endpoints."""

from fastapi.testclient import TestClient

from src.main import create_app
from src.security.rate_limit import RateLimiter


def _client() -> TestClient:
    app = create_app()
    app.state.rate_limiter = RateLimiter()
    return TestClient(app)


def test_evaluate_transform_success() -> None:
    client = _client()
    resp = client.post(
        "/expressions/evaluate-transform",
        json={"expression": "lastOutput.upper()", "variables": {"lastOutput": "hi"}},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"ok": True, "result": "HI", "error": None}


def test_evaluate_transform_undefined_name_returns_ok_false() -> None:
    client = _client()
    resp = client.post(
        "/expressions/evaluate-transform",
        json={"expression": "not_a_real_name + 1", "variables": {}},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert "not_a_real_name" in body["error"]


def test_evaluate_transform_reads_top_level_variable() -> None:
    client = _client()
    resp = client.post(
        "/expressions/evaluate-transform",
        json={"expression": "counter + 1", "variables": {"counter": 41}},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True, "result": 42, "error": None}


def test_evaluate_transform_defaults_variables_to_empty_dict() -> None:
    client = _client()
    resp = client.post("/expressions/evaluate-transform", json={"expression": "1 + 1"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True, "result": 2, "error": None}
