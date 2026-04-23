"""BrowserlessProvider — headless-browser page fetch via chrome.browserless.io."""

import httpx
from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from src.config import get_settings
from src.tools.base import ApiKeyAuth, BuildContext, ToolDefinition, ToolProvider
from src.tools.registry import register_tool_provider


class MissingApiKeyError(RuntimeError):
    """Raised when BROWSERLESS_API_KEY isn't configured."""


class BrowserlessFetchInput(BaseModel):
    url: str = Field(description="URL to fetch with a real Chrome browser")


class _BrowserlessFetchTool(BaseTool):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    name: str = "browserless_fetch"
    description: str = (
        "Fetch a URL through a headless Chrome browser and return the fully-"
        "rendered HTML. Use for pages that require JavaScript execution — for "
        "plain scraping without JS, prefer firecrawl_scrape."
    )
    args_schema: type[BaseModel] = BrowserlessFetchInput  # pyright: ignore[reportIncompatibleVariableOverride]

    def _run(self, url: str) -> str:  # pragma: no cover
        raise NotImplementedError("Use _arun")

    async def _arun(self, url: str) -> str:
        api_key = get_settings().browserless_api_key
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=5.0)) as client:
                resp = await client.post(
                    f"https://chrome.browserless.io/content?token={api_key}",
                    json={"url": url},
                )
        except httpx.TimeoutException:
            return "Error: Browserless fetch timed out after 60s"
        except httpx.HTTPError as exc:
            return f"Error: Browserless fetch failed ({type(exc).__name__}): {exc}"

        if resp.status_code >= 400:
            return f"Error: Browserless fetch failed (HTTP {resp.status_code}): {resp.text[:200]}"
        return resp.text


@register_tool_provider
class BrowserlessProvider(ToolProvider):
    name = "browserless"
    description = "Browserless — headless Chrome page fetch. Returns rendered HTML."
    category: str = "standard"  # pyright: ignore[reportIncompatibleVariableOverride]
    auth = ApiKeyAuth(env_var="BROWSERLESS_API_KEY", settings_field="browserless_api_key")

    async def tools(self) -> list[ToolDefinition]:
        tool_instance = _BrowserlessFetchTool()
        return [
            ToolDefinition(
                name="browserless_fetch",
                description=tool_instance.description,
                args_schema=BrowserlessFetchInput,
            )
        ]

    async def build_tool(self, tool_name: str, context: BuildContext) -> BaseTool:
        if tool_name != "browserless_fetch":
            raise ValueError(f"BrowserlessProvider has no tool named {tool_name!r}")
        if not get_settings().browserless_api_key:
            raise MissingApiKeyError("BROWSERLESS_API_KEY is not configured")
        return _BrowserlessFetchTool()


__all__ = ["BrowserlessProvider", "MissingApiKeyError"]
