"""Tests for the email executor."""

from typing import Any

import pytest
from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]

from src.engine.state import initial_state
from src.engine.workflow import EmailNode
from src.executors.email import EmailExecutor, EmailNodeError
from src.integrations.email.resend import RESEND_API_BASE


@pytest.fixture(autouse=True)
def _stub_api_key(monkeypatch: pytest.MonkeyPatch) -> None:  # pyright: ignore[reportUnusedFunction]
    from src.config import get_settings

    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    get_settings.cache_clear()


def _node(**overrides: Any) -> EmailNode:
    data: dict[str, Any] = {
        "label": "Email",
        "emailFrom": "Composer <reports@example.com>",
        "emailTo": "user@example.com",
        "emailSubject": "Report",
        "emailBody": "<p>Hello</p>",
        "emailBodyType": "html",
    }
    data.update(overrides)
    return EmailNode.model_validate(
        {
            "id": "email-1",
            "type": "email",
            "position": {"x": 0, "y": 0},
            "data": data,
        }
    )


async def test_email_sends_html_via_resend(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(
        method="POST",
        url=f"{RESEND_API_BASE}/emails",
        json={"id": "email_123"},
    )

    delta = await EmailExecutor(_node()).arun(initial_state())

    assert delta["variables"]["lastOutput"]["messageId"] == "email_123"
    assert delta["variables"]["lastOutput"]["status"] == "sent"
    req = httpx_mock.get_request()  # pyright: ignore[reportUnknownMemberType]
    assert req is not None
    assert req.headers["authorization"] == "Bearer re_test"
    assert b'"html":"<p>Hello</p>"' in req.content


async def test_email_substitutes_fields_and_sets_idempotency_key(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    httpx_mock.add_response(
        method="POST",
        url=f"{RESEND_API_BASE}/emails",
        json={"id": "email_456"},
    )
    state = initial_state()
    state["variables"].update(
        {
            "recipient": "alice@example.com",
            "name": "Alice",
            "execution_id": "exec_1",
        }
    )

    node = _node(
        emailTo="{{recipient}}",
        emailSubject="Report for {{name}}",
        emailBody="Hi {{name}}",
        emailBodyType="text",
        emailIdempotencyKey="workflow-{{execution_id}}",
    )
    await EmailExecutor(node).arun(state)

    req = httpx_mock.get_request()  # pyright: ignore[reportUnknownMemberType]
    assert req is not None
    assert req.headers["idempotency-key"] == "workflow-exec_1"
    assert b'"to":["alice@example.com"]' in req.content
    assert b'"subject":"Report for Alice"' in req.content
    assert b'"text":"Hi Alice"' in req.content


async def test_email_missing_api_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.config import get_settings

    monkeypatch.setenv("RESEND_API_KEY", "")
    get_settings.cache_clear()

    with pytest.raises(EmailNodeError, match="RESEND_API_KEY"):
        await EmailExecutor(_node()).arun(initial_state())


async def test_email_invalid_recipient_raises() -> None:
    with pytest.raises(EmailNodeError, match="invalid email"):
        await EmailExecutor(_node(emailTo="not-an-email")).arun(initial_state())


async def test_email_resend_error_raises(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(
        method="POST",
        url=f"{RESEND_API_BASE}/emails",
        status_code=403,
        text="domain not verified",
    )

    with pytest.raises(EmailNodeError, match="domain not verified"):
        await EmailExecutor(_node()).arun(initial_state())


async def test_executor_is_registered() -> None:
    import src.executors.email  # noqa: F401  # pyright: ignore[reportUnusedImport]
    from src.executors.base import build_executor

    executor = build_executor(_node())
    assert isinstance(executor, EmailExecutor)
