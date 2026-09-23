"""HTTP JSON-RPC client for MCP servers.

Supports both plain JSON responses and text/event-stream (SSE) responses.
DeepWiki and Highspot use SSE; most api-key servers use plain JSON.

Counter design: the RPC id counter is **per-instance** (not module-global).
OAB uses Date.now() (a timestamp) rather than a shared counter, so there is
no cross-request ordering guarantee in OAB either.  A per-instance counter
gives test isolation — each fresh MCPClient starts at id=1, matching the
hardcoded id=1 in SSE mock frames.  A module-global counter would cause the
SSE test to fail whenever it runs after other tests (the request would be
id=4+, but the mock frame only contains id=1).

See Phase 3a spec §6.
"""

import itertools
import json
from collections.abc import Awaitable, Callable
from typing import Any, cast

import httpx


class MCPError(RuntimeError):
    """Base class for all MCP-layer errors."""


class MCPHTTPError(MCPError):
    """Transport-level failure (non-2xx HTTP response)."""


class MCPRpcError(MCPError):
    """Protocol-level failure — response included a JSON-RPC error object."""


class MCPTimeoutError(MCPError):
    """Request timed out."""


_DEFAULT_TIMEOUT = 60.0


class MCPClient:
    """JSON-RPC over HTTP MCP client. Stateless — each method is one request."""

    def __init__(
        self,
        url: str,
        auth_header: dict[str, str] | None = None,
        auth_header_factory: Callable[[], Awaitable[dict[str, str]]] | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._url = url
        self._auth_header = dict(auth_header) if auth_header else {}
        self._auth_header_factory = auth_header_factory
        self._timeout = timeout
        self._session_id: str | None = None
        # Per-instance counter so each MCPClient starts at id=1.
        # This keeps test isolation intact and matches OAB's spirit of
        # using an unpredictable id (OAB uses Date.now()).
        self._counter = itertools.count(1)

    async def initialize(self) -> dict[str, Any]:
        """Call `initialize` — server capabilities + version handshake."""
        return await self._rpc(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "composer", "version": "0.1.0"},
            },
        )

    async def tools_list(self) -> list[dict[str, Any]]:
        """Call `tools/list` — enumerate tools. Returns the `tools` array."""
        result = await self._rpc("tools/list", {})
        return list(result.get("tools", []))

    async def tools_call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call `tools/call` — invoke a tool. Returns the full result object."""
        return await self._rpc("tools/call", {"name": name, "arguments": arguments})

    # ─── internals ─────────────────────────────────────────────────────

    async def _rpc(
        self,
        method: str,
        params: dict[str, Any],
        *,
        _allow_initialize_retry: bool = True,
    ) -> dict[str, Any]:
        rpc_id = next(self._counter)
        body = {"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params}
        if self._auth_header_factory is not None:
            auth: dict[str, str] = await self._auth_header_factory()
        else:
            auth = self._auth_header
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **auth,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout, connect=5.0)
            ) as client:
                resp = await client.post(self._url, json=body, headers=headers)
        except httpx.TimeoutException as exc:
            raise MCPTimeoutError(f"MCP {method} request timed out after {self._timeout}s") from exc
        except httpx.HTTPError as exc:
            raise MCPHTTPError(
                f"MCP {method} transport failed: {type(exc).__name__}: {exc}"
            ) from exc

        if resp.status_code >= 400:
            if (
                _allow_initialize_retry
                and method != "initialize"
                and self._needs_initialize_before_request(resp)
            ):
                await self.initialize()
                return await self._rpc(
                    method,
                    params,
                    _allow_initialize_retry=False,
                )
            raise MCPHTTPError(f"MCP {method} returned HTTP {resp.status_code}: {resp.text[:200]}")

        self._capture_session_id(resp)
        payload = self._parse_response(resp, rpc_id)

        if "error" in payload:
            err = payload["error"]
            raise MCPRpcError(
                f"MCP {method} RPC error (code={err.get('code')}): {err.get('message', 'unknown')}"
            )
        return payload.get("result", {})  # type: ignore[no-any-return]

    def _capture_session_id(self, resp: httpx.Response) -> None:
        """Remember the MCP session id returned by stateful streamable-HTTP servers."""
        session_id = resp.headers.get("mcp-session-id")
        if session_id:
            self._session_id = session_id

    def _needs_initialize_before_request(self, resp: httpx.Response) -> bool:
        """Detect stateful MCP servers that reject non-initialize calls before a session exists."""
        try:
            payload = resp.json()
        except json.JSONDecodeError:
            return False
        err = cast("dict[str, Any]", payload).get("error") if isinstance(payload, dict) else None
        if not isinstance(err, dict):
            return False
        err = cast("dict[str, Any]", err)
        message = str(err.get("message", "")).lower()
        return "initialize request" in message and "session id" in message

    def _parse_response(self, resp: httpx.Response, expected_id: int) -> dict[str, Any]:
        """Extract the JSON-RPC envelope, whether response is JSON or SSE."""
        content_type = resp.headers.get("content-type", "")
        if "text/event-stream" in content_type.lower():
            return self._parse_sse(resp.text, expected_id)
        # Plain JSON
        try:
            data: dict[str, Any] = resp.json()
            return data
        except json.JSONDecodeError as exc:
            raise MCPHTTPError(f"MCP response was not valid JSON: {exc}") from exc

    def _parse_sse(self, body: str, expected_id: int) -> dict[str, Any]:
        """Walk SSE frames, return the first `data:` line whose JSON id matches."""
        for frame in body.split("\n\n"):
            for line in frame.splitlines():
                if line.startswith("data:"):
                    data_str = line[len("data:") :].strip()
                    if not data_str:
                        continue
                    try:
                        parsed: dict[str, Any] = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    if parsed.get("id") == expected_id:
                        return parsed
        raise MCPHTTPError(f"MCP SSE response did not contain a frame with id={expected_id}")


__all__ = [
    "MCPClient",
    "MCPError",
    "MCPHTTPError",
    "MCPRpcError",
    "MCPTimeoutError",
]
