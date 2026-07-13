"""Regression test fixtures.

Shared fixtures (`client`, `app`, `_require_test_database_url`) are defined
in the top-level `tests/conftest.py` and automatically available here.
"""

import pytest


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
