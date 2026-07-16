"""Integration test fixtures.

Fixtures are defined in the top-level `tests/conftest.py` so that both
`tests/integration/` and `tests/regression/` can share them without
requiring `pytest_plugins` in sub-level conftest files (deprecated in
recent pytest). This file adds directory-scoped fixtures specific to
`tests/integration/`.
"""

import pytest
import pytest_asyncio
from fastapi import FastAPI

from tests._rate_limit_reset import reset_rate_limit_buckets


@pytest_asyncio.fixture(autouse=True)
async def _reset_rate_limit_buckets() -> None:  # pyright: ignore[reportUnusedFunction]
    """Clear rate_limit_buckets before each test. See tests/_rate_limit_reset.py."""
    await reset_rate_limit_buckets()


@pytest_asyncio.fixture(autouse=True)
async def _drive_cloud_tasks_synchronously(  # pyright: ignore[reportUnusedFunction]
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Make POST /executions (and resume/external-invoke) complete
    synchronously in tests, matching the pre-ADR-0033 behavior these tests
    were written against.

    Production drives execution via Cloud Tasks: enqueue_execution() enqueues
    a task, and Cloud Tasks later delivers an OIDC-authenticated HTTP push to
    POST /internal/claim-and-run, which is what actually runs the graph. CI
    and local test runs have neither a publicly reachable URL for Cloud Tasks
    to push to nor real GCP Application Default Credentials, so the real
    enqueue_execution() fails outright (DefaultCredentialsError) or, even if
    it somehow succeeded, nothing would ever call claim-and-run back.

    This fixture replaces enqueue_execution, at every call site that imports
    it, with a same-process ASGI call to /internal/claim-and-run on the same
    `app` instance — the same Prisma client, the same DB, the same
    SELECT ... FOR UPDATE SKIP LOCKED claim guarantee — so the large existing
    suite of tests that predate the Cloud Tasks migration and assume
    synchronous completion keeps working without needing real GCP infra or
    a rewrite. `_verify_internal_oidc` (src/api/internal.py) already no-ops
    when CLOUD_TASKS_SERVICE_ACCOUNT is unset, which it is in this test
    environment, so no OIDC token needs to be minted here.
    """
    from httpx import ASGITransport, AsyncClient

    async def _fake_enqueue(execution_id: str, *, kind: str, db: object = None) -> None:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://internal") as c:
            resp = await c.post(
                "/internal/claim-and-run",
                json={"executionId": execution_id, "kind": kind},
            )
            resp.raise_for_status()

    monkeypatch.setattr("src.api.executions.enqueue_execution", _fake_enqueue)
    monkeypatch.setattr("src.api.approval_email.enqueue_execution", _fake_enqueue)
    monkeypatch.setattr("src.api.run.enqueue_execution", _fake_enqueue)
    # src/maintenance/execution_sweeper.py imports enqueue_execution locally
    # inside sweep_expired_leases (a deferred import, not a module-level
    # binding), so there's no src.maintenance.execution_sweeper.enqueue_execution
    # attribute to patch here. No currently-failing test exercises that path
    # anyway — the sweeper's own unit tests already mock
    # src.execution.cloud_tasks.enqueue_execution directly at its real import
    # site when needed (see tests/unit/maintenance/test_execution_sweeper.py).
