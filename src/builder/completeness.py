"""Content checks for builder output: are the nodes actually filled in?

Kept separate from `collect_workflow_violations` on purpose. That validator
gates every canvas save and every run, and a half-built canvas must still
save. These checks only steer the builder (its repair rounds) and warn the
user; they never block a save.
"""

from collections.abc import Mapping, Set

from src.engine.workflow import Workflow
from src.variable_validation import find_unknown_variable_references

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


def collect_completeness_issues(
    workflow: Workflow,
    *,
    model_catalog: Mapping[str, str | None] | None = None,
    credential_requested: Set[str] = frozenset(),
    only: Set[str] | None = None,
) -> list[str]:
    """Return one fix-it message per unfilled node (empty = complete).

    `only` limits the report to those node ids. Variable names are still
    resolved from every node (via the publish-time rules in
    `src.variable_validation`), so references to untouched nodes resolve.
    """
    dumped = [(n.id, n.type, n.data.model_dump(by_alias=True)) for n in workflow.nodes]
    unknown: dict[str, list[str]] = {}
    for ref in find_unknown_variable_references(workflow):
        names = unknown.setdefault(ref.node_id, [])
        if ref.root_name not in names:
            names.append(ref.root_name)
    issues: list[str] = []
    for node_id, node_type, data in dumped:
        if only is not None and node_id not in only:
            continue
        where = f"{node_type} node {node_id!r}"
        # Modes that run without an LLM need no prompt or model: jira
        # "extract" is a JQL search; an mcp node with a toolName and no
        # instructions calls that one tool (src/executors/mcp.py arun).
        uses_llm = not (
            (node_type == "jira" and data.get("operation") == "extract")
            or (node_type == "mcp" and data.get("toolName") and not data.get("instructions"))
        )
        field = _INSTRUCTION_FIELD.get(node_type) if uses_llm else None
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
        if model_catalog and uses_llm and node_type in _MODEL_CHECKED_TYPES:
            model = data.get("model")
            if not model:
                issues.append(f"{where} has no `model`: pick one of the available models.")
            elif model not in model_catalog:
                issues.append(
                    f"{where} uses model {model!r}, which is not available: "
                    "pick one of the available models."
                )
        missing = sorted(unknown.get(node_id, []))
        if missing:
            issues.append(
                f"{where} references undeclared variable(s) {', '.join(missing)}: "
                "declare them with set_start_inputs or fix the reference."
            )
    return issues
