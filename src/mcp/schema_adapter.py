"""MCP schema adapter.

Two responsibilities:
1. Normalize tool inputSchema across MCP spec versions (inputSchema /
   schema / input_schema).
2. Substitute {VAR} placeholders in MCP server URLs from Settings
   (e.g., {FIRECRAWL_API_KEY} → fc-abc123 for Firecrawl's MCP that
   embeds the API key in the URL path).

See Phase 3a spec §7.
"""

import re
from typing import Any

from src.config import get_settings


class UnresolvedUrlTemplateError(ValueError):
    """Raised when a URL contains a {VAR} placeholder not found in Settings."""


_URL_TEMPLATE_PATTERN = re.compile(r"\{([A-Z_][A-Z0-9_]*)\}")


def normalize_input_schema(mcp_tool: dict[str, Any]) -> dict[str, Any]:
    """Return a tool's input JSON Schema, checking three keys in priority order.

    MCP spec uses 'inputSchema' (camelCase); some servers use 'schema' or
    'input_schema'. Check all three, prefer spec-compliant. Default empty dict.
    """
    return (
        mcp_tool.get("inputSchema") or mcp_tool.get("schema") or mcp_tool.get("input_schema") or {}
    )


def substitute_url_placeholders(url_template: str) -> str:
    """Replace {ENV_VAR} placeholders from Settings; raise if any unresolved.

    Firecrawl uses URLs like https://mcp.firecrawl.dev/{FIRECRAWL_API_KEY}/v2/sse.
    Env-var-style names (ALL_UPPER_SNAKE) map to snake_case Settings fields.
    """
    settings = get_settings()

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        field = name.lower()
        value = getattr(settings, field, None)
        if not value:
            raise UnresolvedUrlTemplateError(
                f"URL template contains {{{name}}} but Settings.{field} is empty or missing. "
                f"Add the value to .env or pass it via env var."
            )
        return str(value)

    return _URL_TEMPLATE_PATTERN.sub(_replace, url_template)


__all__ = [
    "UnresolvedUrlTemplateError",
    "normalize_input_schema",
    "substitute_url_placeholders",
]
