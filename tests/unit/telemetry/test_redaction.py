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
