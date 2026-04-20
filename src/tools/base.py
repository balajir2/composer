"""Tool Provider Framework — base abstractions.

See ADR-0009; spec §8.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from langchain_core.tools import BaseTool
from pydantic import BaseModel

from src.config import get_settings

if TYPE_CHECKING:
    from src.engine.state import WorkflowStateDict
    from src.engine.workflow import AgentNode


# ─── Auth requirements ────────────────────────────────────────


@dataclass(frozen=True, kw_only=True)
class AuthRequirement:
    """Base: a provider declares what kind of auth it needs."""

    required: bool = True


@dataclass(frozen=True, kw_only=True)
class NoAuth(AuthRequirement):
    required: bool = False


@dataclass(frozen=True, kw_only=True)
class ApiKeyAuth(AuthRequirement):
    """Single API key stored centrally in Settings."""

    env_var: str
    settings_field: str


@dataclass(frozen=True, kw_only=True)
class OAuthAuth(AuthRequirement):
    """Per-user OAuth (Phase 3 MCP). Envelope here; details in Phase 3."""

    authorize_url: str
    token_url: str
    scopes: list[str] = field(default_factory=list)
    include_rfc8707_resource: bool = True


# ─── Descriptors ──────────────────────────────────────────────


@dataclass(frozen=True, kw_only=True)
class ToolDefinition:
    """Enumerable metadata about a tool offered by a provider."""

    name: str
    description: str
    args_schema: type[BaseModel]


@dataclass(kw_only=True)
class BuildContext:
    """Context passed to `build_tool` — everything a provider might need."""

    node: "AgentNode"
    state: "WorkflowStateDict"
    user_id: str | None = None
    db: Any | None = None  # Prisma client — populated when MCP resolution is needed


@dataclass(frozen=True, kw_only=True)
class HealthStatus:
    ok: bool
    message: str


# ─── Provider ABC ─────────────────────────────────────────────


class ToolProvider(ABC):
    """A service offering one or more tools for LLM consumption.

    Standard tools (Tavily, Serper, …) subclass this directly. MCP servers
    subclass via McpToolProvider (Phase 3). Register at import time via
    @register_tool_provider from src.tools.registry.
    """

    name: str
    description: str
    category: Literal["standard", "mcp"]
    auth: AuthRequirement

    @abstractmethod
    async def tools(self) -> list[ToolDefinition]:
        """Enumerate offered tools."""

    @abstractmethod
    async def build_tool(self, tool_name: str, context: BuildContext) -> BaseTool:
        """Construct a LangChain BaseTool for the named tool."""

    async def health_check(self) -> HealthStatus:
        """Default: verify the auth credential is present in Settings."""
        if isinstance(self.auth, ApiKeyAuth):
            value = getattr(get_settings(), self.auth.settings_field, "")
            ok = bool(value)
            return HealthStatus(
                ok=ok,
                message=(
                    f"{self.auth.env_var} present"
                    if ok
                    else f"{self.auth.env_var} missing in settings"
                ),
            )
        if isinstance(self.auth, NoAuth):
            return HealthStatus(ok=True, message="no auth required")
        # OAuthAuth: default is "configured" — Phase 3 subclass overrides.
        return HealthStatus(ok=True, message="OAuth configured")


__all__ = [
    "ApiKeyAuth",
    "AuthRequirement",
    "BuildContext",
    "HealthStatus",
    "NoAuth",
    "OAuthAuth",
    "ToolDefinition",
    "ToolProvider",
]
