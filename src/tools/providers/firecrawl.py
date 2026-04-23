"""FirecrawlProvider — web scrape via https://firecrawl.dev."""

from typing import Any

import httpx
from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from src.config import get_settings
from src.tools.base import ApiKeyAuth, BuildContext, ToolDefinition, ToolProvider
from src.tools.registry import register_tool_provider


class MissingApiKeyError(RuntimeError):
    """Raised when FIRECRAWL_API_KEY isn't configured."""


class FirecrawlScrapeInput(BaseModel):
    url: str = Field(description="URL to scrape")


class _FirecrawlScrapeTool(BaseTool):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    name: str = "firecrawl_scrape"
    description: str = (
        "Scrape a URL and return the page content as clean markdown. "
        "Handles JavaScript rendering and pagination. Use when you need the full "
        "content of a web page, not just search results."
    )
    args_schema: type[BaseModel] = FirecrawlScrapeInput  # pyright: ignore[reportIncompatibleVariableOverride]

    def _run(self, url: str) -> str:  # pragma: no cover
        raise NotImplementedError("Use _arun")

    async def _arun(self, url: str) -> str:
        api_key = get_settings().firecrawl_api_key
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=5.0)) as client:
                resp = await client.post(
                    "https://api.firecrawl.dev/v1/scrape",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={"url": url, "formats": ["markdown"]},
                )
        except httpx.TimeoutException:
            return "Error: Firecrawl scrape timed out after 60s"
        except httpx.HTTPError as exc:
            return f"Error: Firecrawl scrape failed ({type(exc).__name__}): {exc}"

        if resp.status_code >= 400:
            return f"Error: Firecrawl scrape failed (HTTP {resp.status_code}): {resp.text[:200]}"

        data: dict[str, Any] = resp.json()
        if not data.get("success"):
            return f"Error: Firecrawl scrape returned success=false: {data}"
        payload = data.get("data", {})
        markdown = payload.get("markdown", "")
        metadata = payload.get("metadata", {})
        title = metadata.get("title", "(no title)")
        source = metadata.get("sourceURL", url)
        return f"# {title}\n\n_Source: {source}_\n\n{markdown}"


@register_tool_provider
class FirecrawlProvider(ToolProvider):
    name = "firecrawl"
    description = "Firecrawl — web scrape with JS rendering; returns clean markdown."
    category: str = "standard"  # pyright: ignore[reportIncompatibleVariableOverride]
    auth = ApiKeyAuth(env_var="FIRECRAWL_API_KEY", settings_field="firecrawl_api_key")

    async def tools(self) -> list[ToolDefinition]:
        tool_instance = _FirecrawlScrapeTool()
        return [
            ToolDefinition(
                name="firecrawl_scrape",
                description=tool_instance.description,
                args_schema=FirecrawlScrapeInput,
            )
        ]

    async def build_tool(self, tool_name: str, context: BuildContext) -> BaseTool:
        if tool_name != "firecrawl_scrape":
            raise ValueError(f"FirecrawlProvider has no tool named {tool_name!r}")
        if not get_settings().firecrawl_api_key:
            raise MissingApiKeyError("FIRECRAWL_API_KEY is not configured")
        return _FirecrawlScrapeTool()


__all__ = ["FirecrawlProvider", "MissingApiKeyError"]
