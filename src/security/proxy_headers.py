"""Trusted-proxy handling for X-Forwarded-* headers (audit P1-6).

uvicorn's built-in `--proxy-headers --forwarded-allow-ips '*'` picks the
LEFTMOST `X-Forwarded-For` entry. That entry is whatever the caller sent, so
it let any client choose the address that `request.client.host` — the key
for the login / registration / approval-email rate limiters — reported.

Proxies APPEND to `X-Forwarded-For`, so only entries counted from the right
are trustworthy. This middleware trusts exactly `trusted_hops` of them: with
Cloud Run's front end directly in front of the container (this project's
topology — the domain is mapped straight to the service, no load balancer)
that is 1, and the client address is the last entry. If the header has
fewer entries than that, or the chosen entry is not an IP address, the
header did not come through the proxies we trust and the TCP peer address is
left alone.

`X-Forwarded-Proto` is honoured under the same trust switch (hops > 0) so
redirects and websocket URLs keep their https/wss scheme behind TLS
termination; only the values `http`, `https`, `ws`, `wss` are accepted.
"""

from __future__ import annotations

import ipaddress
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, MutableMapping
    from typing import Any

    Scope = MutableMapping[str, Any]
    Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
    Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]
    ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

_ALLOWED_SCHEMES = frozenset({"http", "https", "ws", "wss"})


def _trusted_client_host(forwarded_for: str, hops: int) -> str | None:
    entries = [part.strip() for part in forwarded_for.split(",")]
    if len(entries) < hops:
        return None
    candidate = entries[-hops]
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return None
    return candidate


class TrustedProxyHeadersMiddleware:
    """Set `scope["client"]` / `scope["scheme"]` from proxy-appended headers."""

    def __init__(self, app: ASGIApp, *, trusted_hops: int) -> None:
        if trusted_hops < 0:
            raise ValueError("trusted_hops must be >= 0")
        self.app = app
        self.trusted_hops = trusted_hops

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self.trusted_hops > 0 and scope["type"] in ("http", "websocket"):
            headers = dict(scope["headers"])

            forwarded_for = headers.get(b"x-forwarded-for")
            if forwarded_for is not None:
                host = _trusted_client_host(forwarded_for.decode("latin1"), self.trusted_hops)
                if host is not None:
                    # The peer's port is lost once we take the forwarded
                    # address, matching uvicorn's own behavior.
                    scope["client"] = (host, 0)

            forwarded_proto = headers.get(b"x-forwarded-proto")
            if forwarded_proto is not None:
                proto = forwarded_proto.decode("latin1").strip()
                if proto in _ALLOWED_SCHEMES:
                    scope["scheme"] = (
                        proto.replace("http", "ws") if scope["type"] == "websocket" else proto
                    )

        await self.app(scope, receive, send)


__all__ = ["TrustedProxyHeadersMiddleware"]
