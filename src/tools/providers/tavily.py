"""TavilyProvider — web search via Tavily (the Phase 2 reference provider).

See spec §9 and ADR-0009.
"""

from typing import Any

import httpx
from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from src.config import get_settings
from src.tools.base import (
    ApiKeyAuth,
    BuildContext,
    HealthStatus,
    ToolDefinition,
    ToolProvider,
)
from src.tools.registry import register_tool_provider


class MissingApiKeyError(RuntimeError):
    """Raised when TAVILY_API_KEY isn't configured."""


class TavilySearchInput(BaseModel):
    query: str = Field(description="Web search query")
    max_results: int = Field(default=5, ge=1, le=20)


class _TavilySearchTool(BaseTool):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str = "tavily_search"
    description: str = (
        "Search the web via Tavily. Use for current events, fact lookups, "
        "and questions that need fresh information."
    )
    args_schema: type[BaseModel] = TavilySearchInput  # pyright: ignore[reportIncompatibleVariableOverride]

    def _run(self, query: str, max_results: int = 5) -> str:  # pragma: no cover
        raise NotImplementedError("Use _arun — Composer is async-only.")

    async def _arun(self, query: str, max_results: int = 5) -> str:
        api_key = get_settings().tavily_api_key
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key": api_key,
                        "query": query,
                        "max_results": max_results,
                        "search_depth": "basic",
                    },
                )
        except httpx.TimeoutException:
            return "Error: Tavily search timed out after 30s"
        except httpx.HTTPError as exc:
            return f"Error: Tavily search failed ({type(exc).__name__}): {exc}"

        if resp.status_code >= 400:
            body = resp.text[:200]
            return f"Error: Tavily search failed (HTTP {resp.status_code}): {body}"

        data: dict[str, Any] = resp.json()
        results = data.get("results", [])
        if not results:
            return "No results."
        lines = [f"# Search results for: {query}\n"]
        for r in results:
            lines.append(f"## {r.get('title', '(no title)')}")
            lines.append(f"{r.get('url', '')}")
            lines.append(f"{r.get('content', '')}\n")
        return "\n".join(lines)


@register_tool_provider
class TavilyProvider(ToolProvider):
    name = "tavily"
    description = "Tavily web search — general-purpose web search with content extraction."
    category: str = "standard"  # pyright: ignore[reportIncompatibleVariableOverride]
    auth = ApiKeyAuth(env_var="TAVILY_API_KEY", settings_field="tavily_api_key")

    async def tools(self) -> list[ToolDefinition]:
        tool_instance = _TavilySearchTool()
        return [
            ToolDefinition(
                name="tavily_search",
                description=tool_instance.description,
                args_schema=TavilySearchInput,
            ),
        ]

    async def build_tool(self, tool_name: str, context: BuildContext) -> BaseTool:
        if tool_name != "tavily_search":
            raise ValueError(f"TavilyProvider has no tool named {tool_name!r}")
        if not get_settings().tavily_api_key:
            raise MissingApiKeyError("TAVILY_API_KEY is not configured")
        return _TavilySearchTool()

    async def health_check(self) -> HealthStatus:
        key = get_settings().tavily_api_key
        if not key:
            return HealthStatus(ok=False, message="TAVILY_API_KEY missing in settings")
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key": key,
                        "query": "ping",
                        "max_results": 1,
                        "search_depth": "basic",
                    },
                )
            if resp.status_code == 200:
                return HealthStatus(ok=True, message="Tavily reachable + key valid")
            return HealthStatus(
                ok=False,
                message=f"Tavily returned HTTP {resp.status_code}: {resp.text[:200]}",
            )
        except httpx.HTTPError as exc:
            return HealthStatus(ok=False, message=f"Tavily unreachable: {exc}")


__all__ = ["MissingApiKeyError", "TavilyProvider"]
