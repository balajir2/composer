"""Assemble a redacted diagnosis context from a failed execution."""

from dataclasses import dataclass, field
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db.repos import executions as executions_repo

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


async def build_diagnosis(
    sessionmaker: async_sessionmaker[AsyncSession],
    execution_id: str,
    *,
    workflow_id: str,
    user_id: str,
    role: str = "user",
) -> Diagnosis | None:
    """Diagnose an execution of ``workflow_id`` that the caller may see.

    Same visibility rule as ``GET /executions/{id}`` (owner or admin), plus the run must belong
    to the workflow being built. Anything else is indistinguishable from "no such execution".
    Reads in one short session that is closed before returning.
    """
    async with sessionmaker() as session:
        row = await executions_repo.get(session, execution_id)
    if row is None or row.workflow_id != workflow_id:
        return None
    if role != "admin" and row.user_id != user_id:
        return None
    variables: Any = row.variables or {}
    variables_tail = _redact_dict(dict(list(variables.items())[-_MAX_TAIL:]))
    return Diagnosis(
        status=row.status,
        error=row.error,
        node_results=_redact_dict(row.node_results),
        variables_tail=variables_tail,
    )
