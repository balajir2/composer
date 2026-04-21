"""Tests for Workflow / WorkflowEdge / WorkflowNode Pydantic models."""

import pytest
from pydantic import ValidationError

from src.engine.workflow import (
    EndNode,
    Position,
    StartNode,
    Workflow,
    WorkflowEdge,
)


def test_position_accepts_numeric_coords() -> None:
    p = Position(x=10.5, y=-3)
    assert p.x == 10.5
    assert p.y == -3.0


def test_start_node_parses_minimal() -> None:
    n = StartNode.model_validate(
        {
            "id": "n1",
            "type": "start",
            "position": {"x": 0, "y": 0},
            "data": {"label": "Start"},
        }
    )
    assert n.id == "n1"
    assert n.type == "start"
    assert n.data.label == "Start"
    assert n.data.input_variables == []


def test_start_node_accepts_input_variables_camelcase() -> None:
    n = StartNode.model_validate(
        {
            "id": "n1",
            "type": "start",
            "position": {"x": 0, "y": 0},
            "data": {
                "label": "Start",
                "inputVariables": [
                    {
                        "name": "user_message",
                        "type": "string",
                        "required": True,
                        "description": "The user's input",
                    }
                ],
            },
        }
    )
    assert len(n.data.input_variables) == 1
    assert n.data.input_variables[0].name == "user_message"
    assert n.data.input_variables[0].required is True


def test_end_node_parses_minimal() -> None:
    n = EndNode.model_validate(
        {
            "id": "n2",
            "type": "end",
            "position": {"x": 200, "y": 0},
            "data": {"label": "End"},
        }
    )
    assert n.type == "end"


def test_edge_accepts_alias_sourceHandle() -> None:
    e = WorkflowEdge.model_validate(
        {"id": "e1", "source": "n1", "target": "n2", "sourceHandle": "if"}
    )
    assert e.source_handle == "if"


def test_workflow_parses_start_to_end() -> None:
    wf = Workflow.model_validate(
        {
            "name": "Smoke",
            "nodes": [
                {
                    "id": "n1",
                    "type": "start",
                    "position": {"x": 0, "y": 0},
                    "data": {"label": "Start"},
                },
                {
                    "id": "n2",
                    "type": "end",
                    "position": {"x": 200, "y": 0},
                    "data": {"label": "End"},
                },
            ],
            "edges": [{"id": "e1", "source": "n1", "target": "n2"}],
        }
    )
    assert wf.name == "Smoke"
    assert len(wf.nodes) == 2
    assert wf.nodes[0].type == "start"
    assert wf.nodes[1].type == "end"


def test_workflow_rejects_unknown_node_type() -> None:
    with pytest.raises(ValidationError):
        Workflow.model_validate(
            {
                "name": "Bad",
                "nodes": [
                    {
                        "id": "n1",
                        "type": "nope",
                        "position": {"x": 0, "y": 0},
                        "data": {"label": "?"},
                    },
                ],
                "edges": [],
            }
        )


def test_workflow_round_trip_preserves_camelcase_aliases() -> None:
    """parse(camelCase JSON) -> dump(by_alias=True) -> re-parse preserves all fields.

    Guards against silent alias breakage when Task 4 adds 16 more node types -
    any missed `alias=` on a new field shows up here first.
    """
    original = {
        "name": "RoundTrip",
        "userId": "user-42",
        "estimatedTime": "5m",
        "isTemplate": True,
        "isPublic": False,
        "createdAt": "2026-04-20T00:00:00Z",
        "nodes": [
            {
                "id": "n1",
                "type": "start",
                "position": {"x": 0, "y": 0},
                "data": {
                    "label": "Start",
                    "nodeType": "custom",
                    "nodeName": "MyStart",
                    "inputVariables": [
                        {
                            "name": "x",
                            "type": "string",
                            "required": True,
                            "description": "first arg",
                            "defaultValue": "hello",
                        }
                    ],
                },
            },
            {
                "id": "n2",
                "type": "end",
                "position": {"x": 200, "y": 0},
                "data": {"label": "End"},
            },
        ],
        "edges": [{"id": "e1", "source": "n1", "target": "n2", "sourceHandle": "out"}],
    }
    parsed = Workflow.model_validate(original)
    dumped = parsed.model_dump(by_alias=True, exclude_none=True)
    reparsed = Workflow.model_validate(dumped)

    assert reparsed.name == "RoundTrip"
    assert reparsed.user_id == "user-42"
    assert reparsed.estimated_time == "5m"
    assert reparsed.is_template is True
    assert reparsed.edges[0].source_handle == "out"

    # Narrow the discriminated-union node to StartNode so pyright sees
    # StartNodeData and the inputVariables field.
    start_node = reparsed.nodes[0]
    assert isinstance(start_node, StartNode)
    assert start_node.data.node_name == "MyStart"
    assert start_node.data.input_variables[0].default_value == "hello"


ALL_NODE_TYPES = [
    "start",
    "end",
    "note",
    "agent",
    "mcp",
    "if-else",
    "while",
    "user-approval",
    "transform",
    "data-transform",
    "set-state",
    "extract",
    "http",
    "guardrails",
    "vector-db",
    "gamma-ai",
    "arcade",
    "join-chunks",
]


# Minimal data overrides for node types that have required fields beyond `label`.
_MINIMAL_DATA_OVERRIDES: dict[str, dict[str, object]] = {
    "join-chunks": {"label": "X", "joinChunksVariable": "chunks"},
    "arcade": {"label": "X", "arcadeTool": "X@1"},
}


@pytest.mark.parametrize("node_type", ALL_NODE_TYPES)
def test_every_node_type_parses_minimal_instance(node_type: str) -> None:
    node_data = _MINIMAL_DATA_OVERRIDES.get(node_type, {"label": "X"})
    wf = Workflow.model_validate(
        {
            "name": "T",
            "nodes": [
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                {
                    "id": "x",
                    "type": node_type,
                    "position": {"x": 100, "y": 0},
                    "data": node_data,
                },
                {"id": "e", "type": "end", "position": {"x": 200, "y": 0}, "data": {"label": "E"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "x"},
                {"id": "e2", "source": "x", "target": "e"},
            ],
        }
    )
    # start, x, end — verify the middle node parsed with the requested type
    assert wf.nodes[1].type == node_type


def test_all_18_types_exhaustive() -> None:
    assert len(ALL_NODE_TYPES) == 18


def test_workflow_edge_branch_defaults_none() -> None:
    from src.engine.workflow import WorkflowEdge

    edge = WorkflowEdge.model_validate({"id": "e1", "source": "a", "target": "b"})
    assert edge.branch is None


def test_workflow_edge_branch_accepts_string() -> None:
    from src.engine.workflow import WorkflowEdge

    edge = WorkflowEdge.model_validate({"id": "e1", "source": "x", "target": "y", "branch": "true"})
    assert edge.branch == "true"


def test_workflow_edge_branch_round_trip_json() -> None:
    from src.engine.workflow import WorkflowEdge

    edge = WorkflowEdge.model_validate({"id": "e1", "source": "x", "target": "y", "branch": "body"})
    dumped = edge.model_dump(mode="json")
    assert dumped["branch"] == "body"


def test_join_chunks_node_data_parses_camelcase_aliases() -> None:
    from src.engine.workflow import JoinChunksNodeData

    data = JoinChunksNodeData.model_validate(
        {
            "label": "JC",
            "joinChunksVariable": "chunks",
            "joinChunksSeparator": "\n---\n",
            "joinChunksPrefix": "> ",
            "joinChunksSuffix": " <",
            "joinChunksIncludeMetadata": True,
        }
    )
    assert data.variable == "chunks"
    assert data.separator == "\n---\n"
    assert data.prefix == "> "
    assert data.suffix == " <"
    assert data.include_metadata is True


def test_join_chunks_node_data_defaults() -> None:
    from src.engine.workflow import JoinChunksNodeData

    data = JoinChunksNodeData.model_validate(
        {
            "label": "JC",
            "joinChunksVariable": "chunks",
        }
    )
    assert data.variable == "chunks"
    assert data.separator == "\n\n"
    assert data.prefix == ""
    assert data.suffix == ""
    assert data.include_metadata is False


def test_join_chunks_node_data_missing_variable_raises() -> None:
    from pydantic import ValidationError

    from src.engine.workflow import JoinChunksNodeData

    with pytest.raises(ValidationError):
        JoinChunksNodeData.model_validate({"label": "JC"})


def test_join_chunks_node_full_round_trip() -> None:
    from src.engine.workflow import JoinChunksNode

    node = JoinChunksNode.model_validate(
        {
            "id": "jc1",
            "type": "join-chunks",
            "position": {"x": 0, "y": 0},
            "data": {
                "label": "JC",
                "joinChunksVariable": "chunks",
            },
        }
    )
    assert node.id == "jc1"
    assert node.type == "join-chunks"
    assert node.data.variable == "chunks"


def test_guardrails_node_data_parses_camelcase_aliases() -> None:
    from src.engine.workflow import GuardrailsNodeData

    data = GuardrailsNodeData.model_validate(
        {
            "label": "GR",
            "piiEnabled": True,
            "moderationEnabled": True,
            "jailbreakEnabled": False,
            "hallucinationEnabled": False,
            "actionOnViolation": "block",
            "model": "claude-haiku-4-5-20251001",
        }
    )
    assert data.pii_enabled is True
    assert data.moderation_enabled is True
    assert data.jailbreak_enabled is False
    assert data.hallucination_enabled is False
    assert data.action_on_violation == "block"
    assert data.model == "claude-haiku-4-5-20251001"


def test_guardrails_node_data_defaults() -> None:
    from src.engine.workflow import GuardrailsNodeData

    data = GuardrailsNodeData.model_validate({"label": "GR"})
    assert data.pii_enabled is False
    assert data.moderation_enabled is False
    assert data.jailbreak_enabled is False
    assert data.hallucination_enabled is False
    assert data.action_on_violation == "warn"
    assert data.model is None


def test_guardrails_node_data_invalid_action_raises() -> None:
    from pydantic import ValidationError

    from src.engine.workflow import GuardrailsNodeData

    with pytest.raises(ValidationError):
        GuardrailsNodeData.model_validate(
            {
                "label": "GR",
                "actionOnViolation": "nuke",
            }
        )


def test_guardrails_node_full_round_trip() -> None:
    from src.engine.workflow import GuardrailsNode

    node = GuardrailsNode.model_validate(
        {
            "id": "g1",
            "type": "guardrails",
            "position": {"x": 0, "y": 0},
            "data": {"label": "GR", "piiEnabled": True},
        }
    )
    assert node.id == "g1"
    assert node.type == "guardrails"
    assert node.data.pii_enabled is True


def test_gamma_ai_node_data_parses_camelcase_aliases() -> None:
    from src.engine.workflow import GammaAiNodeData

    data = GammaAiNodeData.model_validate(
        {
            "label": "GA",
            "prompt": "Make a deck about {{topic}}",
            "format": "presentation",
            "textMode": "generate",
            "numCards": 8,
            "textAmount": "medium",
            "imageSource": "unsplash",
            "language": "en",
            "exportAs": "pptx",
        }
    )
    assert data.prompt == "Make a deck about {{topic}}"
    assert data.format == "presentation"
    assert data.text_mode == "generate"
    assert data.num_cards == 8
    assert data.text_amount == "medium"
    assert data.image_source == "unsplash"
    assert data.language == "en"
    assert data.export_as == "pptx"


def test_gamma_ai_node_data_defaults() -> None:
    from src.engine.workflow import GammaAiNodeData

    data = GammaAiNodeData.model_validate({"label": "GA"})
    assert data.prompt is None
    assert data.format == "presentation"
    assert data.text_mode == "generate"
    assert data.num_cards is None
    assert data.text_amount is None
    assert data.image_source is None
    assert data.language is None
    assert data.export_as == "web"


def test_gamma_ai_node_data_invalid_format_raises() -> None:
    import pytest
    from pydantic import ValidationError

    from src.engine.workflow import GammaAiNodeData

    with pytest.raises(ValidationError):
        GammaAiNodeData.model_validate({"label": "GA", "format": "bogus"})


def test_gamma_ai_node_full_round_trip() -> None:
    from src.engine.workflow import GammaAiNode

    node = GammaAiNode.model_validate(
        {
            "id": "ga1",
            "type": "gamma-ai",
            "position": {"x": 0, "y": 0},
            "data": {"label": "GA", "prompt": "Hello"},
        }
    )
    assert node.id == "ga1"
    assert node.type == "gamma-ai"
    assert node.data.prompt == "Hello"


def test_arcade_node_data_parses_camelcase_aliases() -> None:
    from src.engine.workflow import ArcadeNodeData

    data = ArcadeNodeData.model_validate(
        {
            "label": "AR",
            "arcadeTool": "GoogleDocs.CreateDocumentFromText@4.3.1",
            "arcadeInput": {"title": "Hello", "body": "World"},
            "arcadeUserId": "alice@example.com",
        }
    )
    assert data.tool == "GoogleDocs.CreateDocumentFromText@4.3.1"
    assert data.input == {"title": "Hello", "body": "World"}
    assert data.user_id == "alice@example.com"


def test_arcade_node_data_defaults() -> None:
    from src.engine.workflow import ArcadeNodeData

    data = ArcadeNodeData.model_validate(
        {
            "label": "AR",
            "arcadeTool": "Slack.SendMessage@1.0.0",
        }
    )
    assert data.tool == "Slack.SendMessage@1.0.0"
    assert data.input == {}
    assert data.user_id == "workflow-builder"


def test_arcade_node_data_missing_tool_raises() -> None:
    from pydantic import ValidationError

    from src.engine.workflow import ArcadeNodeData

    with pytest.raises(ValidationError):
        ArcadeNodeData.model_validate({"label": "AR"})


def test_arcade_node_full_round_trip() -> None:
    from src.engine.workflow import ArcadeNode

    node = ArcadeNode.model_validate(
        {
            "id": "ar1",
            "type": "arcade",
            "position": {"x": 0, "y": 0},
            "data": {"label": "AR", "arcadeTool": "Tool@1"},
        }
    )
    assert node.id == "ar1"
    assert node.type == "arcade"
    assert node.data.tool == "Tool@1"


def test_vector_db_node_data_parses_camelcase_aliases() -> None:
    from src.engine.workflow import VectorDbNodeData

    data = VectorDbNodeData.model_validate(
        {
            "label": "VDB",
            "vectorDbProvider": "qdrant",
            "vectorDbEndpoint": "http://localhost:6333",
            "vectorDbApiKey": "{{env.QDRANT_API_KEY}}",
            "vectorDbCollection": "docs",
            "vectorDbDimension": 768,
            "vectorDbEmbeddingProvider": "openai",
            "vectorDbEmbeddingModel": "text-embedding-3-large",
            "vectorDbQueryPrompt": "find {{topic}}",
            "vectorDbTopK": 10,
            "vectorDbScoreThreshold": 0.7,
            "vectorDbNamespace": "ns1",
            "vectorDbIncludeMetadata": False,
            "vectorDbIncludeVector": True,
            "vectorDbTextField": "page_content",
            "vectorDbOutputVariable": "hits",
            "vectorDbMetadataFilter": '{"category":"docs"}',
            "vectorDbJoinResults": True,
            "vectorDbJoinSeparator": "\n---\n",
            "vectorDbJoinPrefix": "> ",
            "vectorDbJoinSuffix": " <",
        }
    )
    assert data.provider == "qdrant"
    assert data.endpoint == "http://localhost:6333"
    assert data.api_key == "{{env.QDRANT_API_KEY}}"
    assert data.collection == "docs"
    assert data.dimension == 768
    assert data.embedding_provider == "openai"
    assert data.embedding_model == "text-embedding-3-large"
    assert data.query_prompt == "find {{topic}}"
    assert data.top_k == 10
    assert data.score_threshold == 0.7
    assert data.namespace == "ns1"
    assert data.include_metadata is False
    assert data.include_vector is True
    assert data.text_field == "page_content"
    assert data.output_variable == "hits"
    assert data.metadata_filter == '{"category":"docs"}'
    assert data.join_results is True
    assert data.join_separator == "\n---\n"
    assert data.join_prefix == "> "
    assert data.join_suffix == " <"


def test_vector_db_node_data_defaults() -> None:
    from src.engine.workflow import VectorDbNodeData

    data = VectorDbNodeData.model_validate({"label": "VDB"})
    assert data.provider == "pinecone"
    assert data.endpoint == ""
    assert data.api_key == ""
    assert data.collection == ""
    assert data.dimension == 1536
    assert data.embedding_provider == "openai"
    assert data.embedding_model == "text-embedding-3-small"
    assert data.query_prompt == ""
    assert data.top_k == 5
    assert data.score_threshold == 0.0
    assert data.namespace is None
    assert data.include_metadata is True
    assert data.include_vector is False
    assert data.text_field is None
    assert data.output_variable == "vectorDbResults"
    assert data.metadata_filter is None
    assert data.join_results is False


def test_vector_db_node_data_invalid_provider_raises() -> None:
    import pytest
    from pydantic import ValidationError

    from src.engine.workflow import VectorDbNodeData

    with pytest.raises(ValidationError):
        VectorDbNodeData.model_validate({"label": "VDB", "vectorDbProvider": "bogus"})


def test_vector_db_node_full_round_trip() -> None:
    from src.engine.workflow import VectorDbNode

    node = VectorDbNode.model_validate(
        {
            "id": "vdb1",
            "type": "vector-db",
            "position": {"x": 0, "y": 0},
            "data": {"label": "VDB"},
        }
    )
    assert node.id == "vdb1"
    assert node.type == "vector-db"
    assert node.data.provider == "pinecone"
