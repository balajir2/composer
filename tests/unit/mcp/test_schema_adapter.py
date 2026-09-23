"""Tests for MCP schema adapter."""

from typing import Any

import pytest

from src.mcp.schema_adapter import (
    UnresolvedUrlTemplateError,
    normalize_input_schema,
    substitute_url_placeholders,
)


def test_normalize_prefers_input_schema_camel() -> None:
    tool = {
        "name": "t",
        "inputSchema": {"type": "object", "properties": {"x": {"type": "string"}}},
    }
    assert normalize_input_schema(tool) == {
        "type": "object",
        "properties": {"x": {"type": "string"}},
    }


def test_normalize_falls_back_to_schema() -> None:
    tool: dict[str, Any] = {"name": "t", "schema": {"type": "object", "properties": {}}}
    assert normalize_input_schema(tool) == {"type": "object", "properties": {}}


def test_normalize_falls_back_to_input_schema_snake() -> None:
    tool = {"name": "t", "input_schema": {"type": "object"}}
    assert normalize_input_schema(tool) == {"type": "object"}


def test_normalize_prefers_input_schema_over_others() -> None:
    """camelCase inputSchema must win over the two fallbacks."""
    tool = {
        "name": "t",
        "inputSchema": {"winner": True},
        "schema": {"loser": True},
        "input_schema": {"loser": True},
    }
    assert normalize_input_schema(tool) == {"winner": True}


def test_normalize_empty_when_all_missing() -> None:
    tool = {"name": "t"}
    assert normalize_input_schema(tool) == {}


def test_substitute_url_with_known_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-123")
    from src.config import get_settings

    get_settings.cache_clear()
    assert (
        substitute_url_placeholders("https://mcp.firecrawl.dev/{FIRECRAWL_API_KEY}/v2/sse")
        == "https://mcp.firecrawl.dev/fc-123/v2/sse"
    )


def test_substitute_url_with_multiple_placeholders(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-123")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-abc")
    from src.config import get_settings

    get_settings.cache_clear()
    assert (
        substitute_url_placeholders("https://x/{FIRECRAWL_API_KEY}/{TAVILY_API_KEY}")
        == "https://x/fc-123/tvly-abc"
    )


def test_substitute_url_no_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    url = "https://mcp.deepwiki.com/sse"
    assert substitute_url_placeholders(url) == url


def test_substitute_url_unknown_placeholder_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    with pytest.raises(UnresolvedUrlTemplateError, match="DOES_NOT_EXIST"):
        substitute_url_placeholders("https://x/{DOES_NOT_EXIST}/y")
