"""Auto-register all tool providers.

Adding a new provider: drop a file here that defines a
@register_tool_provider class, then append `from . import <name>` below.
"""

from src.tools.providers import browserless as _browserless
from src.tools.providers import firecrawl as _firecrawl
from src.tools.providers import jira as _jira
from src.tools.providers import serper as _serper
from src.tools.providers import tavily as _tavily

__all__ = ["_browserless", "_firecrawl", "_jira", "_serper", "_tavily"]
