"""SSRF guard for the HTTP node.

The HTTP node accepts a variable-substituted URL and fires the request
server-side with no restriction, so a workflow author (or anything that
can inject a value reaching {{...}} substitution) could otherwise target
loopback, private-network, or cloud-metadata endpoints reachable from the
backend. See P0-6 in docs/claude-improvement-backlog.md.

`_resolve_addresses` is a module-level function (not inlined) so tests
can monkeypatch it — real DNS resolution has no place in a hermetic unit
test, and pytest-httpx-mocked HTTP tests never touch the network either.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from src.config import Settings

# Hostnames that resolve (or are documented to resolve) to a cloud
# metadata service. Blocked outright regardless of what they resolve to,
# since IP-literal/DNS-resolution checks alone wouldn't catch a metadata
# proxy that responds on a non-link-local address.
_METADATA_HOSTNAMES = frozenset(
    {
        "metadata.google.internal",
        "metadata.internal",
        "metadata",
        "instance-data",
        "instance-data.ec2.internal",
    }
)


class SSRFBlockedError(RuntimeError):
    """Raised when a URL is not safe for the backend to fetch server-side."""


def _resolve_addresses(hostname: str) -> list[str]:
    """Resolve `hostname` to its IP addresses. Isolated for test patching."""
    infos = socket.getaddrinfo(hostname, None)
    # sockaddr is (host, port) for AF_INET or (host, port, flowinfo, scopeid)
    # for AF_INET6 — first element is always the address string in both.
    return sorted({str(info[4][0]) for info in infos})


def _is_blocked_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # unparseable -> fail closed
    return (
        ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_unspecified
        or ip.is_private
        or ip.is_reserved
    )


def _allowlist(settings: Settings) -> set[str]:
    return {
        h.strip().lower() for h in settings.http_node_hostname_allowlist.split(",") if h.strip()
    }


def validate_outbound_url(url: str, settings: Settings) -> None:
    """Raise SSRFBlockedError if `url` is not safe for the HTTP node to fetch.

    Check order: hostname presence -> admin allowlist (bypasses everything
    below) -> scheme -> metadata hostname -> IP-literal -> DNS-resolved
    addresses (skipped if ssrf_protection_enabled is False).
    """
    parts = urlsplit(url)
    hostname = (parts.hostname or "").lower()
    if not hostname:
        raise SSRFBlockedError(f"URL has no hostname: {url!r}")

    if hostname in _allowlist(settings):
        return

    if parts.scheme not in ("https", "http"):
        raise SSRFBlockedError(f"Unsupported URL scheme {parts.scheme!r}; use https://")
    if parts.scheme == "http" and not settings.http_node_allow_http:
        raise SSRFBlockedError(
            "Plain http:// is blocked by default — use https://, set "
            "HTTP_NODE_ALLOW_HTTP=true, or add this host to "
            "HTTP_NODE_HOSTNAME_ALLOWLIST."
        )

    if hostname in _METADATA_HOSTNAMES:
        raise SSRFBlockedError(f"Blocked cloud metadata hostname: {hostname!r}")

    try:
        literal_ip = ipaddress.ip_address(hostname)
    except ValueError:
        literal_ip = None
    if literal_ip is not None and _is_blocked_ip(str(literal_ip)):
        raise SSRFBlockedError(f"Blocked IP address: {hostname!r}")

    if not settings.ssrf_protection_enabled:
        return

    try:
        resolved = _resolve_addresses(hostname)
    except OSError as exc:
        # Fail closed: an unresolvable host can't be verified safe, and a
        # workflow's HTTP node failing loudly here is far better than a
        # DNS hiccup silently becoming a bypass.
        raise SSRFBlockedError(f"DNS resolution failed for {hostname!r}: {exc}") from exc
    for ip_str in resolved:
        if _is_blocked_ip(ip_str):
            raise SSRFBlockedError(f"{hostname!r} resolves to a blocked address ({ip_str})")


__all__ = ["SSRFBlockedError", "validate_outbound_url"]
