"""Tests for the HTTP-node SSRF guard (P0-6)."""

import re
from typing import Any

import pytest

from src.config import Settings
from src.security.ssrf import SSRFBlockedError, validate_outbound_url


def _settings(**overrides: Any) -> Settings:
    return Settings(**overrides)  # pyright: ignore[reportCallIssue]


def test_allows_public_https_url(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.security.ssrf as ssrf_mod

    monkeypatch.setattr(ssrf_mod, "_resolve_addresses", lambda host: ["8.8.8.8"])
    validate_outbound_url("https://api.example.com/v1/items", _settings())


def test_blocks_plain_http_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.security.ssrf as ssrf_mod

    monkeypatch.setattr(ssrf_mod, "_resolve_addresses", lambda host: ["8.8.8.8"])
    with pytest.raises(SSRFBlockedError, match="http"):
        validate_outbound_url("http://api.example.com/v1/items", _settings())


def test_allows_plain_http_when_explicitly_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.security.ssrf as ssrf_mod

    monkeypatch.setattr(ssrf_mod, "_resolve_addresses", lambda host: ["8.8.8.8"])
    validate_outbound_url("http://api.example.com/v1/items", _settings(http_node_allow_http=True))


def test_blocks_loopback_ip_literal() -> None:
    with pytest.raises(SSRFBlockedError, match=re.escape("127.0.0.1")):
        validate_outbound_url("https://127.0.0.1/admin", _settings())


def test_blocks_localhost_hostname_via_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.security.ssrf as ssrf_mod

    monkeypatch.setattr(ssrf_mod, "_resolve_addresses", lambda host: ["127.0.0.1"])
    with pytest.raises(SSRFBlockedError):
        validate_outbound_url("https://localhost/admin", _settings())


def test_blocks_private_ip_range(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.security.ssrf as ssrf_mod

    monkeypatch.setattr(ssrf_mod, "_resolve_addresses", lambda host: ["10.0.0.5"])
    with pytest.raises(SSRFBlockedError):
        validate_outbound_url("https://internal.corp.example/api", _settings())


def test_blocks_link_local_metadata_ip_literal() -> None:
    with pytest.raises(SSRFBlockedError, match=re.escape("169.254.169.254")):
        validate_outbound_url("https://169.254.169.254/latest/meta-data/", _settings())


def test_blocks_gcp_metadata_hostname(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.security.ssrf as ssrf_mod

    # Even if DNS somehow resolved it to something that looks public, the
    # hostname itself is blocked outright.
    monkeypatch.setattr(ssrf_mod, "_resolve_addresses", lambda host: ["8.8.8.8"])
    with pytest.raises(SSRFBlockedError, match="metadata"):
        validate_outbound_url("https://metadata.google.internal/computeMetadata/v1/", _settings())


def test_blocks_dns_rebinding_to_private_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """The hostname itself looks innocuous, but DNS resolves it to a
    private address — this is what the DNS-resolution check exists for."""
    import src.security.ssrf as ssrf_mod

    monkeypatch.setattr(ssrf_mod, "_resolve_addresses", lambda host: ["192.168.1.1"])
    with pytest.raises(SSRFBlockedError):
        validate_outbound_url("https://looks-public.example.com/", _settings())


def test_allowlisted_hostname_bypasses_private_ip_block(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.security.ssrf as ssrf_mod

    monkeypatch.setattr(ssrf_mod, "_resolve_addresses", lambda host: ["10.0.0.5"])
    validate_outbound_url(
        "https://internal.corp.example/api",
        _settings(http_node_hostname_allowlist="internal.corp.example"),
    )


def test_ssrf_protection_disabled_skips_dns_check(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.security.ssrf as ssrf_mod

    monkeypatch.setattr(ssrf_mod, "_resolve_addresses", lambda host: ["10.0.0.5"])
    validate_outbound_url(
        "https://internal.corp.example/api",
        _settings(ssrf_protection_enabled=False),
    )


def test_blocks_url_with_no_hostname() -> None:
    with pytest.raises(SSRFBlockedError):
        validate_outbound_url("file:///etc/passwd", _settings())


def test_dns_resolution_failure_blocks_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    import src.security.ssrf as ssrf_mod

    def _raise(host: str) -> list[str]:
        raise socket.gaierror("name or service not known")

    monkeypatch.setattr(ssrf_mod, "_resolve_addresses", _raise)
    with pytest.raises(SSRFBlockedError):
        validate_outbound_url("https://does-not-resolve.invalid/", _settings())
