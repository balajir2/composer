"""Tests for FirecrawlProvider (web scrape via firecrawl.dev)."""

import pytest
from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]

from src.config import get_settings
from src.engine.state import initial_state
from src.engine.workflow import AgentNode
from src.tools.base import ApiKeyAuth, BuildContext
from src.tools.providers.firecrawl import FirecrawlProvider, MissingApiKeyError


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
    p = FirecrawlProvider()
    assert p.name == "firecrawl"
    assert p.category == "standard"
    assert isinstance(p.auth, ApiKeyAuth)
    assert p.auth.env_var == "FIRECRAWL_API_KEY"


async def test_tools_lists_scrape() -> None:
    tools = await FirecrawlProvider().tools()
    assert [t.name for t in tools] == ["firecrawl_scrape"]


async def test_build_tool_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", "")
    get_settings.cache_clear()
    with pytest.raises(MissingApiKeyError):
        await FirecrawlProvider().build_tool(
            "firecrawl_scrape",
            BuildContext(node=_agent_node(), state=initial_state()),
        )


async def test_scrape_tool_returns_markdown(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test-key")
    get_settings.cache_clear()
    httpx_mock.add_response(
        url="https://api.firecrawl.dev/v1/scrape",
        method="POST",
        json={
            "success": True,
            "data": {
                "markdown": "# Page title\nBody content.",
                "metadata": {"title": "Page title", "sourceURL": "https://example.com"},
            },
        },
    )
    tool = await FirecrawlProvider().build_tool(
        "firecrawl_scrape",
        BuildContext(node=_agent_node(), state=initial_state()),
    )
    result = await tool._arun(url="https://example.com")  # pyright: ignore[reportPrivateUsage]
    assert "Page title" in result and "Body content" in result


async def test_scrape_tool_http_error(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test-key")
    get_settings.cache_clear()
    httpx_mock.add_response(
        url="https://api.firecrawl.dev/v1/scrape", method="POST", status_code=500, text="err"
    )
    tool = await FirecrawlProvider().build_tool(
        "firecrawl_scrape",
        BuildContext(node=_agent_node(), state=initial_state()),
    )
    assert (await tool._arun(url="https://example.com")).startswith(  # pyright: ignore[reportPrivateUsage]
        "Error: Firecrawl scrape failed (HTTP 500)"
    )
