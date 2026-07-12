"""Tests for the approve-via-email helper."""

import base64
from pathlib import Path
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


async def test_send_approval_email_attaches_file_inside_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from src.engine.approval_email import send_approval_email

    monkeypatch.setenv("APPROVAL_ATTACHMENT_ROOT", str(tmp_path))
    from src.config import get_settings

    get_settings.cache_clear()

    attachment_file = tmp_path / "brd.pdf"
    file_bytes = b"%PDF-1.4 fake pdf content for testing"
    attachment_file.write_bytes(file_bytes)

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
        approver_cc=None,
        pending_since="2026-07-11T10:00:00+00:00",
        attachment_path=str(attachment_file),
    )

    attachments = captured["attachments"]
    assert isinstance(attachments, list)
    assert len(attachments) == 1
    assert attachments[0]["filename"] == "brd.pdf"
    assert base64.b64decode(attachments[0]["content"]) == file_bytes

    get_settings.cache_clear()


async def test_send_approval_email_skips_attachment_outside_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from src.engine.approval_email import send_approval_email

    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside_file = outside_dir / "secret.txt"
    outside_file.write_bytes(b"top secret")

    monkeypatch.setenv("APPROVAL_ATTACHMENT_ROOT", str(allowed_root))
    from src.config import get_settings

    get_settings.cache_clear()

    captured: dict[str, object] = {}
    send_mock = AsyncMock(return_value={"id": "email-1"})

    async def fake_send_email(
        self: object, payload: dict[str, object], **kw: object
    ) -> dict[str, object]:
        captured.update(payload)
        return await send_mock(payload)

    monkeypatch.setattr("src.engine.approval_email.ResendEmailProvider.send_email", fake_send_email)

    await send_approval_email(
        execution_id="exec-1",
        node_id="approval-1",
        prompt="Approve?",
        approver_email="reviewer@example.com",
        approver_cc=None,
        pending_since="2026-07-11T10:00:00+00:00",
        attachment_path=str(outside_file),
    )

    send_mock.assert_awaited_once()
    assert "attachments" not in captured

    get_settings.cache_clear()


async def test_send_approval_email_skips_attachment_when_file_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from src.engine.approval_email import send_approval_email

    monkeypatch.setenv("APPROVAL_ATTACHMENT_ROOT", str(tmp_path))
    from src.config import get_settings

    get_settings.cache_clear()

    missing_file = tmp_path / "does-not-exist.pdf"

    captured: dict[str, object] = {}
    send_mock = AsyncMock(return_value={"id": "email-1"})

    async def fake_send_email(
        self: object, payload: dict[str, object], **kw: object
    ) -> dict[str, object]:
        captured.update(payload)
        return await send_mock(payload)

    monkeypatch.setattr("src.engine.approval_email.ResendEmailProvider.send_email", fake_send_email)

    await send_approval_email(
        execution_id="exec-1",
        node_id="approval-1",
        prompt="Approve?",
        approver_email="reviewer@example.com",
        approver_cc=None,
        pending_since="2026-07-11T10:00:00+00:00",
        attachment_path=str(missing_file),
    )

    send_mock.assert_awaited_once()
    assert "attachments" not in captured

    get_settings.cache_clear()


async def test_send_approval_email_skips_attachment_over_max_bytes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from src.engine.approval_email import send_approval_email

    monkeypatch.setenv("APPROVAL_ATTACHMENT_ROOT", str(tmp_path))
    monkeypatch.setenv("APPROVAL_ATTACHMENT_MAX_BYTES", "10")
    from src.config import get_settings

    get_settings.cache_clear()

    big_file = tmp_path / "too-big.pdf"
    big_file.write_bytes(b"x" * 11)

    captured: dict[str, object] = {}
    send_mock = AsyncMock(return_value={"id": "email-1"})

    async def fake_send_email(
        self: object, payload: dict[str, object], **kw: object
    ) -> dict[str, object]:
        captured.update(payload)
        return await send_mock(payload)

    monkeypatch.setattr("src.engine.approval_email.ResendEmailProvider.send_email", fake_send_email)

    await send_approval_email(
        execution_id="exec-1",
        node_id="approval-1",
        prompt="Approve?",
        approver_email="reviewer@example.com",
        approver_cc=None,
        pending_since="2026-07-11T10:00:00+00:00",
        attachment_path=str(big_file),
    )

    send_mock.assert_awaited_once()
    assert "attachments" not in captured

    get_settings.cache_clear()


async def test_send_approval_email_no_attachment_path_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existing no-attachment-path behavior (attachment_path not passed) is
    completely unchanged -- no `attachments` key ends up in the payload."""
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
        approver_cc=None,
        pending_since="2026-07-11T10:00:00+00:00",
    )

    assert "attachments" not in captured
