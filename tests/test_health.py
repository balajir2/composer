"""Smoke test for the /health endpoint. Verifies FastAPI app is wired correctly."""

from fastapi.testclient import TestClient

from src.main import app


def test_health_endpoint_returns_ok() -> None:
    """The /health endpoint should return status=ok and service metadata."""
    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["service"] == "composer"
    assert "version" in payload
    assert "environment" in payload
