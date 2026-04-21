"""Tests for the http executor."""

from typing import Any

import pytest
from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]

from src.engine.state import initial_state
from src.engine.workflow import HttpNode
from src.executors.http import HttpExecutor, HttpNodeError


def _http_node(**data: Any) -> HttpNode:
    return HttpNode.model_validate(
        {
            "id": "h",
            "type": "http",
            "position": {"x": 0, "y": 0},
            "data": {"label": "H", **data},
        }
    )


async def test_http_get_returns_json(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        url="https://example.test/api/item",
        method="GET",
        json={"id": 1, "title": "ok"},
    )
    node = _http_node(httpMethod="GET", httpUrl="https://example.test/api/item")
    delta = await HttpExecutor(node).arun(initial_state())
    assert delta["variables"]["lastOutput"] == {"id": 1, "title": "ok"}
    assert delta["current_node_id"] == "h"
    assert delta["node_results"]["h"]["status"] == "completed"


async def test_http_post_with_json_body(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        url="https://example.test/echo",
        method="POST",
        json={"echoed": True},
    )
    node = _http_node(
        httpMethod="POST",
        httpUrl="https://example.test/echo",
        httpBody={"hello": "world"},
    )
    delta = await HttpExecutor(node).arun(initial_state())
    assert delta["variables"]["lastOutput"] == {"echoed": True}
    req = httpx_mock.get_request()  # pyright: ignore[reportUnknownMemberType]
    assert req is not None
    # httpx serialises dict bodies as JSON when json= is passed
    assert b'"hello"' in req.content


async def test_http_post_with_string_body(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        url="https://example.test/raw",
        method="POST",
        text="accepted",
    )
    node = _http_node(
        httpMethod="POST",
        httpUrl="https://example.test/raw",
        httpBody="raw-payload",
    )
    delta = await HttpExecutor(node).arun(initial_state())
    # Non-JSON response → returned as text
    assert delta["variables"]["lastOutput"] == "accepted"


async def test_http_non_2xx_raises(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        url="https://example.test/bad",
        method="GET",
        status_code=500,
        text="boom",
    )
    node = _http_node(httpMethod="GET", httpUrl="https://example.test/bad")
    with pytest.raises(HttpNodeError, match="500"):
        await HttpExecutor(node).arun(initial_state())


async def test_http_response_path_dot_notation(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        url="https://example.test/nested",
        method="GET",
        json={"data": {"result": {"score": 99}}},
    )
    node = _http_node(
        httpMethod="GET",
        httpUrl="https://example.test/nested",
        responsePath="data.result.score",
    )
    delta = await HttpExecutor(node).arun(initial_state())
    assert delta["variables"]["lastOutput"] == 99


async def test_http_header_variable_substitution(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        url="https://example.test/",
        method="GET",
        json={"ok": True},
    )
    node = _http_node(
        httpMethod="GET",
        httpUrl="https://example.test/",
        httpHeaders={"X-Token": "Bearer {{token}}"},
    )
    state = initial_state()
    state["variables"]["token"] = "abc"
    await HttpExecutor(node).arun(state)
    req = httpx_mock.get_request()  # pyright: ignore[reportUnknownMemberType]
    assert req is not None
    assert req.headers.get("x-token") == "Bearer abc"


async def test_http_url_variable_substitution(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        url="https://example.test/users/42",
        method="GET",
        json={"id": 42},
    )
    node = _http_node(
        httpMethod="GET",
        httpUrl="https://example.test/users/{{user_id}}",
    )
    state = initial_state()
    state["variables"]["user_id"] = 42
    delta = await HttpExecutor(node).arun(state)
    assert delta["variables"]["lastOutput"] == {"id": 42}


async def test_http_executor_is_registered() -> None:
    import src.executors.http  # noqa: F401  # pyright: ignore[reportUnusedImport]
    from src.executors.base import build_executor

    node = _http_node(httpMethod="GET", httpUrl="https://x/")
    executor = build_executor(node)
    assert isinstance(executor, HttpExecutor)
