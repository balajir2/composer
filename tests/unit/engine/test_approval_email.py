"""Tests for the approve-via-email helper."""

from unittest.mock import AsyncMock

import pytest

from src.integrations.email.resend import ResendEmailProviderError


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
        pending_since="2026-07-11T10:00:00+00:00",
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
        pending_since="2026-07-11T10:00:00+00:00",
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
        pending_since="2026-07-11T10:00:00+00:00",
    )
    assert "cc" not in captured


async def test_send_approval_email_logs_and_swallows_resend_error(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from src.engine.approval_email import send_approval_email

    async def failing_send_email(
        self: object, payload: dict[str, object], **kw: object
    ) -> dict[str, object]:
        raise ResendEmailProviderError("Resend API error 500: boom")

    monkeypatch.setattr(
        "src.engine.approval_email.ResendEmailProvider.send_email", failing_send_email
    )

    with caplog.at_level("ERROR", logger="src.engine.approval_email"):
        await send_approval_email(
            execution_id="exec-1",
            node_id="approval-1",
            prompt="Approve?",
            approver_email="reviewer@example.com",
            approver_cc=None,
            pending_since="2026-07-11T10:00:00+00:00",
        )

    assert any(
        record.levelname == "ERROR"
        and "exec-1" in record.message
        and "approval-1" in record.message
        for record in caplog.records
    )


async def test_send_approval_email_escapes_html_in_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
        prompt="<script>alert(1)</script><b>bold</b>",
        approver_email="reviewer@example.com",
        approver_cc=None,
        pending_since="2026-07-11T10:00:00+00:00",
    )

    html = captured["html"]
    assert isinstance(html, str)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&lt;b&gt;bold&lt;/b&gt;" in html
