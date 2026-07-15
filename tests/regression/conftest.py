"""Regression test fixtures.

Shared fixtures (`client`, `app`, `_require_test_database_url`) are defined
in the top-level `tests/conftest.py` and automatically available here.
"""

import pytest
import pytest_asyncio
from fastapi import FastAPI


@pytest.fixture(autouse=True)
def _stub_dns_resolution(monkeypatch: pytest.MonkeyPatch) -> None:  # pyright: ignore[reportUnusedFunction]
    """Mirrors tests/unit/executors/conftest.py's fixture of the same name.

    The OAB HTTP-node regression tests use httpx_mock, which never touches
    the network — but the SSRF guard (src/security/ssrf.py) does a real DNS
    lookup before the executor builds a request, and these tests use
    placeholder hostnames (api.example.test) that don't resolve. Without
    this stub the SSRF guard blocks the request before httpx_mock ever gets
    a chance to intercept it.
    """
    import src.security.ssrf as ssrf_mod

    monkeypatch.setattr(ssrf_mod, "_resolve_addresses", lambda host: ["8.8.8.8"])  # pyright: ignore[reportUnknownLambdaType]


@pytest_asyncio.fixture(autouse=True)
async def _drive_cloud_tasks_synchronously(  # pyright: ignore[reportUnusedFunction]
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same rationale as tests/integration/conftest.py's fixture of the same
    name — these regression tests predate the Cloud Tasks migration
    (ADR-0033) and assume POST /executions completes synchronously. See that
    file's docstring for the full explanation.
    """
    from httpx import ASGITransport, AsyncClient

    async def _fake_enqueue(execution_id: str, *, kind: str) -> None:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://internal") as c:
            resp = await c.post(
                "/internal/claim-and-run",
                json={"executionId": execution_id, "kind": kind},
            )
            resp.raise_for_status()

    monkeypatch.setattr("src.api.executions.enqueue_execution", _fake_enqueue)
    monkeypatch.setattr("src.api.approval_email.enqueue_execution", _fake_enqueue)
    monkeypatch.setattr("src.api.run.enqueue_execution", _fake_enqueue)
    # See tests/integration/conftest.py's identical fixture for why
    # src.maintenance.execution_sweeper is deliberately not patched here.
