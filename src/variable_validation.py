"""Workflow variable-reference validation (P0-1).

Catches the class of bug where a node references `{{some_var}}` that
doesn't correspond to any Start-declared input, another node's id/name
alias, or a known built-in. Unresolved placeholders don't fail at
runtime — `src/variable_substitution.py` renders them back as the
literal `{{some_var}}` text, so a typo silently reaches an LLM prompt
or an external action's arguments instead of erroring. Confirmed live:
a Start input named `MB` alongside a Jira node referencing the
undeclared `{{jira_project_key}}`.

Scope: verifies every referenced root name exists *somewhere* in the
workflow. It does not validate upstream/downstream ordering (whether
the referencing node actually runs after the node it references) —
that needs full DAG reachability analysis and is a tracked follow-up,
not implemented here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from src.engine.workflow import Workflow

_PATTERN = re.compile(r"\{\{([^}]+)\}\}")

# Mirrors src/variable_substitution.py's implicit roots plus the two
# fallback names guardrails/final-output selection read directly.
_BUILTIN_VARIABLES = frozenset({"lastOutput", "input", "finalOutput"})


@dataclass(frozen=True)
class UnknownVariableReference:
    node_id: str
    field_path: str
    placeholder: str
    root_name: str


def _sanitize_node_id(node_id: str) -> str:
    """Mirror events_wrapper.py's _sanitize_node_id."""
    return node_id.replace("-", "_")


def _sanitize_node_name(name: str) -> str | None:
    """Mirror events_wrapper.py's _sanitize_node_name."""
    cleaned = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return cleaned or None


def _extract_references(value: Any) -> list[str]:
    """Recursively collect every `{{...}}` placeholder body from a node
    field value (str / dict / list; other types have none)."""
    refs: list[str] = []
    if isinstance(value, str):
        refs.extend(match.group(1).strip() for match in _PATTERN.finditer(value))
    elif isinstance(value, dict):
        value = cast("dict[str, Any]", value)
        for v in value.values():
            refs.extend(_extract_references(v))
    elif isinstance(value, list):
        value = cast("list[Any]", value)
        for item in value:
            refs.extend(_extract_references(item))
    return refs


def _available_variable_names(workflow: Workflow) -> set[str]:
    """The set of valid top-level `{{...}}` root names for `workflow`."""
    names: set[str] = set(_BUILTIN_VARIABLES)
    for node in workflow.nodes:
        names.add(_sanitize_node_id(node.id))
        node_name = getattr(node.data, "node_name", None)
        if isinstance(node_name, str):
            alias = _sanitize_node_name(node_name)
            if alias:
                names.add(alias)
        if node.type == "start":
            for input_var in node.data.input_variables:  # pyright: ignore[reportAttributeAccessIssue]
                names.add(input_var.name)
    return names


def find_unknown_variable_references(workflow: Workflow) -> list[UnknownVariableReference]:
    """Return every `{{...}}` reference in `workflow` whose root segment
    doesn't match a Start input, another node's id/name alias, or a
    built-in. Skips `{{state...}}`-prefixed paths (an explicit, already-
    namespaced addressing mode) and empty placeholders."""
    available = _available_variable_names(workflow)
    problems: list[UnknownVariableReference] = []
    for node in workflow.nodes:
        node_data: dict[str, Any] = node.data.model_dump(by_alias=True)
        for field_path, value in node_data.items():
            for placeholder in _extract_references(value):
                if not placeholder:
                    continue
                root = placeholder.split(".")[0]
                if root == "state":
                    continue
                if root not in available:
                    problems.append(
                        UnknownVariableReference(
                            node_id=node.id,
                            field_path=field_path,
                            placeholder=placeholder,
                            root_name=root,
                        )
                    )
    return problems


__all__ = ["UnknownVariableReference", "find_unknown_variable_references"]
