"""Content checks that steer the builder toward filled-in nodes.

2026-11-19: a builder-made workflow had agent/MCP nodes with no prompts, a
guardrails node with every check off, and no models selected. None of that
is a structural violation, so nothing reported it.
"""

from typing import Any

from src.builder.completeness import collect_completeness_issues
from src.engine.workflow import Workflow

CATALOG: dict[str, str | None] = {"anthropic/claude-sonnet-4-5": "Sonnet"}


def _wf(*nodes: dict[str, Any], start_inputs: list[str] | None = None) -> Workflow:
    start_data: dict[str, Any] = {"label": "S"}
    if start_inputs:
        start_data["inputVariables"] = [{"name": n} for n in start_inputs]
    base: list[dict[str, Any]] = [
        {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": start_data},
        {"id": "e", "type": "end", "position": {"x": 0, "y": 0}, "data": {"label": "E"}},
    ]
    full = [*base, *[{"position": {"x": 0, "y": 0}, **n} for n in nodes]]
    return Workflow.model_validate({"name": "t", "nodes": full, "edges": []})


def _agent(nid: str = "a", **data: Any) -> dict[str, Any]:
    return {"id": nid, "type": "agent", "data": {"label": "A", **data}}


def test_filled_agent_has_no_issues() -> None:
    wf = _wf(_agent(instructions="Summarise {{lastOutput}}", model="anthropic/claude-sonnet-4-5"))
    assert collect_completeness_issues(wf, model_catalog=CATALOG) == []


def test_agent_without_instructions_is_reported() -> None:
    issues = collect_completeness_issues(_wf(_agent()))
    assert len(issues) == 1
    assert "'a'" in issues[0] and "instructions" in issues[0]


def test_whitespace_instructions_count_as_missing() -> None:
    assert collect_completeness_issues(_wf(_agent(instructions="   ")))


def test_guardrails_with_every_check_off_is_reported() -> None:
    node = {"id": "pii-filter", "type": "guardrails", "data": {"label": "PII"}}
    issues = collect_completeness_issues(_wf(node))
    assert any("pii-filter" in i and "piiEnabled" in i for i in issues)


def test_guardrails_with_one_check_on_passes() -> None:
    node = {"id": "g", "type": "guardrails", "data": {"label": "G", "piiEnabled": True}}
    assert collect_completeness_issues(_wf(node)) == []


def test_mcp_without_server_is_reported_unless_credential_requested() -> None:
    node = {"id": "m", "type": "mcp", "data": {"label": "M", "instructions": "search"}}
    assert any("mcpServerId" in i for i in collect_completeness_issues(_wf(node)))
    assert collect_completeness_issues(_wf(node), credential_requested={"m"}) == []


def test_model_rules_apply_only_with_a_catalog() -> None:
    wf = _wf(_agent(instructions="x"))
    assert collect_completeness_issues(wf) == []
    assert any("model" in i for i in collect_completeness_issues(wf, model_catalog=CATALOG))


def test_model_outside_the_catalog_is_reported() -> None:
    wf = _wf(_agent(instructions="x", model="openai/not-enabled"))
    issues = collect_completeness_issues(wf, model_catalog=CATALOG)
    assert any("openai/not-enabled" in i for i in issues)


def test_undeclared_variable_is_reported_and_declared_one_is_not() -> None:
    wf = _wf(_agent(instructions="Research {{topic}} for {{audience}}"), start_inputs=["topic"])
    issues = collect_completeness_issues(wf)
    assert any("audience" in i for i in issues)
    assert not any("topic" in i for i in issues)


def test_node_aliases_and_builtins_are_known() -> None:
    wf = _wf(
        {"id": "web-search", "type": "agent", "data": {"label": "W", "instructions": "go"}},
        {
            "id": "x",
            "type": "agent",
            "data": {"label": "X", "nodeName": "Place Extractor", "instructions": "go"},
        },
        _agent(
            "b", instructions="{{web_search}} {{place_extractor.city}} {{lastOutput}} {{input}}"
        ),
    )
    assert collect_completeness_issues(wf) == []


def test_label_is_not_a_run_time_alias() -> None:
    wf = _wf(
        {"id": "n1", "type": "agent", "data": {"label": "Search", "instructions": "go"}},
        _agent("b", instructions="use {{search}}"),
    )
    assert any("search" in i for i in collect_completeness_issues(wf))


def test_set_state_key_is_a_known_name() -> None:
    wf = _wf(
        {"id": "ss", "type": "set-state", "data": {"label": "SS", "stateKey": "summary"}},
        _agent("b", instructions="use {{summary}}"),
    )
    assert collect_completeness_issues(wf) == []
