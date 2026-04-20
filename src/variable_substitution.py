"""OAB-parity `{{...}}` variable substitution engine.

Mirrors lib/workflow/variable-substitution.ts. Supports flat keys,
dotted paths, explicit `state.variables.` / `state.nodeResults.`
prefixes, and blocks prototype-pollution segments.

See ADR-0007 and Phase 2 spec §7.
"""

import json
import re
from typing import Any

from src.engine.state import WorkflowStateDict

_PATTERN = re.compile(r"\{\{([^}]+)\}\}")

# Block both JS prototype-pollution keys (OAB) and Python dunder-attacks.
_UNSAFE_SEGMENTS: frozenset[str] = frozenset(
    {
        "__proto__",
        "constructor",
        "prototype",
        "__class__",
        "__dict__",
        "__globals__",
        "__builtins__",
        "__subclasses__",
    }
)


def substitute(template: str, state: WorkflowStateDict) -> str:
    """Render `template` with `{{path}}` placeholders resolved from `state`.

    Unresolved placeholders render as the literal `{{path}}` text (matches OAB).
    """
    return _PATTERN.sub(lambda m: _resolve(m.group(1).strip(), state), template)


def _resolve(path: str, state: WorkflowStateDict) -> str:
    segments = path.split(".")
    literal = f"{{{{{path}}}}}"  # fallback on miss

    # Explicit state.<root>.<rest> prefix
    if segments[0] == "state":
        if len(segments) < 3:
            return literal
        root_name = segments[1]
        rest = segments[2:]
        if root_name == "variables":
            root: Any = state["variables"]
        elif root_name == "nodeResults":
            root = state["node_results"]
        else:
            return literal
        value = _walk(root, rest)
    else:
        value = _walk(state["variables"], segments)

    if value is None:
        return literal
    return value if isinstance(value, str) else json.dumps(value)


def _walk(root: Any, segments: list[str]) -> Any:
    current = root
    for seg in segments:
        if seg in _UNSAFE_SEGMENTS:
            return None
        if isinstance(current, dict):
            current = current.get(seg)
        else:
            return None
        if current is None:
            return None
    return current


__all__ = ["substitute"]
