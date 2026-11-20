from src.builder.builder_tools import NODE_CLASSES
from src.builder.node_catalog import (
    NODE_PURPOSES,
    build_field_guide,
    build_node_catalog,
    describe_models,
)
from src.builder.prompts import build_system_prompt

EXPECTED_TYPES = {
    "start",
    "end",
    "note",
    "file-trigger",
    "file-write",
    "agent",
    "mcp",
    "http",
    "extract",
    "transform",
    "data-transform",
    "set-state",
    "if-else",
    "decision",
    "while",
    "user-approval",
    "guardrails",
    "vector-db",
    "gamma-ai",
    "email",
    "arcade",
    "join-chunks",
    "jira",
    "confluence",
    "download-pdf",
    "join",
}


def test_catalog_covers_every_workflow_node_type():
    assert set(NODE_PURPOSES) == EXPECTED_TYPES


def test_catalog_mentions_every_type():
    text = build_node_catalog()
    for t in EXPECTED_TYPES:
        assert f"`{t}`" in text


def test_field_guide_covers_every_node_type() -> None:
    guide = build_field_guide(NODE_CLASSES)
    for node_type in NODE_CLASSES:
        assert f"`{node_type}`:" in guide


def test_field_guide_names_the_fields_the_model_kept_leaving_out() -> None:
    lines = {line.split("`")[1]: line for line in build_field_guide(NODE_CLASSES).splitlines()}
    assert "instructions" in lines["agent"] and "model" in lines["agent"]
    assert "piiEnabled (bool)" in lines["guardrails"]
    assert "mcpServerId" in lines["mcp"]
    assert "label" not in lines["agent"]  # passed separately to create_node


def test_field_guide_marks_required_fields_and_literal_choices() -> None:
    lines = {line.split("`")[1]: line for line in build_field_guide(NODE_CLASSES).splitlines()}
    assert "instruction* (text)" in lines["decision"]
    assert "actionOnViolation (block|warn)" in lines["guardrails"]


def test_model_list_lists_ids_or_says_to_leave_unset() -> None:
    assert "`anthropic/claude-sonnet-4-5` (Sonnet)" in describe_models(
        {"anthropic/claude-sonnet-4-5": "Sonnet"}
    )
    assert "leave `model` unset" in describe_models({})


def test_system_prompt_includes_field_guide_and_models() -> None:
    prompt = build_system_prompt("(empty)", {"openai/gpt-5": None})
    assert "piiEnabled" in prompt
    assert "`openai/gpt-5`" in prompt


def test_field_guide_leaves_out_secrets_and_ui_bookkeeping() -> None:
    """Listing credential fields invites the model to invent values for them;
    credentials must come from the user via request_credential."""
    guide = build_field_guide(NODE_CLASSES)
    for hidden in ("apiToken", "vectorDbApiKey", "nodeType"):
        assert hidden not in guide
