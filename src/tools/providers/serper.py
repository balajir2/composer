"""SerperProvider — Google search via https://serper.dev."""

from typing import Any

import httpx
from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from src.config import get_settings
from src.tools.base import ApiKeyAuth, BuildContext, ToolDefinition, ToolProvider
from src.tools.registry import register_tool_provider


class MissingApiKeyError(RuntimeError):
    """Raised when SERPER_API_KEY isn't configured."""


class SerperSearchInput(BaseModel):
    query: str = Field(description="Google search query")
    num: int = Field(default=10, ge=1, le=20)


class _SerperSearchTool(BaseTool):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str = "serper_search"
    description: str = (
        "Google search via Serper.dev. Returns Google's organic results for a query. "
        "Use when you need high-quality search results similar to a direct Google query."
    )
    args_schema: type[BaseModel] = SerperSearchInput  # pyright: ignore[reportIncompatibleVariableOverride]

    def _run(self, query: str, num: int = 10) -> str:  # pragma: no cover
        raise NotImplementedError("Use _arun")

    async def _arun(self, query: str, num: int = 10) -> str:
        api_key = get_settings().serper_api_key
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
                resp = await client.post(
                    "https://google.serper.dev/search",
                    headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
                    json={"q": query, "num": num},
                )
        except httpx.TimeoutException:
            return "Error: Serper search timed out after 30s"
        except httpx.HTTPError as exc:
            return f"Error: Serper search failed ({type(exc).__name__}): {exc}"

        if resp.status_code >= 400:
            return f"Error: Serper search failed (HTTP {resp.status_code}): {resp.text[:200]}"

        data: dict[str, Any] = resp.json()
        organic = data.get("organic", [])
        if not organic:
            return "No results."
        lines = [f"# Google search results for: {query}\n"]
        for r in organic:
            lines.append(f"## {r.get('title', '(no title)')}")
            lines.append(f"{r.get('link', '')}")
            lines.append(f"{r.get('snippet', '')}\n")
        return "\n".join(lines)


@register_tool_provider
class SerperProvider(ToolProvider):
    name = "serper"
    description = "Serper — Google search via serper.dev. Organic results in JSON."
    category: str = "standard"  # pyright: ignore[reportIncompatibleVariableOverride]
    auth = ApiKeyAuth(env_var="SERPER_API_KEY", settings_field="serper_api_key")

    async def tools(self) -> list[ToolDefinition]:
        tool_instance = _SerperSearchTool()
        return [
            ToolDefinition(
                name="serper_search",
                description=tool_instance.description,
                args_schema=SerperSearchInput,
            )
        ]

    async def build_tool(self, tool_name: str, context: BuildContext) -> BaseTool:
        if tool_name != "serper_search":
            raise ValueError(f"SerperProvider has no tool named {tool_name!r}")
        if not get_settings().serper_api_key:
            raise MissingApiKeyError("SERPER_API_KEY is not configured")
        return _SerperSearchTool()


__all__ = ["MissingApiKeyError", "SerperProvider"]
