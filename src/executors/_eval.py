"""simpleeval wrapper — the ONLY eval primitive in Composer (ADR-0012).

All user-supplied expression evaluation routes through `evaluate`. No
`eval()`, `exec()`, or `compile()` calls anywhere in src/. The scope
exposes a small, explicit whitelist of names + coercion functions; no
other builtins, no imports, no dunder attribute access.

Scope:
  - variables       : dict — state["variables"]
  - lastOutput      : shorthand for variables["lastOutput"]
  - node_results    : dict — state["node_results"]
  - per-call extras : e.g., `item` for data-transform iteration
  - functions       : int, float, str, bool, len (safe coercions only)
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


# Safe coercion functions exposed to expressions. No eval / exec / open /
# import / hasattr / getattr — anything that would let an expression reach
# outside the whitelisted scope.
_SAFE_FUNCTIONS: dict[str, Any] = {
    "int": int,
    "float": float,
    "str": str,
    "bool": bool,
    "len": len,
}


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
    return SimpleEval(names=names, functions=dict(_SAFE_FUNCTIONS))


def evaluate(
    expression: str,
    state: WorkflowStateDict,
    *,
    extra_names: dict[str, Any] | None = None,
) -> Any:
    """Evaluate a simpleeval expression over state.

    Raises EvalError on any failure (undefined name, syntax, blocked access,
    missing key in a dict subscript, etc.).  See ADR-0012.
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
        KeyError,
        IndexError,
        TypeError,
        ValueError,
    ) as exc:
        raise EvalError(
            f"simpleeval failed evaluating {expression!r}: {type(exc).__name__}: {exc}"
        ) from exc


__all__ = ["EvalError", "evaluate"]
