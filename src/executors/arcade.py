"""arcade node executor.

Two-step protocol: authorize, then execute.  If auth is pending, pauses
via LangGraph's interrupt() — user completes OAuth externally and calls
POST /executions/{id}/resume to retry.  Retry counter caps attempts at
MAX_RETRIES (3).

Output:
    lastOutput = result.output.value ?? result.output ?? result

See Phase 6d spec §3 + §5, ADR-0019.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
from langgraph.types import interrupt  # pyright: ignore[reportUnknownVariableType]

from src.config import get_settings
from src.executors.base import register_executor
from src.variable_substitution import substitute

if TYPE_CHECKING:
    from src.engine.state import WorkflowStateDict
    from src.engine.workflow import ArcadeNode

ARCADE_API_BASE = "https://api.arcade.dev/v1"
MAX_RETRIES = 3


class ArcadeNodeError(RuntimeError):
    """Raised for configuration/network errors (missing key, bad response)."""


class ArcadeAuthError(RuntimeError):
    """Raised when Arcade reports auth failure or retry counter exceeded."""


class ArcadeUserCanceledError(RuntimeError):
    """Raised when the user rejects the auth prompt on /resume."""


@register_executor("arcade")
class ArcadeExecutor:
    def __init__(self, node: ArcadeNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        api_key = get_settings().arcade_api_key
        if not api_key:
            raise ArcadeNodeError(f"arcade node {self.node.id!r}: ARCADE_API_KEY not configured")

        tool_name = self.node.data.tool
        if not tool_name:
            raise ArcadeNodeError(f"arcade node {self.node.id!r}: arcadeTool is required")

        user_id = self.node.data.user_id
        variables = state.get("variables") or {}

        # Check resumed decision — if rejected, raise early
        decision = variables.get(f"_approval_{self.node.id}")
        if decision == "rejected":
            raise ArcadeUserCanceledError(
                f"arcade node {self.node.id!r}: user canceled authorization"
            )

        # Retry counter check
        retries_key = f"_arcade_retries_{self.node.id}"
        retries = int(variables.get(retries_key) or 0)
        if retries > MAX_RETRIES:
            raise ArcadeAuthError(
                f"arcade node {self.node.id!r}: auth retry limit ({MAX_RETRIES}) "
                f"exceeded — tool {tool_name!r} never authorized"
            )

        # Substitute variables in the input dict
        substituted_input: dict[str, Any] = {}
        for key, value in self.node.data.input.items():
            substituted_input[key] = substitute(value, state) if isinstance(value, str) else value

        async with httpx.AsyncClient(
            base_url=ARCADE_API_BASE,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        ) as client:
            auth = await self._authorize(client, tool_name, user_id)

            if auth.get("status") == "failed":
                raise ArcadeAuthError(
                    f"arcade node {self.node.id!r}: authorization failed for {tool_name!r}"
                )

            if auth.get("status") != "completed":
                # Auth pending → increment retry counter in state BEFORE interrupt
                # so the counter persists through LangGraph's pre-interrupt snapshot.
                next_retries = retries + 1
                state["variables"][retries_key] = next_retries
                interrupt(
                    {
                        "node_id": self.node.id,
                        "prompt": (
                            f"Authorize {tool_name}: visit {auth.get('url', '')} to complete OAuth"
                        ),
                        "auth_url": auth.get("url"),
                        "auth_id": auth.get("id"),
                        "tool_name": tool_name,
                    }
                )
                # Unreachable in current LangGraph (interrupt always raises on first
                # pass; on resume, control returns here but we re-enter arun fresh).
                raise ArcadeNodeError(f"arcade node {self.node.id!r}: interrupt() did not raise")

            # Auth completed → execute
            result = await self._execute(client, tool_name, substituted_input, user_id)

        output = self._extract_output(result)
        return {
            "variables": {
                "lastOutput": output,
                # Clear retry counter on success
                retries_key: 0,
            },
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "input": {
                        "tool": tool_name,
                        "user_id": user_id,
                    },
                    "output": output,
                }
            },
        }

    async def _authorize(
        self,
        client: httpx.AsyncClient,
        tool_name: str,
        user_id: str,
    ) -> dict[str, Any]:
        try:
            resp = await client.post(
                "/tools/authorize",
                json={"tool_name": tool_name, "user_id": user_id},
            )
        except httpx.HTTPError as exc:
            raise ArcadeNodeError(
                f"arcade node {self.node.id!r}: POST /tools/authorize failed: {exc}"
            ) from exc
        if resp.status_code >= 400:
            raise ArcadeNodeError(
                f"arcade node {self.node.id!r}: Arcade API error {resp.status_code}: {resp.text}"
            )
        body: dict[str, Any] = resp.json()
        if not body.get("id"):
            raise ArcadeNodeError(
                f"arcade node {self.node.id!r}: authorize response missing 'id': {body}"
            )
        return body

    async def _execute(
        self,
        client: httpx.AsyncClient,
        tool_name: str,
        tool_input: dict[str, Any],
        user_id: str,
    ) -> dict[str, Any]:
        try:
            resp = await client.post(
                "/tools/execute",
                json={
                    "tool_name": tool_name,
                    "input": tool_input,
                    "user_id": user_id,
                },
            )
        except httpx.HTTPError as exc:
            raise ArcadeNodeError(
                f"arcade node {self.node.id!r}: POST /tools/execute failed: {exc}"
            ) from exc
        if resp.status_code >= 400:
            raise ArcadeNodeError(
                f"arcade node {self.node.id!r}: Arcade API error {resp.status_code}: {resp.text}"
            )
        result: dict[str, Any] = resp.json()
        return result

    @staticmethod
    def _extract_output(result: dict[str, Any]) -> Any:
        output = result.get("output")
        if isinstance(output, dict) and "value" in output:
            return output["value"]
        if output is not None:
            return output
        return result


__all__ = [
    "ARCADE_API_BASE",
    "MAX_RETRIES",
    "ArcadeAuthError",
    "ArcadeExecutor",
    "ArcadeNodeError",
    "ArcadeUserCanceledError",
]
