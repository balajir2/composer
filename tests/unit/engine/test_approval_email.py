"""Tests for the approve-via-email helper."""

from unittest.mock import AsyncMock

import pytest


async def test_send_approval_email_noop_when_no_approver(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.engine.approval_email import send_approval_email

    send_mock = AsyncMock()
    monkeypatch.setattr("src.engine.approval_email.ResendEmailProvider.send_email", send_mock)
    await send_approval_email(
        execution_id="exec-1",
        node_id="approval-1",
        prompt="Approve?",
        approver_email="",
        approver_cc=None,
    )
    send_mock.assert_not_awaited()


async def test_send_approval_email_sends_with_both_links(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.engine.approval_email import send_approval_email

    monkeypatch.setenv("BACKEND_PUBLIC_URL", "https://api.example.com")
    monkeypatch.setenv("RESEND_FROM_EMAIL", "noreply@example.com")
    from src.config import get_settings

    get_settings.cache_clear()

    captured: dict[str, object] = {}

    async def fake_send_email(
        self: object, payload: dict[str, object], **kw: object
    ) -> dict[str, object]:
        captured.update(payload)
        return {"id": "email-1"}

    monkeypatch.setattr("src.engine.approval_email.ResendEmailProvider.send_email", fake_send_email)

    await send_approval_email(
        execution_id="exec-1",
        node_id="approval-1",
        prompt="Approve the BRD?",
        approver_email="reviewer@example.com",
        approver_cc="manager@example.com",
    )

    assert captured["to"] == ["reviewer@example.com"]
    assert captured["cc"] == ["manager@example.com"]
    html = captured["html"]
    assert isinstance(html, str)
    assert "Approve the BRD?" in html
    assert "https://api.example.com/approvals/email/" in html
    # Two distinct links (approve + reject) must both be present.
    assert html.count("https://api.example.com/approvals/email/") == 2

    get_settings.cache_clear()


async def test_send_approval_email_omits_cc_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.engine.approval_email import send_approval_email

    captured: dict[str, object] = {}

    async def fake_send_email(
        self: object, payload: dict[str, object], **kw: object
    ) -> dict[str, object]:
        captured.update(payload)
        return {"id": "email-1"}

    monkeypatch.setattr("src.engine.approval_email.ResendEmailProvider.send_email", fake_send_email)

    await send_approval_email(
        execution_id="exec-1",
        node_id="approval-1",
        prompt="Approve?",
        approver_email="reviewer@example.com",
        approver_cc="",
    )
    assert "cc" not in captured
