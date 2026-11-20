"""Content checks for builder output: are the nodes actually filled in?

Kept separate from `collect_workflow_violations` on purpose. That validator
gates every canvas save and every run, and a half-built canvas must still
save. These checks only steer the builder (its repair rounds) and warn the
user; they never block a save.
"""

import re
from collections.abc import Iterator, Mapping, Set
from typing import Any

from src.engine.workflow import Workflow

_INSTRUCTION_FIELD: dict[str, str] = {
    "agent": "instructions",
    "mcp": "instructions",
    "jira": "instructions",
    "decision": "instruction",
    "gamma-ai": "prompt",
}
# Types whose `model` is a "provider/modelId" string, the catalog's key format.
_MODEL_CHECKED_TYPES = {"agent", "mcp"}
_GUARDRAIL_FLAGS = ("piiEnabled", "moderationEnabled", "jailbreakEnabled", "hallucinationEnabled")
# Mirrors the run-time scope in src/executors/_eval.py.
_BUILTIN_NAMES = {"lastOutput", "input", "variables", "node_results"}
_MUSTACHE_HEAD = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)")


def _snake(text: str) -> str:
    # Same rule as events_wrapper._sanitize_node_name.
    return re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():  # pyright: ignore[reportUnknownVariableType]
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:  # pyright: ignore[reportUnknownVariableType]
            yield from _strings(item)


def _known_names(dumped: list[tuple[str, str, dict[str, Any]]]) -> set[str]:
    names = set(_BUILTIN_NAMES)
    for node_id, node_type, data in dumped:
        names.add(node_id.replace("-", "_"))
        node_name = data.get("nodeName")
        if isinstance(node_name, str) and _snake(node_name):
            names.add(_snake(node_name))
        if node_type == "start":
            variables: list[dict[str, Any]] = data.get("inputVariables") or []
            names.update(str(v["name"]) for v in variables)
        state_key = data.get("stateKey")
        if node_type == "set-state" and isinstance(state_key, str):
            names.add(state_key)
    return names


def collect_completeness_issues(
    workflow: Workflow,
    *,
    model_catalog: Mapping[str, str | None] | None = None,
    credential_requested: Set[str] = frozenset(),
) -> list[str]:
    """Return one fix-it message per unfilled node (empty = complete)."""
    dumped = [(n.id, n.type, n.data.model_dump(by_alias=True)) for n in workflow.nodes]
    known = _known_names(dumped)
    issues: list[str] = []
    for node_id, node_type, data in dumped:
        where = f"{node_type} node {node_id!r}"
        field = _INSTRUCTION_FIELD.get(node_type)
        if field and not str(data.get(field) or "").strip():
            issues.append(f"{where} has no `{field}`: say what this step must do.")
        if node_type == "guardrails" and not any(data.get(f) for f in _GUARDRAIL_FLAGS):
            issues.append(
                f"{where} has every check off: enable at least one of "
                f"{', '.join(_GUARDRAIL_FLAGS)}."
            )
        if (
            node_type == "mcp"
            and not data.get("mcpServerId")
            and node_id not in credential_requested
        ):
            issues.append(
                f"{where} has no `mcpServerId`: call request_credential with kind "
                "'mcp-server' instead of guessing an id."
            )
        if model_catalog and node_type in _MODEL_CHECKED_TYPES:
            model = data.get("model")
            if not model:
                issues.append(f"{where} has no `model`: pick one of the available models.")
            elif model not in model_catalog:
                issues.append(
                    f"{where} uses model {model!r}, which is not available: "
                    "pick one of the available models."
                )
        missing = sorted({m for s in _strings(data) for m in _MUSTACHE_HEAD.findall(s)} - known)
        if missing:
            issues.append(
                f"{where} references undeclared variable(s) {', '.join(missing)}: "
                "declare them with set_start_inputs or fix the reference."
            )
    return issues
