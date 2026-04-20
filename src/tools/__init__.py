"""Tool Provider Framework.

Importing this package auto-loads every provider under src.tools.providers
and src.mcp (when Phase 3 lands). The load triggers each provider's
@register_tool_provider decorator, populating the registry.
"""

from src.tools import providers as _providers

__all__ = ["_providers"]
