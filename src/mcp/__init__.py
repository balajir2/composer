"""MCP integration — Phase 3.

This package is a placeholder in Phase 2. Phase 3 populates it with:
  - src/mcp/base.py      — McpToolProvider(ToolProvider) subclass
  - src/mcp/client.py    — HTTP JSON-RPC MCP client
  - src/mcp/oauth.py     — OAuth 2.1 with RFC 8707, PKCE, token refresh
  - src/mcp/resolver.py  — Per-user MCP server lookup + tool resolution
  - src/mcp/providers/   — One McpToolProvider instance per registered server

Phase 2's ToolProvider ABC (src/tools/base.py) is designed so Phase 3
drops these in without modifying the Agent executor or tool registry.

See ADR-0009 + spec §8.6.
"""
