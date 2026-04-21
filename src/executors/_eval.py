"""simpleeval wrapper — the ONLY eval primitive in Composer (ADR-0012).

All user-supplied expression evaluation routes through `evaluate`. No
`eval()`, `exec()`, or `compile()` calls anywhere in src/. The scope
exposes a small, explicit whitelist of names; no builtins, no imports,
no dunder attribute access.

Scope:
  - variables       : dict — state["variables"]
  - lastOutput      : shorthand for variables["lastOutput"]
  - node_results    : dict — state["node_results"]
  - per-call extras : e.g., `item` for data-transform iteration
"""

from typing import Any

from simpleeval import (  # pyright: ignore[reportMissingImports, reportMissingTypeStubs]
    AttributeDoesNotExist,
    FunctionNotDefined,
    InvalidExpression,
    NameNotDefined,
    NumberTooHigh,
    SimpleEval,
)

from src.engine.state import WorkflowStateDict


class EvalError(RuntimeError):
    """Any simpleeval failure — invalid expression, undefined name, syntax, etc."""


def _build_evaluator(
    state: WorkflowStateDict,
    *,
    extra_names: dict[str, Any] | None = None,
) -> Any:
    """Return a SimpleEval bound to the per-node scope."""
    variables = dict(state.get("variables") or {})
    names: dict[str, Any] = {
        "variables": variables,
        "lastOutput": variables.get("lastOutput"),
        "node_results": dict(state.get("node_results") or {}),
    }
    if extra_names:
        names.update(extra_names)
    return SimpleEval(names=names)


def evaluate(
    expression: str,
    state: WorkflowStateDict,
    *,
    extra_names: dict[str, Any] | None = None,
) -> Any:
    """Evaluate a simpleeval expression over state.

    Raises EvalError on any failure (undefined name, syntax, blocked access).
    See ADR-0012.
    """
    try:
        return _build_evaluator(state, extra_names=extra_names).eval(expression)
    except (
        NameNotDefined,
        AttributeDoesNotExist,
        FunctionNotDefined,
        NumberTooHigh,
        InvalidExpression,
        SyntaxError,
    ) as exc:
        raise EvalError(
            f"simpleeval failed evaluating {expression!r}: {type(exc).__name__}: {exc}"
        ) from exc


__all__ = ["EvalError", "evaluate"]
