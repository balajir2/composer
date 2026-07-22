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
from src.executors.data_transform import SUPPORTED_OPS, run_map_filter_reduce
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


# Defensive cap on evaluate-data-transform's collection size. This
# endpoint accepts a client-supplied collection directly -- unlike real
# execution, no workflow input-size limit applies here, so unbounded
# input from an authenticated-but-adversarial client shouldn't cost the
# server unbounded eval time.
_MAX_TEST_ITEMS = 50


class EvaluateDataTransformRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    operation: str
    collection: str
    expression: str
    item_var: str = Field(default="item", alias="itemVar")
    initial: Any = None
    variables: dict[str, Any] = Field(default_factory=dict)


class EvaluateDataTransformResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ok: bool
    result: Any = None
    error: str | None = None
    item_count: int | None = Field(default=None, alias="itemCount")
    truncated: bool = False


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
    except Exception as exc:
        # evaluate()'s EvalError wrapping doesn't cover every exception
        # simpleeval can raise (e.g. ZeroDivisionError isn't in its except
        # tuple). Real executors are shielded by
        # src/engine/events_wrapper.py's engine-level catch-all; this route
        # is the outermost boundary and has no equivalent net, so it must
        # catch broadly itself to honor its "always 200" contract.
        return EvaluateExpressionResult(ok=False, error=f"{type(exc).__name__}: {exc}")
    return EvaluateExpressionResult(ok=True, result=result)


@router.post("/expressions/evaluate-data-transform", response_model=EvaluateDataTransformResult)
async def evaluate_data_transform_expression(
    payload: EvaluateDataTransformRequest,
    user_id: str = Depends(get_current_user_id),
    limiter: RateLimiterProtocol = Depends(get_rate_limiter),
) -> EvaluateDataTransformResult:  # pyright: ignore[reportUnusedFunction]
    await enforce(
        limiter,
        route_key="expression_test",
        client_key=user_id,
        config=per_minute_config(get_settings().rate_limit_expression_test_per_minute),
    )
    if payload.operation not in SUPPORTED_OPS:
        return EvaluateDataTransformResult(
            ok=False,
            error=(f"operation {payload.operation!r} not supported (need map / filter / reduce)"),
        )

    state = initial_state()
    state["variables"] = payload.variables

    try:
        coll = evaluate(payload.collection, state)
    except EvalError as exc:
        return EvaluateDataTransformResult(ok=False, error=f"collection: {exc}")
    except Exception as exc:
        return EvaluateDataTransformResult(
            ok=False, error=f"collection: {type(exc).__name__}: {exc}"
        )

    if not isinstance(coll, (list, tuple)):
        return EvaluateDataTransformResult(
            ok=False,
            error=(
                f"collection evaluated to non-iterable type {type(coll).__name__}, expected a list"
            ),
        )

    truncated = len(coll) > _MAX_TEST_ITEMS
    sample = list(coll[:_MAX_TEST_ITEMS])

    try:
        result = run_map_filter_reduce(
            payload.operation, sample, payload.expression, payload.item_var, payload.initial, state
        )
    except EvalError as exc:
        return EvaluateDataTransformResult(ok=False, error=f"per-item expression: {exc}")
    except Exception as exc:
        return EvaluateDataTransformResult(
            ok=False, error=f"per-item expression: {type(exc).__name__}: {exc}"
        )

    return EvaluateDataTransformResult(
        ok=True, result=result, itemCount=len(sample), truncated=truncated
    )


__all__ = [
    "EvaluateDataTransformRequest",
    "EvaluateDataTransformResult",
    "EvaluateExpressionResult",
    "EvaluateTransformRequest",
    "router",
]
