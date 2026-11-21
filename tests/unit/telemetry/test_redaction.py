from typing import cast

import pytest

from src.telemetry.redaction import redact


@pytest.mark.parametrize(
    "value",
    [
        "Bearer abcdef",
        "postgresql://user:pass@host/db",
        "https://example.com/approval?token=secret",
        "cookie: session=secret",
        "api_key=secret",
    ],
)
def test_redacts_secrets_in_text(value: str) -> None:
    assert "secret" not in str(redact(value, max_bytes=1024))
    assert "abcdef" not in str(redact(value, max_bytes=1024))
    assert "user:pass" not in str(redact(value, max_bytes=1024))


def test_redacts_nested_credentials() -> None:
    assert redact(
        {"password": "secret", "status": "ok", "nested": [{"api_key": "secret"}]}, max_bytes=1024
    ) == {
        "password": "[REDACTED]",
        "status": "ok",
        "nested": [{"api_key": "[REDACTED]"}],
    }


def test_bounds_utf8_and_preserves_primitives() -> None:
    assert redact("ééé", max_bytes=3) == "é"
    assert redact([1, True, None], max_bytes=3) == [1, True, None]


def test_redacts_embedded_provider_keys() -> None:
    assert "sk-12345678901234567890" not in str(
        redact("error: sk-12345678901234567890", max_bytes=1024)
    )


@pytest.mark.parametrize(
    "value",
    [
        "https://user:password@example.com/path",
        "postgresql+psycopg://user:password@example.com/db",
        "Cookie: first=secret1; second=secret2",
    ],
)
def test_embedded_credentials_are_removed(value: str) -> None:
    cleaned = str(redact(value, max_bytes=1024))
    for secret in ("password", "secret1", "secret2"):
        assert secret not in cleaned


def test_secret_values_in_mapping_keys_are_removed() -> None:
    result = redact({"https://user:password@example.com": "ok"}, max_bytes=1024)
    assert "password" not in str(result)
    assert redact({"Bearer abcdef12345678901234567890": "ok"}, max_bytes=1024) == {
        "[REDACTED]": "ok"
    }


@pytest.mark.parametrize("limit", [0, 1, 3])
def test_every_generated_text_and_mapping_key_respects_byte_limit(limit: int) -> None:
    cycle: list[object] = []
    cycle.append(cycle)
    value = {"long_key": "Bearer secret", "password": b"secret", "cycle": cycle}
    result = redact(value, max_bytes=limit)

    def check(item: object) -> None:
        if isinstance(item, str):
            assert len(item.encode("utf-8")) <= limit
        elif isinstance(item, dict):
            for key, child in cast("dict[str, object]", item).items():
                check(key)
                check(child)
        elif isinstance(item, list):
            for child in cast("list[object]", item):
                check(child)

    check(result)


def test_bytes_are_never_exposed_in_keys_or_values() -> None:
    result = redact({b"secret-bytes": b"secret-value"}, max_bytes=1024)
    assert "secret-bytes" not in str(result)
    assert "secret-value" not in str(result)


def test_cyclic_container_is_replaced_and_bounded() -> None:
    cycle: list[object] = []
    cycle.append(cycle)
    assert redact(cycle, max_bytes=3) == ["[RE"]


@pytest.mark.parametrize(
    "value",
    [
        "https://example.com/?%74oken=hidden-value",
        "https://example.com/?%73ig=hidden-value",
        "https://example.com/%61pproval/hidden-value",
        "https://example.com/?signature=hidden-value",
        "postgresql+psycopg://host/db?sslmode=require",
    ],
)
def test_structural_sensitive_urls_are_removed(value: str) -> None:
    assert redact(value, max_bytes=1024) == "[REDACTED]"


def test_mapping_key_collisions_do_not_overwrite_scrubbed_values() -> None:
    assert redact({"first": "one", "fifth": "two"}, max_bytes=2) == {"fi": "[R"}
    assert redact({b"secret": "unsafe", "[REDACTED]": "ok"}, max_bytes=1024) == {"[REDACTED]": "ok"}


def test_benign_urls_text_and_nested_primitives_survive() -> None:
    assert redact(
        {"url": "https://example.com/public?count=2", "data": (1, True, None)}, max_bytes=1024
    ) == {"url": "https://example.com/public?count=2", "data": [1, True, None]}
