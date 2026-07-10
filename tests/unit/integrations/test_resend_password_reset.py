"""Tests for send_password_reset_email (self-service password reset plan)."""

from unittest.mock import AsyncMock, patch

import pytest

from src.integrations.email.resend import send_password_reset_email


@pytest.mark.asyncio
async def test_send_password_reset_email_builds_correct_payload() -> None:
    with patch(
        "src.integrations.email.resend.ResendEmailProvider.send_email",
        new_callable=AsyncMock,
    ) as mock_send:
        mock_send.return_value = {"id": "email-123"}
        await send_password_reset_email(
            to="alice@example.com",
            reset_link="https://www.flowcomposer.online/reset-password?token=abc123",
        )

    mock_send.assert_awaited_once()
    assert mock_send.await_args is not None
    payload = mock_send.await_args.args[0]
    assert payload["to"] == ["alice@example.com"]
    assert "reset" in payload["subject"].lower()
    assert "https://www.flowcomposer.online/reset-password?token=abc123" in payload["html"]


@pytest.mark.asyncio
async def test_send_password_reset_email_uses_configured_from_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RESEND_FROM_EMAIL", "noreply@script-research.online")
    from src.config import get_settings

    get_settings.cache_clear()
    with patch(
        "src.integrations.email.resend.ResendEmailProvider.send_email",
        new_callable=AsyncMock,
    ) as mock_send:
        mock_send.return_value = {"id": "email-123"}
        await send_password_reset_email(to="bob@example.com", reset_link="https://x/reset?t=1")

    assert mock_send.await_args is not None
    payload = mock_send.await_args.args[0]
    assert payload["from"] == "noreply@script-research.online"
    get_settings.cache_clear()
