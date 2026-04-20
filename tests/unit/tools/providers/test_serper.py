"""Tests for SerperProvider (Google search via Serper.dev)."""

import pytest
from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]

from src.config import get_settings
from src.engine.state import initial_state
from src.engine.workflow import AgentNode
from src.tools.base import ApiKeyAuth, BuildContext
from src.tools.providers.serper import MissingApiKeyError, SerperProvider


def _agent_node() -> AgentNode:
    return AgentNode.model_validate(
        {
            "id": "a",
            "type": "agent",
            "position": {"x": 0, "y": 0},
            "data": {"label": "A"},
        }
    )


def test_provider_metadata() -> None:
    p = SerperProvider()
    assert p.name == "serper"
    assert p.category == "standard"
    assert isinstance(p.auth, ApiKeyAuth)
    assert p.auth.env_var == "SERPER_API_KEY"


async def test_tools_lists_search() -> None:
    tools = await SerperProvider().tools()
    assert [t.name for t in tools] == ["serper_search"]


async def test_build_tool_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SERPER_API_KEY", "")
    get_settings.cache_clear()
    with pytest.raises(MissingApiKeyError):
        await SerperProvider().build_tool(
            "serper_search",
            BuildContext(node=_agent_node(), state=initial_state()),
        )


async def test_search_tool_returns_markdown(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    monkeypatch.setenv("SERPER_API_KEY", "test-key")
    get_settings.cache_clear()
    httpx_mock.add_response(
        url="https://google.serper.dev/search",
        method="POST",
        json={
            "organic": [
                {
                    "title": "Result 1",
                    "link": "https://example.com/1",
                    "snippet": "Body 1",
                    "position": 1,
                },
                {
                    "title": "Result 2",
                    "link": "https://example.com/2",
                    "snippet": "Body 2",
                    "position": 2,
                },
            ],
        },
    )
    tool = await SerperProvider().build_tool(
        "serper_search",
        BuildContext(node=_agent_node(), state=initial_state()),
    )
    result = await tool._arun(query="test query")  # pyright: ignore[reportPrivateUsage]
    assert "Result 1" in result and "https://example.com/1" in result


async def test_search_tool_http_error_returns_error_string(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    monkeypatch.setenv("SERPER_API_KEY", "test-key")
    get_settings.cache_clear()
    httpx_mock.add_response(
        url="https://google.serper.dev/search", method="POST", status_code=500, text="err"
    )
    tool = await SerperProvider().build_tool(
        "serper_search",
        BuildContext(node=_agent_node(), state=initial_state()),
    )
    result = await tool._arun(query="foo")  # pyright: ignore[reportPrivateUsage]
    assert result.startswith("Error: Serper search failed (HTTP 500)")
