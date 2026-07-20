"""Tests for JiraProvider."""

import base64

import pytest
from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]

from src.config import get_settings
from src.engine.state import initial_state
from src.engine.workflow import AgentNode
from src.tools.base import ApiKeyAuth, BuildContext
from src.tools.providers.jira import JiraProvider


def _agent_node() -> AgentNode:
    return AgentNode.model_validate(
        {
            "id": "a",
            "type": "agent",
            "position": {"x": 0, "y": 0},
            "data": {"label": "A"},
        }
    )


def _set_jira_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JIRA_DOMAIN", "test.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "test@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "test-token")
    get_settings.cache_clear()


def _auth_header() -> str:
    return f"Basic {base64.b64encode(b'test@example.com:test-token').decode()}"


def test_provider_metadata() -> None:
    p = JiraProvider()
    assert p.name == "jira"
    assert p.category == "standard"
    assert isinstance(p.auth, ApiKeyAuth)
    assert p.auth.env_var == "JIRA_API_TOKEN"


async def test_tools_enumerates_six() -> None:
    tools = await JiraProvider().tools()
    names = {t.name for t in tools}
    assert names == {
        "jira_create_issue",
        "jira_get_issue",
        "jira_search_issues",
        "jira_update_issue",
        "jira_transition_issue",
        "jira_add_comment",
    }


async def test_build_tool_requires_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JIRA_DOMAIN", "")
    monkeypatch.setenv("JIRA_EMAIL", "")
    monkeypatch.setenv("JIRA_API_TOKEN", "")
    get_settings.cache_clear()
    node = _agent_node()
    tool = await JiraProvider().build_tool(
        "jira_get_issue",
        BuildContext(node=node, state=initial_state()),
    )
    result = await tool._arun(issue_key="PROJ-1")  # pyright: ignore[reportPrivateUsage]
    assert result.startswith("Error:")


async def test_build_tool_unknown_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_jira_env(monkeypatch)
    node = _agent_node()
    with pytest.raises(ValueError, match="no tool named"):
        await JiraProvider().build_tool(
            "ghost_tool",
            BuildContext(node=node, state=initial_state()),
        )


async def test_create_issue(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    _set_jira_env(monkeypatch)
    httpx_mock.add_response(
        url="https://test.atlassian.net/rest/api/3/issue",
        method="POST",
        json={"id": "10000", "key": "PROJ-1"},
    )
    node = _agent_node()
    tool = await JiraProvider().build_tool(
        "jira_create_issue",
        BuildContext(node=node, state=initial_state()),
    )
    result = await tool._arun(  # pyright: ignore[reportPrivateUsage]
        project_key="PROJ", summary="Test issue"
    )
    assert "PROJ-1" in result

    # Verify auth header
    requests = httpx_mock.get_requests()
    assert len(requests) == 1
    assert requests[0].headers["Authorization"] == _auth_header()


async def test_get_issue(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    _set_jira_env(monkeypatch)
    httpx_mock.add_response(
        url="https://test.atlassian.net/rest/api/3/issue/PROJ-1?fields=summary,status,assignee,priority,description,issuetype,labels",
        method="GET",
        json={
            "key": "PROJ-1",
            "fields": {
                "summary": "Test issue",
                "status": {"name": "Open"},
                "assignee": {"displayName": "John Doe"},
                "priority": {"name": "High"},
                "issuetype": {"name": "Bug"},
                "labels": ["urgent"],
                "description": {
                    "content": [
                        {"content": [{"text": "Desc", "type": "text"}], "type": "paragraph"}
                    ]
                },
            },
        },
    )
    node = _agent_node()
    tool = await JiraProvider().build_tool(
        "jira_get_issue",
        BuildContext(node=node, state=initial_state()),
    )
    result = await tool._arun(issue_key="PROJ-1")  # pyright: ignore[reportPrivateUsage]
    assert "PROJ-1" in result
    assert "Test issue" in result
    assert "John Doe" in result
    assert "High" in result


async def test_search_issues(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    _set_jira_env(monkeypatch)
    httpx_mock.add_response(
        url="https://test.atlassian.net/rest/api/3/search/jql",
        method="POST",
        json={
            "issues": [
                {
                    "key": "PROJ-1",
                    "fields": {
                        "summary": "Test",
                        "status": {"name": "Open"},
                        "priority": {"name": "Medium"},
                        "assignee": {"displayName": "Jane"},
                    },
                }
            ],
        },
    )
    node = _agent_node()
    tool = await JiraProvider().build_tool(
        "jira_search_issues",
        BuildContext(node=node, state=initial_state()),
    )
    result = await tool._arun(jql="project=PROJ")  # pyright: ignore[reportPrivateUsage]
    assert "PROJ-1" in result
    assert "Test" in result


async def test_update_issue(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    _set_jira_env(monkeypatch)
    httpx_mock.add_response(
        url="https://test.atlassian.net/rest/api/3/issue/PROJ-1",
        method="PUT",
        status_code=204,
    )
    node = _agent_node()
    tool = await JiraProvider().build_tool(
        "jira_update_issue",
        BuildContext(node=node, state=initial_state()),
    )
    result = await tool._arun(  # pyright: ignore[reportPrivateUsage]
        issue_key="PROJ-1", fields='{"summary": "Updated"}'
    )
    assert "Updated" in result


async def test_transition_issue_by_id(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    _set_jira_env(monkeypatch)
    httpx_mock.add_response(
        url="https://test.atlassian.net/rest/api/3/issue/PROJ-1/transitions",
        method="POST",
        status_code=204,
    )
    node = _agent_node()
    tool = await JiraProvider().build_tool(
        "jira_transition_issue",
        BuildContext(node=node, state=initial_state()),
    )
    result = await tool._arun(  # pyright: ignore[reportPrivateUsage]
        issue_key="PROJ-1", transition_id="11"
    )
    assert "PROJ-1" in result


async def test_transition_issue_by_name(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    _set_jira_env(monkeypatch)
    httpx_mock.add_response(
        url="https://test.atlassian.net/rest/api/3/issue/PROJ-1/transitions",
        method="GET",
        json={"transitions": [{"id": "11", "name": "In Progress"}]},
    )
    httpx_mock.add_response(
        url="https://test.atlassian.net/rest/api/3/issue/PROJ-1/transitions",
        method="POST",
        status_code=204,
    )
    node = _agent_node()
    tool = await JiraProvider().build_tool(
        "jira_transition_issue",
        BuildContext(node=node, state=initial_state()),
    )
    result = await tool._arun(  # pyright: ignore[reportPrivateUsage]
        issue_key="PROJ-1", transition_name="In Progress"
    )
    assert "PROJ-1" in result


async def test_add_comment(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    _set_jira_env(monkeypatch)
    httpx_mock.add_response(
        url="https://test.atlassian.net/rest/api/3/issue/PROJ-1/comment",
        method="POST",
        json={"id": "20000"},
    )
    node = _agent_node()
    tool = await JiraProvider().build_tool(
        "jira_add_comment",
        BuildContext(node=node, state=initial_state()),
    )
    result = await tool._arun(  # pyright: ignore[reportPrivateUsage]
        issue_key="PROJ-1", body="Looks good"
    )
    assert "PROJ-1" in result


async def test_health_check_no_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JIRA_DOMAIN", "")
    monkeypatch.setenv("JIRA_EMAIL", "")
    monkeypatch.setenv("JIRA_API_TOKEN", "")
    get_settings.cache_clear()
    status = await JiraProvider().health_check()
    assert status.ok is False


async def test_health_check_with_config(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    _set_jira_env(monkeypatch)
    httpx_mock.add_response(
        url="https://test.atlassian.net/rest/api/3/myself",
        method="GET",
        json={"displayName": "Test User"},
    )
    status = await JiraProvider().health_check()
    assert status.ok is True
    assert "reachable" in status.message.lower()


async def test_get_issue_http_error(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    _set_jira_env(monkeypatch)
    httpx_mock.add_response(
        url="https://test.atlassian.net/rest/api/3/issue/PROJ-1?fields=summary,status,assignee,priority,description,issuetype,labels",
        method="GET",
        status_code=404,
        text="not found",
    )
    node = _agent_node()
    tool = await JiraProvider().build_tool(
        "jira_get_issue",
        BuildContext(node=node, state=initial_state()),
    )
    result = await tool._arun(issue_key="PROJ-1")  # pyright: ignore[reportPrivateUsage]
    assert result.startswith("Error:")


async def test_update_issue_bad_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_jira_env(monkeypatch)
    node = _agent_node()
    tool = await JiraProvider().build_tool(
        "jira_update_issue",
        BuildContext(node=node, state=initial_state()),
    )
    result = await tool._arun(  # pyright: ignore[reportPrivateUsage]
        issue_key="PROJ-1", fields="not json"
    )
    assert "Error:" in result
    assert "JSON" in result


async def test_transition_no_id_or_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_jira_env(monkeypatch)
    node = _agent_node()
    tool = await JiraProvider().build_tool(
        "jira_transition_issue",
        BuildContext(node=node, state=initial_state()),
    )
    result = await tool._arun(issue_key="PROJ-1")  # pyright: ignore[reportPrivateUsage]
    assert "Error:" in result
