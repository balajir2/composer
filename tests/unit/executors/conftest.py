"""Shared fixtures for executor unit tests."""

import pytest


@pytest.fixture(autouse=True)
def _stub_dns_resolution(monkeypatch: pytest.MonkeyPatch) -> None:  # pyright: ignore[reportUnusedFunction]
    """Executor tests run through pytest-httpx's mock transport, which
    never touches the network — but the SSRF guard (src/security/ssrf.py)
    does a real DNS lookup before the HTTP node executor builds a
    request. Stub it to a genuinely public address so hermetic tests
    using placeholder hostnames (e.g. example.test) don't depend on real
    DNS or get blocked by the guard. Tests that specifically exercise
    SSRF blocking live in tests/unit/security/test_ssrf.py and patch
    this themselves (which overrides this autouse fixture within that
    test's scope).
    """
    import src.security.ssrf as ssrf_mod

    monkeypatch.setattr(ssrf_mod, "_resolve_addresses", lambda host: ["8.8.8.8"])  # pyright: ignore[reportUnknownLambdaType]
