"""HTTP node executor.

Fires a single HTTP request and returns the parsed response body. Non-2xx
responses raise HttpNodeError; that bubbles up through LangGraphExecutor
as a failed execution.

See Phase 4a spec §6. SSRF policy + response-size cap + URL redaction:
see P0-6 in docs/claude-improvement-backlog.md.
"""

import logging
from typing import Any, cast
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from src.config import get_settings
from src.engine.state import WorkflowStateDict
from src.engine.workflow import HttpNode
from src.executors.base import register_executor
from src.security.encryption import decrypt_sensitive_headers
from src.security.ssrf import SSRFBlockedError, validate_outbound_url
from src.variable_substitution import substitute, substitute_in_value

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 60.0
# Read once at import time (consistent with _DEFAULT_TIMEOUT above); tests
# monkeypatch this module attribute directly to exercise the size cap
# without downloading a genuinely large response.
_MAX_RESPONSE_BYTES = get_settings().http_node_max_response_bytes

# Query-param names whose values get redacted before a URL appears in an
# error message or node_results — these commonly carry secrets embedded
# directly in the URL (e.g. `?api_key=...`).
_SENSITIVE_QUERY_PARAMS = frozenset(
    {"token", "api_key", "apikey", "access_token", "secret", "password", "key", "authorization"}
)


class HttpNodeError(RuntimeError):
    """Raised when the HTTP node gets a non-2xx response or transport failure."""


def _dot_path(value: Any, path: str) -> Any:
    """Walk a dot-separated path into a nested dict/list.  Missing → None."""
    cur: Any = value
    for segment in path.split("."):
        if isinstance(cur, dict) and segment in cur:
            cur = cast("dict[str, Any]", cur)[segment]
        elif isinstance(cur, list) and segment.isdigit():
            cur = cast("list[Any]", cur)
            idx = int(segment)
            cur = cur[idx] if 0 <= idx < len(cur) else None
        else:
            return None
    return cur


def _redact_url(url: str) -> str:
    """Strip userinfo (user:pass@) and redact sensitive query-param values
    before a URL is persisted in an error message or node_results."""
    parts = urlsplit(url)
    netloc = parts.hostname or ""
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    query_pairs = parse_qsl(parts.query, keep_blank_values=True)
    redacted_pairs = [
        (k, "***REDACTED***" if k.lower() in _SENSITIVE_QUERY_PARAMS else v) for k, v in query_pairs
    ]
    redacted_query = urlencode(redacted_pairs)
    return urlunsplit((parts.scheme, netloc, parts.path, redacted_query, ""))


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

        try:
            validate_outbound_url(url, get_settings())
        except SSRFBlockedError as exc:
            raise HttpNodeError(
                f"http node {self.node.id!r}: blocked by SSRF policy: {exc}"
            ) from exc

        # P0-5: sensitive header values (Authorization, X-Api-Key, ...) are
        # encrypted at rest by the workflow API; decrypt before
        # substitution so `{{var}}` templates inside a decrypted value
        # still resolve normally.
        raw_headers = decrypt_sensitive_headers(self.node.data.http_headers or {})
        headers = substitute_in_value(raw_headers, state)
        body_raw = substitute_in_value(self.node.data.http_body, state)

        body_kwargs: dict[str, Any] = {}
        if isinstance(body_raw, (dict, list)):
            body_kwargs["json"] = body_raw
        elif isinstance(body_raw, str) and body_raw:
            body_kwargs["content"] = body_raw

        redacted_url = _redact_url(url)

        # follow_redirects defaults to False in httpx — deliberately not
        # enabled here, since following a redirect would need the same
        # SSRF validation re-applied to the new target (P0-6) and that
        # isn't implemented.
        try:
            async with (
                httpx.AsyncClient(timeout=httpx.Timeout(_DEFAULT_TIMEOUT, connect=5.0)) as client,
                client.stream(method, url, headers=headers, **body_kwargs) as resp,
            ):
                chunks: list[bytes] = []
                total = 0
                async for chunk in resp.aiter_bytes():
                    total += len(chunk)
                    if total > _MAX_RESPONSE_BYTES:
                        raise HttpNodeError(
                            f"http node {self.node.id!r}: response from {redacted_url!r} "
                            f"exceeded the {_MAX_RESPONSE_BYTES}-byte size limit"
                        )
                    chunks.append(chunk)
                body = b"".join(chunks)
                status_code = resp.status_code
                content_type = resp.headers.get("content-type", "").lower()
        except httpx.HTTPError as exc:
            raise HttpNodeError(
                f"http node {self.node.id!r} transport failed: {type(exc).__name__}: {exc}"
            ) from exc

        if status_code >= 400:
            raise HttpNodeError(
                f"http node {self.node.id!r} got HTTP {status_code} from "
                f"{redacted_url!r}: {body[:200].decode('utf-8', errors='replace')}"
            )

        if "application/json" in content_type:
            import json

            parsed: Any = json.loads(body)
        else:
            parsed = body.decode("utf-8", errors="replace")

        if self.node.data.response_path:
            parsed = _dot_path(parsed, self.node.data.response_path)

        return {
            "variables": {"lastOutput": parsed},
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "input": {"method": method, "url": redacted_url},
                    "output": parsed,
                }
            },
        }


__all__ = ["HttpExecutor", "HttpNodeError"]
