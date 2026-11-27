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


def _unverified_body(domain: str = "flowcomposer.com") -> dict[str, Any]:
    return {
        "message": f"The {domain} domain is not verified. Please, add and verify your domain.",
        "name": "validation_error",
        "statusCode": 403,
    }


async def _sent_payload(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
    node: EmailNode,
    state: Any = None,
) -> dict[str, Any]:
    import json

    httpx_mock.add_response(method="POST", url=f"{RESEND_API_BASE}/emails", json={"id": "e1"})
    await EmailExecutor(node).arun(state or initial_state())
    req = httpx_mock.get_request()  # pyright: ignore[reportUnknownMemberType]
    assert req is not None
    return json.loads(req.content)


@pytest.mark.parametrize("blank", ["", "   ", " \n\t "])
async def test_blank_from_uses_platform_sender(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
    monkeypatch: pytest.MonkeyPatch,
    blank: str,
) -> None:
    from src.config import get_settings

    monkeypatch.setenv("RESEND_FROM_EMAIL", "platform@verified.example")
    get_settings.cache_clear()

    payload = await _sent_payload(httpx_mock, _node(emailFrom=blank))

    assert payload["from"] == "platform@verified.example"


async def test_from_that_substitutes_to_blank_uses_platform_sender(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.config import get_settings

    monkeypatch.setenv("RESEND_FROM_EMAIL", "platform@verified.example")
    get_settings.cache_clear()

    state = initial_state()
    state["variables"]["sender"] = "  "
    payload = await _sent_payload(httpx_mock, _node(emailFrom="{{sender}}"), state)

    assert payload["from"] == "platform@verified.example"


async def test_explicit_from_is_kept(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.config import get_settings

    monkeypatch.setenv("RESEND_FROM_EMAIL", "platform@verified.example")
    get_settings.cache_clear()

    payload = await _sent_payload(httpx_mock, _node())

    assert payload["from"] == "Composer <reports@example.com>"


async def test_invalid_explicit_from_still_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.config import get_settings

    monkeypatch.setenv("RESEND_FROM_EMAIL", "platform@verified.example")
    get_settings.cache_clear()

    with pytest.raises(EmailNodeError, match="invalid from address"):
        await EmailExecutor(_node(emailFrom="not-an-address")).arun(initial_state())


async def test_blank_from_and_blank_setting_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.config import get_settings

    monkeypatch.setenv("RESEND_FROM_EMAIL", "")
    get_settings.cache_clear()

    with pytest.raises(EmailNodeError, match="no from address"):
        await EmailExecutor(_node(emailFrom="  ")).arun(initial_state())


async def test_unverified_domain_gives_actionable_error(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    httpx_mock.add_response(
        method="POST",
        url=f"{RESEND_API_BASE}/emails",
        status_code=403,
        json=_unverified_body(),
    )

    with pytest.raises(EmailNodeError) as excinfo:
        await EmailExecutor(_node(emailFrom="Team <hello@flowcomposer.com>")).arun(initial_state())

    message = str(excinfo.value)
    assert "'email-1'" in message
    assert "flowcomposer.com" in message
    assert "403" in message
    assert "Domains" in message
    assert "clear" in message.lower()
    assert "re_test" not in message
    assert "Bearer" not in message
    assert "Authorization" not in message


async def test_unverified_domain_taken_from_error_not_from_address(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    httpx_mock.add_response(
        method="POST",
        url=f"{RESEND_API_BASE}/emails",
        status_code=403,
        json=_unverified_body("other.example"),
    )

    with pytest.raises(EmailNodeError, match=r"other.example"):
        await EmailExecutor(_node()).arun(initial_state())


async def test_other_4xx_keeps_original_message(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    httpx_mock.add_response(
        method="POST",
        url=f"{RESEND_API_BASE}/emails",
        status_code=422,
        json={"message": "Invalid `to` field.", "name": "validation_error", "statusCode": 422},
    )

    with pytest.raises(EmailNodeError) as excinfo:
        await EmailExecutor(_node()).arun(initial_state())

    assert "Resend API error 422" in str(excinfo.value)
    assert "Invalid `to` field." in str(excinfo.value)
    assert "Domains" not in str(excinfo.value)


async def test_executor_is_registered() -> None:
    import src.executors.email  # noqa: F401  # pyright: ignore[reportUnusedImport]
    from src.executors.base import build_executor

    executor = build_executor(_node())
    assert isinstance(executor, EmailExecutor)
