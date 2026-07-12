"""Tests for workflow variable-reference validation (P0-1).

The example this guards against is not hypothetical: a live workflow
(BRD to Jira Tickets + Notification) had a Start input named `MB` while
a Jira node referenced `{{jira_project_key}}` — an undeclared variable
that would render as the literal text "{{jira_project_key}}" and get
passed straight to the Jira API.
"""

from src.engine.workflow import Workflow
from src.variable_validation import find_unknown_variable_references


def _workflow(nodes: list[dict], edges: list[dict] | None = None) -> Workflow:
    return Workflow.model_validate({"name": "T", "nodes": nodes, "edges": edges or []})


def _start(inputs: list[dict] | None = None) -> dict:
    return {
        "id": "start-1",
        "type": "start",
        "position": {"x": 0, "y": 0},
        "data": {"label": "Start", "inputVariables": inputs or []},
    }


def _end() -> dict:
    return {"id": "end-1", "type": "end", "position": {"x": 0, "y": 0}, "data": {"label": "End"}}


def test_reference_to_undeclared_start_input_is_flagged() -> None:
    """The exact real-world case: Start declares `MB`, a Jira node
    references `{{jira_project_key}}` instead."""
    wf = _workflow(
        [
            _start(inputs=[{"name": "MB", "type": "string", "required": True}]),
            {
                "id": "jira-1",
                "type": "jira",
                "position": {"x": 0, "y": 0},
                "data": {
                    "label": "Jira",
                    "domain": "x.atlassian.net",
                    "email": "a@b.com",
                    "apiToken": "tok",
                    "instructions": "Create issues in {{jira_project_key}}",
                },
            },
            _end(),
        ]
    )
    problems = find_unknown_variable_references(wf)
    assert len(problems) == 1
    assert problems[0].node_id == "jira-1"
    assert problems[0].root_name == "jira_project_key"
    assert problems[0].placeholder == "jira_project_key"


def test_reference_to_declared_start_input_is_valid() -> None:
    wf = _workflow(
        [
            _start(inputs=[{"name": "topic", "type": "string", "required": True}]),
            {
                "id": "agent-1",
                "type": "agent",
                "position": {"x": 0, "y": 0},
                "data": {"label": "Agent", "instructions": "Write about {{topic}}"},
            },
            _end(),
        ]
    )
    assert find_unknown_variable_references(wf) == []


def test_reference_to_upstream_node_alias_is_valid() -> None:
    """{{agent_1}} — the sanitized node-id alias every node's output is
    also injected under (events_wrapper.py)."""
    wf = _workflow(
        [
            _start(),
            {
                "id": "agent-1",
                "type": "agent",
                "position": {"x": 0, "y": 0},
                "data": {"label": "Agent", "instructions": "hi"},
            },
            {
                "id": "agent-2",
                "type": "agent",
                "position": {"x": 100, "y": 0},
                "data": {"label": "Agent2", "instructions": "Summarise: {{agent_1}}"},
            },
            _end(),
        ]
    )
    assert find_unknown_variable_references(wf) == []


def test_reference_to_named_node_alias_is_valid() -> None:
    """{{place_extractor}} — the sanitized nodeName alias."""
    wf = _workflow(
        [
            _start(),
            {
                "id": "extract-1",
                "type": "extract",
                "position": {"x": 0, "y": 0},
                "data": {"label": "Extract", "nodeName": "Place Extractor"},
            },
            {
                "id": "agent-2",
                "type": "agent",
                "position": {"x": 100, "y": 0},
                "data": {"label": "Agent", "instructions": "City: {{place_extractor.city}}"},
            },
            _end(),
        ]
    )
    assert find_unknown_variable_references(wf) == []


def test_builtin_variables_are_always_valid() -> None:
    wf = _workflow(
        [
            _start(),
            {
                "id": "agent-1",
                "type": "agent",
                "position": {"x": 0, "y": 0},
                "data": {"label": "Agent", "instructions": "{{lastOutput}} and {{input}}"},
            },
            _end(),
        ]
    )
    assert find_unknown_variable_references(wf) == []


def test_explicit_state_prefixed_path_is_skipped() -> None:
    """{{state.variables.x}} / {{state.nodeResults.x}} are an explicit,
    already-namespaced addressing mode — not subject to this check."""
    wf = _workflow(
        [
            _start(),
            {
                "id": "agent-1",
                "type": "agent",
                "position": {"x": 0, "y": 0},
                "data": {"label": "Agent", "instructions": "{{state.variables.anything}}"},
            },
            _end(),
        ]
    )
    assert find_unknown_variable_references(wf) == []


def test_unknown_reference_nested_in_dict_field_is_found() -> None:
    """References inside a nested object field (e.g. arcadeInput), not
    just top-level string fields, must be scanned too."""
    wf = _workflow(
        [
            _start(),
            {
                "id": "arcade-1",
                "type": "arcade",
                "position": {"x": 0, "y": 0},
                "data": {
                    "label": "Arcade",
                    "arcadeTool": "X@1",
                    "arcadeInput": {"title": "{{typo_var}}"},
                },
            },
            _end(),
        ]
    )
    problems = find_unknown_variable_references(wf)
    assert len(problems) == 1
    assert problems[0].root_name == "typo_var"


def test_multiple_unknown_references_all_reported() -> None:
    wf = _workflow(
        [
            _start(),
            {
                "id": "agent-1",
                "type": "agent",
                "position": {"x": 0, "y": 0},
                "data": {"label": "Agent", "instructions": "{{missing_a}} and {{missing_b}}"},
            },
            _end(),
        ]
    )
    problems = find_unknown_variable_references(wf)
    assert {p.root_name for p in problems} == {"missing_a", "missing_b"}
