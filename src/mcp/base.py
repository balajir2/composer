"""McpToolProvider — one instance per McpServer row.

Per ADR-0010, NOT registered via @register_tool_provider. The resolver
instantiates one of these on demand when a node's mcp_server_ids is
non-empty. Still implements the Phase 2 ToolProvider ABC.

See Phase 3a spec §9.
"""

from typing import Any, cast

from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, create_model

from src.mcp.client import MCPClient
from src.mcp.oauth import get_valid_access_token
from src.mcp.sanitize import strip_binary_blobs
from src.mcp.schema_adapter import normalize_input_schema, substitute_url_placeholders
from src.security.encryption import decrypt
from src.tools.base import (
    ApiKeyAuth,
    AuthRequirement,
    BuildContext,
    HealthStatus,
    NoAuth,
    OAuthAuth,
    ToolDefinition,
    ToolProvider,
)


class McpToolProvider(ToolProvider):
    """Runtime-constructed MCP provider. One instance per McpServer row."""

    category: str = "mcp"  # pyright: ignore[reportIncompatibleVariableOverride]

    def __init__(
        self,
        server: Any,
        *,
        db: Any | None = None,
        user_id: str | None = None,
    ) -> None:
        """`server` is a Prisma McpServer row (duck-typed — attribute access).

        db + user_id are required for authType='oauth' — McpToolProvider looks
        tokens up from Prisma on each outbound request. Static-auth callers
        may omit both.
        """
        self._server = server
        self._db = db
        self._user_id = user_id
        resolved_url = substitute_url_placeholders(server.url)

        if server.authType == "oauth":
            # OAuth: use factory so token refreshes apply per outbound call.
            self._client = MCPClient(
                resolved_url,
                auth_header_factory=self._build_oauth_auth_header,
            )
        else:
            # Static auth: resolve header once at construction.
            self._client = MCPClient(
                resolved_url,
                auth_header=self._build_auth_header(),
            )

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
            config: dict[str, Any] = self._server.oauthConfig or {}
            scopes: list[Any] = config.get("scopes", []) or []
            return OAuthAuth(
                authorize_url=config.get("authorizeUrl", ""),
                token_url=config.get("tokenUrl", ""),
                scopes=list(scopes),
                include_rfc8707_resource=True,
            )
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

        Fetches tools/list once and uses the real inputSchema + description
        so the LLM knows which args the tool requires — critical for MCPs
        like firecrawl that reject calls missing required params.  Falls
        back to an open schema only when the server doesn't advertise the
        requested tool.
        """
        del context  # BuildContext isn't needed for static-schema tools.
        defs = await self.tools()
        for td in defs:
            if td.name == tool_name:
                return self.build_tool_from_def(td)
        # Not found in tools/list — keep old open-schema fallback so the
        # executor can still attempt the call (the server will reject with
        # a clear error we surface downstream).
        args_schema = create_model(f"{tool_name}_Args", __config__=ConfigDict(extra="allow"))
        return _McpBoundTool(
            client=self._client,
            tool_name=tool_name,
            description=f"MCP tool: {tool_name}",
            args_schema=args_schema,
        )

    def build_tool_from_def(self, td: ToolDefinition) -> BaseTool:
        """Build a bound tool from an already-fetched ToolDefinition.

        Preferred entry point when the caller already has the tool list
        (e.g. resolver iterating over `tools()`) — avoids a second
        tools/list round-trip.
        """
        return _McpBoundTool(
            client=self._client,
            tool_name=td.name,
            description=td.description or f"MCP tool: {td.name}",
            args_schema=td.args_schema,
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
        """Static-auth header (api-key / bearer / none). Not used for oauth."""
        auth_type = self._server.authType
        if auth_type == "none":
            return None
        if auth_type in {"api-key", "bearer"}:
            if not self._server.encryptedAccessToken:
                raise ValueError(
                    f"MCP server {self._server.id!r} is authType={auth_type} "
                    f"but has no encryptedAccessToken"
                )
            token = decrypt(self._server.encryptedAccessToken)
            if auth_type == "bearer":
                return {"Authorization": f"Bearer {token}"}
            header_name = self._server.headerName or "Authorization"
            return {header_name: token}
        if auth_type == "oauth":
            # Should never happen — __init__ routes oauth through the async factory.
            raise RuntimeError("OAuth auth headers must be built via the async factory.")
        raise ValueError(f"Unknown authType {auth_type!r}")

    async def _build_oauth_auth_header(self) -> dict[str, str]:
        """Async factory: fetches a valid OAuth token per request (refreshes if needed)."""
        if self._db is None:
            raise RuntimeError(
                f"McpToolProvider for oauth server {self._server.id!r} requires db+user_id; "
                f"resolver must pass them."
            )
        token = await get_valid_access_token(self._server, self._user_id, self._db)
        return {"Authorization": f"Bearer {token}"}


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
        # Strip keys whose value is None.  Pydantic auto-fills optional
        # fields with `None` after validation, but many MCP servers
        # (firecrawl, highspot) reject null on optional params with
        # "expected string, received null".  The LLM didn't set these,
        # so omitting them matches its intent and the server's expected
        # request shape.
        cleaned = {k: v for k, v in kwargs.items() if v is not None}
        result = await client.tools_call(tool_name, cleaned)
        return _render_content_blocks(result.get("content", []))


def _json_schema_to_pydantic(name: str, schema: dict[str, Any]) -> type[BaseModel]:
    """Convert a JSON Schema dict into a Pydantic model for LangChain tool binding.

    Minimal implementation: top-level type=object, properties map to fields.
    Each field carries its `description` from the JSON Schema so LangChain
    exposes it to the LLM — without that, the model often can't guess
    what a param like `prompt` or `query` is supposed to contain and
    either skips the call or passes garbage.  Nested objects / oneOf /
    arrays with complex items degrade to dict[str, Any].
    """
    from pydantic import Field as _Field

    # `protected_namespaces=()` silences Pydantic v2's warning when an MCP
    # tool declares a field that collides with a BaseModel attribute —
    # firecrawl_extract / firecrawl_agent both have a `schema` field that
    # shadows BaseModel.schema().  The warning was spamming the uvicorn
    # log on every tools/list fetch.
    args_config = ConfigDict(protected_namespaces=())

    if schema.get("type") != "object":
        # Catch-all: single-field model that accepts anything
        return create_model(
            f"{name}_Args",
            __config__=args_config,
            value=(Any, ...),  # pyright: ignore[reportArgumentType]
        )
    props: dict[str, Any] = schema.get("properties", {})
    required = set(schema.get("required", []))
    fields: dict[str, Any] = {}
    for prop_name, prop_schema in props.items():
        py_type = _json_type_to_python(prop_schema)
        description = (
            cast("dict[str, Any]", prop_schema).get("description")
            if isinstance(prop_schema, dict)
            else None
        )
        # Pydantic's Field() becomes the default in the tuple.  Use `...`
        # as its positional default for required fields, None for optional,
        # and attach description so LangChain → LLM passes it through.
        field_default: Any = ... if prop_name in required else None
        if description:
            field_def = _Field(default=field_default, description=str(description))
            fields[prop_name] = (py_type, field_def)
        else:
            fields[prop_name] = (py_type, field_default)
    if not fields:
        # No properties defined — accept any kwargs
        return create_model(
            f"{name}_Args",
            __config__=ConfigDict(extra="allow", protected_namespaces=()),
        )
    return create_model(  # pyright: ignore[reportArgumentType]
        f"{name}_Args", __config__=args_config, **fields
    )


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
    """Concatenate text content blocks; non-text blocks render as placeholders.

    Text content runs through `strip_binary_blobs` before joining — some MCP
    servers (Highspot's `get_item_content`) embed base64-encoded binary
    payloads in the *text* channel, which would otherwise burn tens of
    thousands of tokens of useless bytes into the agent's conversation
    history.  See src/mcp/sanitize.py for the rationale.
    """
    out: list[str] = []
    for block in content:
        btype = block.get("type")
        if btype == "text":
            raw_text = str(block.get("text", ""))
            out.append(strip_binary_blobs(raw_text))
        elif btype in {"image", "audio", "resource"}:
            out.append(f"[{btype} content omitted]")
        else:
            out.append(f"[{btype or 'unknown'}: {block}]")
    return "\n".join(out) if out else ""


__all__ = ["McpToolProvider"]
