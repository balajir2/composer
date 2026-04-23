"""HTTP node executor.

Fires a single HTTP request and returns the parsed response body. Non-2xx
responses raise HttpNodeError; that bubbles up through LangGraphExecutor
as a failed execution.

See Phase 4a spec §6.
"""

import logging
from typing import Any

import httpx

from src.engine.state import WorkflowStateDict
from src.engine.workflow import HttpNode
from src.executors.base import register_executor
from src.variable_substitution import substitute, substitute_in_value

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 60.0


class HttpNodeError(RuntimeError):
    """Raised when the HTTP node gets a non-2xx response or transport failure."""


def _dot_path(value: Any, path: str) -> Any:
    """Walk a dot-separated path into a nested dict/list.  Missing → None."""
    cur = value
    for segment in path.split("."):
        if isinstance(cur, dict) and segment in cur:
            cur = cur[segment]
        elif isinstance(cur, list) and segment.isdigit():
            idx = int(segment)
            cur = cur[idx] if 0 <= idx < len(cur) else None
        else:
            return None
    return cur


@register_executor("http")
class HttpExecutor:
    def __init__(self, node: HttpNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        method = (self.node.data.http_method or "GET").upper()
        raw_url = self.node.data.http_url or ""
        if not raw_url:
            raise HttpNodeError(f"http node {self.node.id!r} has no httpUrl")
        url = substitute(raw_url, state)
        headers = substitute_in_value(self.node.data.http_headers or {}, state)
        body_raw = substitute_in_value(self.node.data.http_body, state)

        body_kwargs: dict[str, Any] = {}
        if isinstance(body_raw, (dict, list)):
            body_kwargs["json"] = body_raw
        elif isinstance(body_raw, str) and body_raw:
            body_kwargs["content"] = body_raw

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(_DEFAULT_TIMEOUT, connect=5.0)
            ) as client:
                resp = await client.request(method, url, headers=headers, **body_kwargs)
        except httpx.HTTPError as exc:
            raise HttpNodeError(
                f"http node {self.node.id!r} transport failed: {type(exc).__name__}: {exc}"
            ) from exc

        if resp.status_code >= 400:
            raise HttpNodeError(
                f"http node {self.node.id!r} got HTTP {resp.status_code} from "
                f"{url!r}: {resp.text[:200]}"
            )

        content_type = resp.headers.get("content-type", "").lower()
        if "application/json" in content_type:
            parsed: Any = resp.json()
        else:
            parsed = resp.text

        if self.node.data.response_path:
            parsed = _dot_path(parsed, self.node.data.response_path)

        return {
            "variables": {"lastOutput": parsed},
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "input": {"method": method, "url": url},
                    "output": parsed,
                }
            },
        }


__all__ = ["HttpExecutor", "HttpNodeError"]
