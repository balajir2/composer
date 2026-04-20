"""Auto-register all tool providers.

Adding a new provider: drop a file here that defines a
@register_tool_provider class, then append `from . import <name>` below.
"""

from src.tools.providers import tavily as _tavily

__all__ = ["_tavily"]
