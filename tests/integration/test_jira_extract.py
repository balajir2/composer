"""Integration — Start→Jira(operation=extract)→End against the real Jira Cloud API.

Mocked unit tests structurally cannot catch live API drift: this exact
class of regression already bit this project once (Jira removed
/rest/api/3/search — HTTP 410 — in favor of POST /rest/api/3/search/jql
with nextPageToken-based pagination and a string, not array, `expand`
param; both were only discovered via a live run against real
credentials). This test exercises the real HTTP call so that kind of
drift fails a test run instead of surfacing for the first time in
someone's live workflow.
"""

import asyncio
import os
from typing import Any, cast

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def _poll_until_terminal(
    client: AsyncClient,
    execution_id: str,
    timeout: float = 60.0,
) -> dict[str, object]:
    """Poll execution status until terminal state or timeout."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        resp = await client.get(f"/executions/{execution_id}")
        body: dict[str, object] = resp.json()
        if body["status"] in {"completed", "failed"}:
            return body
        await asyncio.sleep(0.3)
    raise AssertionError(f"Execution {execution_id} did not terminate within {timeout}s")


async def test_jira_extract_against_real_api(client: AsyncClient) -> None:
    """Jira extract operation against a real Jira Cloud instance.

    Skips if JIRA_TEST_DOMAIN, JIRA_TEST_EMAIL, or JIRA_TEST_API_TOKEN is
    unset. JIRA_TEST_JQL is optional -- defaults to a project-agnostic
    query (no project/board/label reference) so this runs against any
    Jira Cloud instance out of the box; override it to target a specific
    project if the default query would return zero issues on the
    account being tested against.
    """
    domain = os.environ.get("JIRA_TEST_DOMAIN")
    email = os.environ.get("JIRA_TEST_EMAIL")
    api_token = os.environ.get("JIRA_TEST_API_TOKEN")
    if not domain:
        pytest.skip("JIRA_TEST_DOMAIN not set")
    if not email:
        pytest.skip("JIRA_TEST_EMAIL not set")
    if not api_token:
        pytest.skip("JIRA_TEST_API_TOKEN not set")
    jql = os.environ.get("JIRA_TEST_JQL", "ORDER BY created DESC")

    wf_payload = {
        "name": "Jira extract live-API smoke",
        "nodes": [
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {
                "id": "j",
                "type": "jira",
                "position": {"x": 100, "y": 0},
                "data": {
                    "label": "Jira",
                    "domain": domain,
                    "email": email,
                    "apiToken": api_token,
                    "operation": "extract",
                    "jql": jql,
                    "fields": ["summary", "status", "issuetype"],
                    "expandChangelog": False,
                    "maxIssues": 5,
                },
            },
            {"id": "e", "type": "end", "position": {"x": 200, "y": 0}, "data": {"label": "E"}},
        ],
        "edges": [
            {"id": "e1", "source": "s", "target": "j"},
            {"id": "e2", "source": "j", "target": "e"},
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
    # A status of "completed" here is the actual regression guard: both
    # historical bugs (the removed /search endpoint returning HTTP 410,
    # and sending `expand` as an array instead of a string returning
    # HTTP 400) would have raised inside the executor and failed the
    # execution outright, not merely returned a different value.
    assert final["status"] == "completed", f"Got: {final}"

    output = final.get("output")
    assert isinstance(output, dict), f"Expected a dict output, got: {output!r}"
    output_typed = cast("dict[str, Any]", output)
    issues = output_typed.get("issues")
    assert isinstance(issues, list), f"Expected output['issues'] to be a list, got: {issues!r}"
    issues_list = cast("list[dict[str, Any]]", issues)
    for issue in issues_list:
        assert isinstance(issue, dict)
        assert "key" in issue
        assert "fields" in issue
