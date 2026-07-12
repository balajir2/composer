"""Tests for the arcade executor (mocked Arcade API via pytest-httpx)."""

from typing import Any

import pytest
from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]

from src.engine.state import initial_state
from src.engine.workflow import ArcadeNode
from src.executors.arcade import (
    ARCADE_API_BASE,
    MAX_RETRIES,
    ArcadeAuthError,
    ArcadeExecutor,
    ArcadeNodeError,
    ArcadeUserCanceledError,
)


@pytest.fixture(autouse=True)
def _stub_api_key(monkeypatch: pytest.MonkeyPatch) -> None:  # pyright: ignore[reportUnusedFunction]
    from src.config import get_settings

    monkeypatch.setenv("ARCADE_API_KEY", "test-arcade-key")
    get_settings.cache_clear()


def _node(**data_overrides: Any) -> ArcadeNode:
    data: dict[str, Any] = {
        "label": "AR",
        "arcadeTool": "TestTool@1.0.0",
        "arcadeInput": {},
    }
    data.update(data_overrides)
    return ArcadeNode.model_validate(
        {
            "id": "ar",
            "type": "arcade",
            "position": {"x": 0, "y": 0},
            "data": data,
        }
    )


async def test_auth_completed_executes_and_returns_output(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(
        method="POST",
        url=f"{ARCADE_API_BASE}/tools/authorize",
        json={"id": "auth1", "status": "completed"},
        status_code=200,
    )
    httpx_mock.add_response(
        method="POST",
        url=f"{ARCADE_API_BASE}/tools/execute",
        json={"output": {"value": "hello"}},
        status_code=200,
    )
    delta = await ArcadeExecutor(_node()).arun(initial_state())
    assert delta["variables"]["lastOutput"] == "hello"


async def test_output_plain_dict(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(
        method="POST",
        url=f"{ARCADE_API_BASE}/tools/authorize",
        json={"id": "auth1", "status": "completed"},
        status_code=200,
    )
    httpx_mock.add_response(
        method="POST",
        url=f"{ARCADE_API_BASE}/tools/execute",
        json={"output": {"foo": "bar"}},
        status_code=200,
    )
    delta = await ArcadeExecutor(_node()).arun(initial_state())
    assert delta["variables"]["lastOutput"] == {"foo": "bar"}


async def test_output_fallback_to_result(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    """If 'output' is None / missing, fall back to the whole result dict."""
    httpx_mock.add_response(
        method="POST",
        url=f"{ARCADE_API_BASE}/tools/authorize",
        json={"id": "auth1", "status": "completed"},
        status_code=200,
    )
    httpx_mock.add_response(
        method="POST",
        url=f"{ARCADE_API_BASE}/tools/execute",
        json={"result": "only-result"},
        status_code=200,
    )
    delta = await ArcadeExecutor(_node()).arun(initial_state())
    assert delta["variables"]["lastOutput"] == {"result": "only-result"}


async def test_auth_pending_calls_interrupt(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    """First-pass auth pending → interrupt() is called with correct payload,
    retry counter incremented in state before interrupt."""
    from langgraph.errors import GraphInterrupt

    import src.executors.arcade as ar_mod

    captured: list[dict[str, Any]] = []

    def _fake_interrupt(payload: Any) -> Any:
        captured.append(payload)
        raise GraphInterrupt(payload)

    monkeypatch.setattr(ar_mod, "interrupt", _fake_interrupt)

    httpx_mock.add_response(
        method="POST",
        url=f"{ARCADE_API_BASE}/tools/authorize",
        json={
            "id": "auth_pending",
            "status": "pending",
            "url": "https://auth.arcade.dev/x",
        },
        status_code=200,
    )

    state = initial_state()
    with pytest.raises(GraphInterrupt):
        await ArcadeExecutor(_node()).arun(state)

    assert captured[0]["node_id"] == "ar"
    assert captured[0]["auth_url"] == "https://auth.arcade.dev/x"
    assert captured[0]["auth_id"] == "auth_pending"
    assert captured[0]["tool_name"] == "TestTool@1.0.0"
    # State mutation: retry counter incremented in-place before interrupt
    assert state["variables"]["_arcade_retries_ar"] == 1


async def test_auth_failed_raises(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(
        method="POST",
        url=f"{ARCADE_API_BASE}/tools/authorize",
        json={"id": "auth1", "status": "failed"},
        status_code=200,
    )
    with pytest.raises(ArcadeAuthError, match="authorization failed"):
        await ArcadeExecutor(_node()).arun(initial_state())


async def test_resume_rejected_raises_canceled() -> None:
    """User rejected the auth prompt at /resume — raise ArcadeUserCanceledError
    WITHOUT calling Arcade API."""
    state = initial_state()
    state["variables"]["_approval_ar"] = "rejected"
    with pytest.raises(ArcadeUserCanceledError):
        await ArcadeExecutor(_node()).arun(state)


async def test_resume_approved_retries_and_executes(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    """After resume with approved, executor re-runs authorize; if now completed,
    proceeds to execute. Retry counter cleared on success."""
    httpx_mock.add_response(
        method="POST",
        url=f"{ARCADE_API_BASE}/tools/authorize",
        json={"id": "a", "status": "completed"},
        status_code=200,
    )
    httpx_mock.add_response(
        method="POST",
        url=f"{ARCADE_API_BASE}/tools/execute",
        json={"output": "done"},
        status_code=200,
    )
    state = initial_state()
    state["variables"]["_approval_ar"] = "approved"
    state["variables"]["_arcade_retries_ar"] = 1
    delta = await ArcadeExecutor(_node()).arun(state)
    assert delta["variables"]["lastOutput"] == "done"
    # Retry counter reset on success
    assert delta["variables"]["_arcade_retries_ar"] == 0


async def test_retry_limit_exceeded_raises() -> None:
    state = initial_state()
    state["variables"]["_arcade_retries_ar"] = MAX_RETRIES + 1  # already past
    with pytest.raises(ArcadeAuthError, match="retry limit"):
        await ArcadeExecutor(_node()).arun(state)


async def test_variable_substitution_in_input(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(
        method="POST",
        url=f"{ARCADE_API_BASE}/tools/authorize",
        json={"id": "a", "status": "completed"},
        status_code=200,
    )
    httpx_mock.add_response(
        method="POST",
        url=f"{ARCADE_API_BASE}/tools/execute",
        json={"output": "ok"},
        status_code=200,
    )
    state = initial_state()
    state["variables"]["name"] = "world"
    node = _node(arcadeInput={"body": "Hello {{name}}", "count": 42})
    await ArcadeExecutor(node).arun(state)

    import json

    exec_request = httpx_mock.get_requests(
        method="POST",
        url=f"{ARCADE_API_BASE}/tools/execute",
    )[0]
    body = json.loads(exec_request.content)
    assert body["input"]["body"] == "Hello world"
    assert body["input"]["count"] == 42  # non-string passed through


async def test_variable_substitution_in_user_id(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    """arcadeUserId supports {{variable}} templating just like arcadeInput.

    Regression guard for the P0-0 finding: the designer guide recommends a
    templated arcadeUserId (e.g. so each end user authorizes under their
    own identity), but the executor used to read it literally with no
    substitution — so a workflow author following that guidance would
    silently send the literal string "{{user_email}}" to Arcade instead of
    the resolved value."""
    httpx_mock.add_response(
        method="POST",
        url=f"{ARCADE_API_BASE}/tools/authorize",
        json={"id": "a", "status": "completed"},
        status_code=200,
    )
    httpx_mock.add_response(
        method="POST",
        url=f"{ARCADE_API_BASE}/tools/execute",
        json={"output": "ok"},
        status_code=200,
    )
    state = initial_state()
    state["variables"]["user_email"] = "alice@example.com"
    node = _node(arcadeUserId="{{user_email}}")
    await ArcadeExecutor(node).arun(state)

    import json

    authorize_request = httpx_mock.get_requests(
        method="POST",
        url=f"{ARCADE_API_BASE}/tools/authorize",
    )[0]
    body = json.loads(authorize_request.content)
    assert body["user_id"] == "alice@example.com"


async def test_missing_api_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.config import get_settings

    monkeypatch.setenv("ARCADE_API_KEY", "")
    get_settings.cache_clear()
    with pytest.raises(ArcadeNodeError, match="ARCADE_API_KEY"):
        await ArcadeExecutor(_node()).arun(initial_state())


async def test_authorize_network_error_raises(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    import httpx

    httpx_mock.add_exception(
        method="POST",
        url=f"{ARCADE_API_BASE}/tools/authorize",
        exception=httpx.ConnectError("network down"),
    )
    with pytest.raises(ArcadeNodeError, match="tools/authorize"):
        await ArcadeExecutor(_node()).arun(initial_state())


async def test_authorize_4xx_raises(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(
        method="POST",
        url=f"{ARCADE_API_BASE}/tools/authorize",
        status_code=401,
        text="bad key",
    )
    with pytest.raises(ArcadeNodeError, match="401"):
        await ArcadeExecutor(_node()).arun(initial_state())


async def test_authorize_missing_id_raises(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(
        method="POST",
        url=f"{ARCADE_API_BASE}/tools/authorize",
        json={"status": "completed"},
        status_code=200,  # no 'id'
    )
    with pytest.raises(ArcadeNodeError, match="missing 'id'"):
        await ArcadeExecutor(_node()).arun(initial_state())


async def test_executor_is_registered() -> None:
    import src.executors.arcade  # noqa: F401  # pyright: ignore[reportUnusedImport]
    from src.executors.base import build_executor

    executor = build_executor(_node())
    assert isinstance(executor, ArcadeExecutor)
