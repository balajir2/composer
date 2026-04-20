"""McpToolProvider — one instance per McpServer row.

Per ADR-0010, NOT registered via @register_tool_provider. The resolver
instantiates one of these on demand when a node's mcp_server_ids is
non-empty. Still implements the Phase 2 ToolProvider ABC.

See Phase 3a spec §9.
"""

from typing import Any

from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, create_model

from src.mcp.client import MCPClient
from src.mcp.schema_adapter import normalize_input_schema, substitute_url_placeholders
from src.security.encryption import decrypt
from src.tools.base import (
    ApiKeyAuth,
    AuthRequirement,
    BuildContext,
    HealthStatus,
    NoAuth,
    ToolDefinition,
    ToolProvider,
)


class McpToolProvider(ToolProvider):
    """Runtime-constructed MCP provider. One instance per McpServer row."""

    category: str = "mcp"  # pyright: ignore[reportIncompatibleVariableOverride]

    def __init__(self, server: Any) -> None:
        """`server` is a Prisma McpServer row (duck-typed — attribute access)."""
        self._server = server
        resolved_url = substitute_url_placeholders(server.url)
        # OAuth servers skip header build — the auth property raises NotImplementedError
        # when accessed, not at construction time. This lets callers construct the
        # provider and inspect metadata before deciding whether to proceed with OAuth.
        auth_header = None if server.authType == "oauth" else self._build_auth_header()
        self._client = MCPClient(resolved_url, auth_header=auth_header)

    @property
    def name(self) -> str:  # pyright: ignore[reportIncompatibleVariableOverride]
        return f"mcp:{self._server.id}"

    @property
    def description(self) -> str:  # pyright: ignore[reportIncompatibleVariableOverride]
        return self._server.description or f"MCP server {self._server.name!r}"

    @property
    def auth(self) -> AuthRequirement:  # pyright: ignore[reportIncompatibleVariableOverride]
        auth_type = self._server.authType
        if auth_type == "none":
            return NoAuth()
        if auth_type in {"api-key", "bearer"}:
            return ApiKeyAuth(
                env_var=f"<mcp-server-{self._server.id}>",
                settings_field=f"mcp_server_{self._server.id}_token",
            )
        if auth_type == "oauth":
            raise NotImplementedError("OAuth auth for MCP lands in Phase 3b")
        raise ValueError(f"Unknown authType {auth_type!r} on MCP server {self._server.id!r}")

    async def tools(self) -> list[ToolDefinition]:
        raw_tools = await self._client.tools_list()
        return [
            ToolDefinition(
                name=t["name"],
                description=t.get("description", ""),
                args_schema=_json_schema_to_pydantic(
                    t.get("name", "Args"), normalize_input_schema(t)
                ),
            )
            for t in raw_tools
        ]

    async def build_tool(self, tool_name: str, context: BuildContext) -> BaseTool:
        """Build a LangChain BaseTool for the named MCP tool.

        Does NOT call tools/list to avoid an extra round-trip. The tool
        resolver (Task 6) is responsible for verifying tool existence before
        calling build_tool. An open-schema model is used so any arguments pass.
        """
        # Use an open schema — the agent will pass whatever the LLM produces.
        args_schema = create_model(f"{tool_name}_Args", __config__=ConfigDict(extra="allow"))
        return _McpBoundTool(
            client=self._client,
            tool_name=tool_name,
            description=f"MCP tool: {tool_name}",
            args_schema=args_schema,
        )

    async def health_check(self) -> HealthStatus:
        try:
            await self._client.initialize()
            tools = await self._client.tools_list()
            return HealthStatus(ok=True, message=f"connected; {len(tools)} tool(s) available")
        except Exception as exc:
            return HealthStatus(ok=False, message=f"{type(exc).__name__}: {exc}")

    # ─── private ───────────────────────────────────────────────────────

    def _build_auth_header(self) -> dict[str, str] | None:
        auth_type = self._server.authType
        if auth_type == "none":
            return None
        if auth_type in {"api-key", "bearer"}:
            if not self._server.encryptedAccessToken:
                raise ValueError(
                    f"MCP server {self._server.id!r} is authType={auth_type} but has no encryptedAccessToken"
                )
            token = decrypt(self._server.encryptedAccessToken)
            if auth_type == "bearer":
                return {"Authorization": f"Bearer {token}"}
            header_name = self._server.headerName or "Authorization"
            return {header_name: token}
        if auth_type == "oauth":
            raise NotImplementedError("OAuth auth for MCP lands in Phase 3b")
        raise ValueError(f"Unknown authType {auth_type!r}")


class _McpBoundTool(BaseTool):
    """LangChain BaseTool that dispatches to MCPClient.tools_call."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    # BaseTool fields:
    name: str = ""
    description: str = ""
    args_schema: type[BaseModel] | None = None  # pyright: ignore[reportIncompatibleVariableOverride]

    def __init__(
        self,
        *,
        client: MCPClient,
        tool_name: str,
        description: str,
        args_schema: type[BaseModel],
    ) -> None:
        super().__init__(
            name=tool_name,
            description=description or f"MCP tool: {tool_name}",
            args_schema=args_schema,
        )
        object.__setattr__(self, "_client", client)
        object.__setattr__(self, "_tool_name", tool_name)

    def _run(self, **kwargs: Any) -> str:  # pragma: no cover
        raise NotImplementedError("Use _arun — Composer is async-only.")

    async def _arun(self, **kwargs: Any) -> str:
        client: MCPClient = object.__getattribute__(self, "_client")  # pyright: ignore[reportAttributeAccessIssue]
        tool_name: str = object.__getattribute__(self, "_tool_name")  # pyright: ignore[reportAttributeAccessIssue]
        result = await client.tools_call(tool_name, kwargs)
        return _render_content_blocks(result.get("content", []))


def _json_schema_to_pydantic(name: str, schema: dict[str, Any]) -> type[BaseModel]:
    """Convert a JSON Schema dict into a Pydantic model for LangChain tool binding.

    Minimal implementation: top-level type=object, properties map to fields.
    Nested objects / oneOf / arrays with complex items degrade to dict[str, Any].
    """
    if schema.get("type") != "object":
        # Catch-all: single-field model that accepts anything
        return create_model(
            f"{name}_Args",
            value=(Any, ...),  # pyright: ignore[reportArgumentType]
        )
    props = schema.get("properties", {})
    required = set(schema.get("required", []))
    fields: dict[str, Any] = {}
    for prop_name, prop_schema in props.items():
        py_type = _json_type_to_python(prop_schema)
        default = ... if prop_name in required else None
        fields[prop_name] = (py_type, default)
    if not fields:
        # No properties defined — accept any kwargs
        return create_model(f"{name}_Args", __config__=ConfigDict(extra="allow"))
    return create_model(f"{name}_Args", **fields)  # pyright: ignore[reportArgumentType]


def _json_type_to_python(prop_schema: dict[str, Any]) -> Any:
    """JSON Schema type → Python type (minimal subset)."""
    t = prop_schema.get("type")
    if t == "string":
        return str
    if t == "integer":
        return int
    if t == "number":
        return float
    if t == "boolean":
        return bool
    if t == "array":
        return list
    if t == "object":
        return dict
    return Any


def _render_content_blocks(content: list[dict[str, Any]]) -> str:
    """Concatenate text content blocks; non-text blocks render as placeholders."""
    out: list[str] = []
    for block in content:
        btype = block.get("type")
        if btype == "text":
            out.append(str(block.get("text", "")))
        elif btype in {"image", "audio", "resource"}:
            out.append(f"[{btype} content omitted]")
        else:
            out.append(f"[{btype or 'unknown'}: {block}]")
    return "\n".join(out) if out else ""


__all__ = ["McpToolProvider"]
