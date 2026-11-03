"""Assemble a redacted diagnosis context from a failed execution."""

from dataclasses import dataclass, field
from typing import Any, cast

REDACT_MARKER = "••••••••"
_SECRET_HINTS = ("key", "token", "secret", "password", "authorization", "api_key", "credential")
_MAX_TAIL = 30


@dataclass(frozen=True)
class Diagnosis:
    status: str
    error: str | None
    node_results: dict[str, Any] = field(default_factory=dict[str, Any])
    variables_tail: dict[str, Any] = field(default_factory=dict[str, Any])


def _redact_value(key: str, value: Any) -> Any:
    if any(hint in key.lower() for hint in _SECRET_HINTS):
        return REDACT_MARKER
    if isinstance(value, str) and len(value) > 500:
        return value[:500] + "…"
    return value


def _redact_dict(mapping: Any) -> dict[str, Any]:
    if not isinstance(mapping, dict):
        return {}
    mapping = cast("dict[Any, Any]", mapping)
    return {str(k): _redact_value(str(k), v) for k, v in mapping.items()}


async def build_diagnosis(db: Any, execution_id: str) -> Diagnosis | None:
    row = await db.workflowexecution.find_unique(where={"id": execution_id})
    if row is None:
        return None
    variables: Any = getattr(row, "variables", None) or {}
    variables_tail = _redact_dict(dict(list(variables.items())[-_MAX_TAIL:]))
    return Diagnosis(
        status=str(getattr(row, "status", "")),
        error=getattr(row, "error", None),
        node_results=_redact_dict(getattr(row, "nodeResults", None)),
        variables_tail=variables_tail,
    )
