"""Expression test/preview endpoints for the Designer's transform panels.

Lets a designer try a simpleeval expression against sample state before
saving a transform / data-transform node, instead of only discovering a
typo or wrong variable reference via a full workflow execution. Both
endpoints are pure request/response: no workflow lookup, no DB write, no
execution created. They route through the exact same `evaluate()` real
executions use (ADR-0012 -- evaluate() is the only eval primitive in
Composer), so a passing test here is a real guarantee, not a guess.

See docs/superpowers/specs/2026-07-22-transform-expression-preview-design.md.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from src.config import get_settings
from src.engine.state import initial_state
from src.executors._eval import EvalError, evaluate
from src.security.auth import get_current_user_id
from src.security.rate_limit import (
    RateLimiterProtocol,
    enforce,
    get_rate_limiter,
    per_minute_config,
)

router = APIRouter(tags=["expressions"])


class EvaluateTransformRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    expression: str
    variables: dict[str, Any] = Field(default_factory=dict)


class EvaluateExpressionResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ok: bool
    result: Any = None
    error: str | None = None


@router.post("/expressions/evaluate-transform", response_model=EvaluateExpressionResult)
async def evaluate_transform_expression(
    payload: EvaluateTransformRequest,
    user_id: str = Depends(get_current_user_id),
    limiter: RateLimiterProtocol = Depends(get_rate_limiter),
) -> EvaluateExpressionResult:  # pyright: ignore[reportUnusedFunction]
    await enforce(
        limiter,
        route_key="expression_test",
        client_key=user_id,
        config=per_minute_config(get_settings().rate_limit_expression_test_per_minute),
    )
    state = initial_state()
    state["variables"] = payload.variables
    try:
        result = evaluate(payload.expression, state)
    except EvalError as exc:
        return EvaluateExpressionResult(ok=False, error=str(exc))
    return EvaluateExpressionResult(ok=True, result=result)


__all__ = [
    "EvaluateExpressionResult",
    "EvaluateTransformRequest",
    "router",
]
