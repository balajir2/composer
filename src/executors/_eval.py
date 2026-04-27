"""simpleeval wrapper — the ONLY eval primitive in Composer (ADR-0012).

All user-supplied expression evaluation routes through `evaluate`. No
`eval()`, `exec()`, or `compile()` calls anywhere in src/. The scope
exposes a small, explicit whitelist of names + coercion functions; no
other builtins, no imports, no dunder attribute access.

Scope:
  - variables       : dict — state["variables"]
  - lastOutput      : shorthand for variables["lastOutput"]
  - node_results    : dict — state["node_results"]
  - <each top-level variable> : exposed as a direct name so designers
    can write `check_guard["passed"]` instead of
    `variables["check_guard"]["passed"]`. Reserved names
    (`variables`, `lastOutput`, `node_results`) cannot be shadowed.
  - per-call extras : e.g., `item` for data-transform iteration
  - functions       : int, float, str, bool, len (safe coercions only)

Mustache convenience:
  Conditions also accept the prompt-style `{{path.to.field}}` syntax used
  everywhere else in the canvas. We rewrite each `{{a.b.c}}` to
  `a["b"]["c"]` BEFORE simpleeval parses it. Without this, designers
  who reasonably reach for the familiar prompt syntax hit
  "Set is not available in this evaluator" because Python parses
  `{...}` as a set literal.
"""

import re
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

# Reserved names that user variables must not shadow when spread into
# the eval scope.  Without this guard, a workflow with a variable named
# `lastOutput` would clobber the reserved alias and break expressions
# that rely on it.
_RESERVED_SCOPE_NAMES: frozenset[str] = frozenset({"variables", "lastOutput", "node_results"})

_MUSTACHE_PATTERN = re.compile(r"\{\{([^{}]+)\}\}")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _expand_mustache_to_subscript(expression: str) -> str:
    """Rewrite Mustache references into subscript form for simpleeval.

    `{{check_guard.passed}}` → `check_guard["passed"]`
    `{{user.profile.name}}`  → `user["profile"]["name"]`
    `{{lastOutput}}`         → `lastOutput`

    Bails out (leaves the original `{{...}}` text) when any segment
    isn't a simple identifier, on the theory that an unusual
    expression will be more useful as the original error message than
    a rewrite that silently changes meaning.
    """

    def _replace(match: "re.Match[str]") -> str:
        path = match.group(1).strip()
        if not path:
            return match.group(0)
        segments = path.split(".")
        if not all(_IDENT.match(s) for s in segments):
            return match.group(0)
        head = segments[0]
        tail = "".join(f'["{s}"]' for s in segments[1:])
        return head + tail

    return _MUSTACHE_PATTERN.sub(_replace, expression)


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
    # Spread top-level variables (including aliases populated by
    # events_wrapper such as `check_guard` for a "Check Guard" node)
    # so designers can write `check_guard["passed"]` directly.
    for name, value in variables.items():
        if name in _RESERVED_SCOPE_NAMES:
            continue
        if not _IDENT.match(name):
            # Skip non-identifier keys silently — they can still be
            # reached via `variables["odd-key"]`.
            continue
        names.setdefault(name, value)
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

    Accepts either native simpleeval syntax (`check_guard["passed"]`)
    or Mustache-style references (`{{check_guard.passed}}`) — the
    latter is rewritten to subscript form before parsing.

    Raises EvalError on any failure (undefined name, syntax, blocked access,
    missing key in a dict subscript, etc.).  See ADR-0012.
    """
    expanded = _expand_mustache_to_subscript(expression)
    try:
        return _build_evaluator(state, extra_names=extra_names).eval(expanded)
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
