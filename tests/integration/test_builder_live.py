"""Live quality check for "Build with AI" against the real default model.

Runs weekly via live-provider-smoke.yml (`-m live`), never per PR. Each case
is a realistic request; the result must have no duplicate edges, pass
structural validation and pass every completeness check. A failure means the
builder ships half-filled workflows with the current prompt/model.
"""

import os

import pytest

from src.builder.builder_agent import BuildSession, run_builder_turn
from src.builder.completeness import collect_completeness_issues
from src.builder.model_policy import DEFAULT_BUILDER_MODEL
from src.engine.graph_builder import collect_workflow_violations
from src.engine.workflow import (
    EndNode,
    EndNodeData,
    Position,
    StartNode,
    StartNodeData,
    Workflow,
    WorkflowEdge,
)
from src.llm.providers import build_chat_model

pytestmark = [pytest.mark.integration, pytest.mark.live]

CATALOG = {DEFAULT_BUILDER_MODEL: "Default", "anthropic/claude-haiku-4-5-20251001": "Haiku"}

REQUESTS = [
    "Take a topic from the user, research it with an agent and write a 200-word summary",
    "Check the user's text for PII and moderation problems, then rewrite it politely",
    "Search the web for a topic using a Tavily MCP server and summarise the findings",
    "Classify a support ticket as billing or technical and draft a reply for each case",
    "Extract the company name and amount from an invoice text and ask a human to approve",
]


def _canvas() -> Workflow:
    return Workflow(
        name="live",
        nodes=[
            StartNode(
                id="start-1",
                type="start",
                position=Position(x=0, y=0),
                data=StartNodeData(label="Start"),
            ),
            EndNode(
                id="end-1",
                type="end",
                position=Position(x=400, y=0),
                data=EndNodeData(label="End"),
            ),
        ],
        edges=[WorkflowEdge(id="e1", source="start-1", target="end-1")],
    )


@pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY not set")
@pytest.mark.parametrize("request_text", REQUESTS)
async def test_builder_produces_a_complete_workflow(request_text: str) -> None:
    model = build_chat_model(DEFAULT_BUILDER_MODEL)
    session = BuildSession(workflow_id="live")
    workflow = _canvas()
    events: list[dict[str, object]] = []
    for message in (request_text, "approve the plan"):
        turn_events, _, workflow = await run_builder_turn(
            model=model,
            workflow_id="live",
            workflow=workflow,
            session=session,
            message=message,
            execution_id=None,
            db=None,
            user_id="u1",
            model_catalog=CATALOG,
        )
        events.extend(turn_events)

    requested = {str(e.get("node_id")) for e in events if e.get("type") == "credential_request"}
    pairs = [(e.source, e.target, e.branch) for e in workflow.edges]
    assert len(pairs) == len(set(pairs)), f"duplicate edges: {pairs}"
    assert collect_workflow_violations(workflow) == []
    assert (
        collect_completeness_issues(workflow, model_catalog=CATALOG, credential_requested=requested)
        == []
    )
