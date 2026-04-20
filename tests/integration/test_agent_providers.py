"""Integration — Start→Agent→End against real LLM providers.

Gracefully skips providers whose API key is unset. Each test makes
one LLM call per run — cost is a few cents total across all providers.
"""

import asyncio
import os

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def _poll_until_terminal(
    client: AsyncClient,
    execution_id: str,
    timeout: float = 30.0,
) -> dict[str, object]:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        resp = await client.get(f"/executions/{execution_id}")
        body: dict[str, object] = resp.json()
        if body["status"] in {"completed", "failed"}:
            return body
        await asyncio.sleep(0.2)
    raise AssertionError(f"Execution {execution_id} did not terminate within {timeout}s")


@pytest.mark.parametrize(
    "provider,model,key_env",
    [
        ("anthropic", "anthropic/claude-haiku-4-5-20251001", "ANTHROPIC_API_KEY"),
        ("openai", "openai/gpt-5-nano", "OPENAI_API_KEY"),
        ("google", "google/gemini-2.5-flash", "GOOGLE_API_KEY"),
        ("groq", "groq/llama-3.3-70b-versatile", "GROQ_API_KEY"),
    ],
)
async def test_start_agent_end_real_provider(
    client: AsyncClient,
    provider: str,
    model: str,
    key_env: str,
) -> None:
    """Start→Agent→End against a real LLM. Skips if the provider key is unset."""
    if not os.environ.get(key_env):
        pytest.skip(f"{key_env} not set — skipping {provider} integration test")

    wf_payload = {
        "name": f"Phase-2 smoke {provider}",
        "nodes": [
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {
                "id": "a",
                "type": "agent",
                "position": {"x": 100, "y": 0},
                "data": {
                    "label": "Agent",
                    "model": model,
                    "instructions": "Reply with exactly the word 'hello'.",
                    "outputFormat": "Text",
                },
            },
            {"id": "e", "type": "end", "position": {"x": 200, "y": 0}, "data": {"label": "E"}},
        ],
        "edges": [
            {"id": "e1", "source": "s", "target": "a"},
            {"id": "e2", "source": "a", "target": "e"},
        ],
    }
    create = await client.post("/workflows", json=wf_payload)
    assert create.status_code == 201, create.text
    workflow_id = create.json()["id"]

    start = await client.post(
        "/executions",
        json={"workflowId": workflow_id, "input": ""},
    )
    assert start.status_code == 202, start.text

    final = await _poll_until_terminal(client, start.json()["id"])
    assert final["status"] == "completed", f"Got: {final}"
    output = final.get("output")
    assert isinstance(output, str) and len(output) > 0
