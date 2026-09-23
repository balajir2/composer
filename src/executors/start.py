"""Start node executor.

Port of OAB's lib/workflow/langgraph.ts:569-588 with two Composer-specific
additions (spec §7.1):

1. Writes `variables.lastOutput` so direct start→end workflows produce a
   useful finalOutput rather than OAB's empty default.
2. Applies the Start node's declared `inputVariables`: default values
   are merged into state for any variable the caller didn't provide, and
   `required=True` variables without a user-supplied value raise so the
   execution fails fast with a clear message instead of propagating
   undefined values through downstream prompt substitution.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

from src.config import get_settings
from src.executors.base import register_executor

if TYPE_CHECKING:
    from src.engine.state import WorkflowStateDict
    from src.engine.workflow import StartInputVariable, StartNode


class StartInputValidationError(RuntimeError):
    """Raised when a Start node's required input variable is missing, or
    when execution input violates the caller-input boundary (P0-7)."""


# Engine-owned prefixes/names execution input must never be able to set —
# these are internal bookkeeping the executor/checkpointer/nodes own
# (pause-instance tracking, retry counters, the End node's declared
# output). An external caller supplying one of these could otherwise
# inject state that looks like it came from inside the workflow.
_RESERVED_PREFIXES = ("_",)
_RESERVED_EXACT = frozenset({"finalOutput"})


def _reject_reserved_keys(user_provided: dict[str, Any]) -> None:
    bad = sorted(
        k
        for k in user_provided
        if k in _RESERVED_EXACT or any(k.startswith(p) for p in _RESERVED_PREFIXES)
    )
    if bad:
        raise StartInputValidationError(
            "Execution input contains reserved engine-owned key(s): "
            + ", ".join(bad)
            + ". These are set internally and cannot be supplied by the caller."
        )


def _reject_undeclared_keys_in_strict_mode(
    user_provided: dict[str, Any], declared: list[StartInputVariable]
) -> None:
    if not get_settings().strict_execution_input_enabled:
        return
    declared_names = {var.name for var in declared}
    undeclared = sorted(set(user_provided) - declared_names)
    if undeclared:
        raise StartInputValidationError(
            "Execution input contains undeclared variable(s) not in this "
            "workflow's Start inputs: "
            + ", ".join(undeclared)
            + " (STRICT_EXECUTION_INPUT_ENABLED is on)."
        )


def _coerce_default(var: StartInputVariable) -> Any:
    """Convert a declared default value to the variable's declared type.

    Canvas saves every default as a raw string (that's all the Input can
    emit).  For numeric / boolean / json types we try to coerce so
    downstream nodes don't have to.  If coercion fails, pass the raw
    string through — better to surface something than crash here.
    """
    raw = var.default_value
    if raw is None or raw == "":
        return None
    if var.type == "number":
        try:
            if isinstance(raw, (int, float)):
                return raw
            return float(raw)
        except (TypeError, ValueError):
            return raw
    if var.type == "boolean":
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            lowered = raw.strip().lower()
            if lowered in {"true", "1", "yes"}:
                return True
            if lowered in {"false", "0", "no", ""}:
                return False
        return bool(raw)
    if var.type == "json":
        if isinstance(raw, (dict, list)):
            return cast("dict[str, Any] | list[Any]", raw)
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                return raw
    return raw


@register_executor("start")
class StartExecutor:
    def __init__(self, node: StartNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        raw_input = state["variables"].get("input", "")

        parsed: Any = raw_input
        if isinstance(raw_input, str):
            try:
                parsed = json.loads(raw_input)
            except (json.JSONDecodeError, ValueError):
                parsed = raw_input

        # Build the variables delta: declared-variable defaults first, then
        # user-provided values override, then lastOutput summarizes the
        # caller's input.
        variables_delta: dict[str, Any] = {}
        declared = self.node.data.input_variables or []
        user_provided: dict[str, Any] = (
            cast("dict[str, Any]", parsed) if isinstance(parsed, dict) else {}
        )
        _reject_reserved_keys(user_provided)
        _reject_undeclared_keys_in_strict_mode(user_provided, declared)
        missing_required: list[str] = []

        for var in declared:
            if var.name in user_provided:
                continue
            default = _coerce_default(var)
            if default is not None:
                variables_delta[var.name] = default
            elif var.required:
                missing_required.append(var.name)

        if missing_required:
            raise StartInputValidationError(
                "Missing required Start input variable(s): "
                + ", ".join(sorted(missing_required))
                + ". Pass these in the execution input."
            )

        if isinstance(parsed, dict):
            variables_delta.update(cast("dict[str, Any]", parsed))
            variables_delta["lastOutput"] = parsed
        else:
            variables_delta["input"] = parsed
            variables_delta["lastOutput"] = parsed

        return {
            "variables": variables_delta,
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "input": raw_input,
                    "output": parsed,
                }
            },
        }


__all__ = ["StartExecutor", "StartInputValidationError"]
