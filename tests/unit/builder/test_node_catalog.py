from src.builder.node_catalog import NODE_PURPOSES, build_node_catalog

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
