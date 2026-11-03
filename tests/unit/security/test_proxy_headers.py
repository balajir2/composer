"""Tests for TrustedProxyHeadersMiddleware (audit P1-6).

Cloud Run's front end appends the real client IP as the LAST
`X-Forwarded-For` entry; anything to its left is client-supplied. uvicorn's
`--forwarded-allow-ips '*'` takes the LEFTMOST entry, so a caller could pick
the IP the app (and its login / approval-email rate limiters) saw simply by
sending the header. This middleware trusts exactly N proxy-appended entries
from the right instead.
"""

from collections.abc import MutableMapping
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from src.config import get_settings
from src.main import create_app
from src.security.proxy_headers import TrustedProxyHeadersMiddleware

Scope = MutableMapping[str, Any]

_PEER = ("169.254.8.1", 4242)


async def _receive() -> MutableMapping[str, Any]:
    return {"type": "http.request"}


async def _send(_message: MutableMapping[str, Any]) -> None:
    return None


async def _run(
    headers: list[tuple[bytes, bytes]],
    *,
    hops: int,
    scope_type: str = "http",
    peer: tuple[str, int] | None = _PEER,
) -> Scope:
    seen: list[Scope] = []

    async def app(scope: Scope, receive: Any, send: Any) -> None:
        seen.append(scope)

    middleware = TrustedProxyHeadersMiddleware(app, trusted_hops=hops)
    scope: Scope = {
        "type": scope_type,
        "headers": headers,
        "client": peer,
        "scheme": "http" if scope_type == "http" else "ws",
    }
    await middleware(scope, _receive, _send)
    assert len(seen) == 1
    return seen[0]


def _xff(value: str) -> list[tuple[bytes, bytes]]:
    return [(b"x-forwarded-for", value.encode())]


async def test_single_hop_ignores_client_supplied_leftmost_entries() -> None:
    """The spoofing case: attacker sends `X-Forwarded-For: 1.2.3.4`; Cloud
    Run appends the real address. The app must see the appended one."""
    scope = await _run(_xff("1.2.3.4, 203.0.113.7"), hops=1)

    assert scope["client"] == ("203.0.113.7", 0)


async def test_single_hop_with_only_the_proxy_appended_entry() -> None:
    scope = await _run(_xff("203.0.113.7"), hops=1)

    assert scope["client"] == ("203.0.113.7", 0)


async def test_two_hops_skips_the_inner_proxy_entry() -> None:
    """spoof, real client, inner proxy -> the entry two from the right."""
    scope = await _run(_xff("6.6.6.6, 203.0.113.7, 10.0.0.2"), hops=2)

    assert scope["client"] == ("203.0.113.7", 0)


async def test_fewer_entries_than_hops_keeps_the_peer_address() -> None:
    """Header shorter than the configured topology means it did not come
    through the proxies we trust — do not believe any of it."""
    scope = await _run(_xff("203.0.113.7"), hops=2)

    assert scope["client"] == _PEER


async def test_zero_hops_never_trusts_forwarded_for() -> None:
    scope = await _run(_xff("203.0.113.7"), hops=0)

    assert scope["client"] == _PEER


async def test_missing_header_keeps_the_peer_address() -> None:
    scope = await _run([], hops=1)

    assert scope["client"] == _PEER


async def test_non_ip_entry_is_rejected_and_peer_kept() -> None:
    scope = await _run(_xff("1.2.3.4, not-an-ip"), hops=1)

    assert scope["client"] == _PEER


async def test_ipv6_entry_is_accepted() -> None:
    scope = await _run(_xff("1.2.3.4, 2001:db8::1"), hops=1)

    assert scope["client"] == ("2001:db8::1", 0)


async def test_whitespace_around_entries_is_tolerated() -> None:
    scope = await _run(_xff("  1.2.3.4 ,   203.0.113.7  "), hops=1)

    assert scope["client"] == ("203.0.113.7", 0)


async def test_forwarded_proto_https_sets_scheme_when_trusted() -> None:
    scope = await _run([(b"x-forwarded-proto", b"https")], hops=1)

    assert scope["scheme"] == "https"


async def test_forwarded_proto_is_ignored_with_zero_hops() -> None:
    scope = await _run([(b"x-forwarded-proto", b"https")], hops=0)

    assert scope["scheme"] == "http"


async def test_forwarded_proto_garbage_is_ignored() -> None:
    scope = await _run([(b"x-forwarded-proto", b"gopher")], hops=1)

    assert scope["scheme"] == "http"


async def test_websocket_scheme_follows_forwarded_proto() -> None:
    scope = await _run([(b"x-forwarded-proto", b"https")], hops=1, scope_type="websocket")

    assert scope["scheme"] == "wss"


async def test_lifespan_scope_passes_through_untouched() -> None:
    scope = await _run(_xff("1.2.3.4, 203.0.113.7"), hops=1, scope_type="lifespan")

    assert scope["client"] == _PEER


async def test_missing_peer_does_not_crash() -> None:
    scope = await _run(_xff("1.2.3.4, 203.0.113.7"), hops=1, peer=None)

    assert scope["client"] == ("203.0.113.7", 0)


def test_negative_hops_is_rejected() -> None:
    async def app(scope: Scope, receive: Any, send: Any) -> None:
        return None

    with pytest.raises(ValueError, match="trusted_hops"):
        TrustedProxyHeadersMiddleware(app, trusted_hops=-1)


def _probe_client(monkeypatch: pytest.MonkeyPatch, hops: str | None) -> TestClient:
    if hops is None:
        monkeypatch.delenv("TRUSTED_PROXY_HOPS", raising=False)
    else:
        monkeypatch.setenv("TRUSTED_PROXY_HOPS", hops)
    get_settings.cache_clear()
    app: FastAPI = create_app()

    @app.get("/_probe/client")
    async def probe(request: Request) -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        return {"host": request.client.host if request.client else "none"}

    return TestClient(app)


def test_create_app_installs_the_middleware_with_one_hop_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End to end through the real app: the value request.client.host takes
    — which every rate limiter keys on — is the proxy-appended address."""
    client = _probe_client(monkeypatch, None)

    resp = client.get("/_probe/client", headers={"X-Forwarded-For": "9.9.9.9, 203.0.113.7"})

    assert resp.json() == {"host": "203.0.113.7"}


def test_create_app_honours_trusted_proxy_hops_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _probe_client(monkeypatch, "0")

    resp = client.get("/_probe/client", headers={"X-Forwarded-For": "9.9.9.9, 203.0.113.7"})

    assert resp.json()["host"] == "testclient"
