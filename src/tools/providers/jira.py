"""JiraProvider — Jira Cloud REST API v3 tools via Basic auth.

Credentials flow (per-node):
  1. Start-node variables (jira_domain, jira_email, jira_api_token)
  2. Set-state variables before the Agent node
  3. Global env vars (JIRA_DOMAIN / JIRA_EMAIL / JIRA_API_TOKEN) — fallback only

Tools offered:
  jira_create_issue     — Create a new Jira issue
  jira_get_issue        — Fetch a single issue by key
  jira_search_issues    — Search issues with JQL
  jira_update_issue     — Update fields on an existing issue
  jira_transition_issue — Transition an issue through workflow
  jira_add_comment      — Add a comment to an issue
"""

import base64
import json
from typing import Any

import httpx
from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from src.config import get_settings
from src.tools.base import (
    ApiKeyAuth,
    BuildContext,
    HealthStatus,
    ToolDefinition,
    ToolProvider,
)
from src.tools.registry import register_tool_provider


class MissingConfigError(RuntimeError):
    """Raised when no Jira credentials are available in state or env."""


# ─── Auth helpers ──────────────────────────────────────────────────


def _resolve_creds(
    domain: str = "",
    email: str = "",
    api_token: str = "",
) -> tuple[str, str, str]:
    """Return (domain, email, api_token) using passed values, falling back to env."""
    settings = get_settings()
    domain = domain or settings.jira_domain
    email = email or settings.jira_email
    api_token = api_token or settings.jira_api_token
    if not all([domain, email, api_token]):
        raise MissingConfigError(
            "Jira credentials not found. Set jira_domain, jira_email, jira_api_token "
            "as Start-node variables or Set State before this Agent node."
        )
    return domain, email, api_token


def _build_headers(email: str, api_token: str) -> dict[str, str]:
    encoded = base64.b64encode(f"{email}:{api_token}".encode()).decode()
    return {
        "Authorization": f"Basic {encoded}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _build_url(domain: str, path: str) -> str:
    return f"https://{domain}/rest/api/3/{path}"


# ─── Input schemas ─────────────────────────────────────────────────


class JiraCreateIssueInput(BaseModel):
    project_key: str = Field(description="Jira project key (e.g. PROJ)")
    summary: str = Field(description="Issue summary / title")
    description: str | None = Field(default=None, description="Issue description")
    issue_type: str = Field(default="Task", description="Issue type — Task, Bug, Story, Epic")
    priority: str | None = Field(
        default=None, description="Priority — Highest, High, Medium, Low, Lowest"
    )
    labels: str | None = Field(default=None, description="Comma-separated labels")
    assignee: str | None = Field(default=None, description="Assignee account ID")


class JiraGetIssueInput(BaseModel):
    issue_key: str = Field(description="Jira issue key (e.g. PROJ-123)")


class JiraSearchInput(BaseModel):
    jql: str = Field(description="JQL query (e.g. 'project = PROJ AND status = Open')")
    max_results: int = Field(default=50, ge=1, le=200)


class JiraTransitionInput(BaseModel):
    issue_key: str = Field(description="Jira issue key (e.g. PROJ-123)")
    transition_id: str | None = Field(default=None, description="Transition ID (numeric)")
    transition_name: str | None = Field(
        default=None, description="Transition name (e.g. 'In Progress', 'Done')"
    )


class JiraCommentInput(BaseModel):
    issue_key: str = Field(description="Jira issue key (e.g. PROJ-123)")
    body: str = Field(description="Comment body text")


class JiraUpdateIssueInput(BaseModel):
    issue_key: str = Field(description="Jira issue key (e.g. PROJ-123)")
    fields: str = Field(
        description='JSON string of fields to update (e.g. \'{"summary": "new title"}\')'
    )


# ─── Tool classes ──────────────────────────────────────────────────


class _JiraCreateIssueTool(BaseTool):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str = "jira_create_issue"
    description: str = (
        "Create a new issue in Jira. Requires project_key and summary. "
        "Optionally pass description, issue_type, priority, labels, and assignee."
    )
    args_schema: type[BaseModel] = JiraCreateIssueInput  # pyright: ignore[reportIncompatibleVariableOverride]

    _domain: str = ""
    _email: str = ""
    _api_token: str = ""

    def _run(self, **kwargs: Any) -> str:
        raise NotImplementedError("Use _arun — Composer is async-only.")

    async def _arun(
        self,
        project_key: str,
        summary: str,
        description: str | None = None,
        issue_type: str = "Task",
        priority: str | None = None,
        labels: str | None = None,
        assignee: str | None = None,
    ) -> str:
        try:
            domain, email, api_token = _resolve_creds(self._domain, self._email, self._api_token)
        except MissingConfigError as exc:
            return f"Error: {exc}"

        fields: dict[str, Any] = {
            "project": {"key": project_key},
            "summary": summary,
            "issuetype": {"name": issue_type},
        }
        if description:
            fields["description"] = {
                "type": "doc",
                "version": 1,
                "content": [
                    {"type": "paragraph", "content": [{"type": "text", "text": description}]}
                ],
            }
        if priority:
            fields["priority"] = {"name": priority}
        if labels:
            fields["labels"] = [label.strip() for label in labels.split(",") if label.strip()]
        if assignee:
            fields["assignee"] = {"id": assignee}

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
                resp = await client.post(
                    _build_url(domain, "issue"),
                    headers=_build_headers(email, api_token),
                    json={"fields": fields},
                )
        except httpx.HTTPError as exc:
            return f"Error: Jira create issue failed ({type(exc).__name__}): {exc}"

        if resp.status_code >= 400:
            return f"Error: Jira create issue failed (HTTP {resp.status_code}): {resp.text[:500]}"

        data: dict[str, Any] = resp.json()
        key = data.get("key", "unknown")
        return f"Created Jira issue {key}: https://{domain}/browse/{key}"


class _JiraGetIssueTool(BaseTool):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str = "jira_get_issue"
    description: str = (
        "Fetch a Jira issue by its key (e.g. PROJ-123). "
        "Returns summary, status, assignee, and description."
    )
    args_schema: type[BaseModel] = JiraGetIssueInput  # pyright: ignore[reportIncompatibleVariableOverride]

    _domain: str = ""
    _email: str = ""
    _api_token: str = ""

    def _run(self, **kwargs: Any) -> str:
        raise NotImplementedError("Use _arun — Composer is async-only.")

    async def _arun(self, issue_key: str) -> str:
        try:
            domain, email, api_token = _resolve_creds(self._domain, self._email, self._api_token)
        except MissingConfigError as exc:
            return f"Error: {exc}"

        params = "fields=summary,status,assignee,priority,description,issuetype,labels"
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
                resp = await client.get(
                    _build_url(domain, f"issue/{issue_key}?{params}"),
                    headers=_build_headers(email, api_token),
                )
        except httpx.HTTPError as exc:
            return f"Error: Jira get issue failed ({type(exc).__name__}): {exc}"

        if resp.status_code >= 400:
            return f"Error: Jira get issue failed (HTTP {resp.status_code}): {resp.text[:500]}"

        issue: dict[str, Any] = resp.json()
        fields = issue.get("fields", {})
        status_obj = fields.get("status", {})
        assignee_obj: Any = fields.get("assignee") or {}
        issue_type_obj = fields.get("issuetype", {})

        lines = [
            f"## {issue.get('key')}: {fields.get('summary', '')}",
            f"- **Status:** {status_obj.get('name', 'N/A')}",
            f"- **Type:** {issue_type_obj.get('name', 'N/A')}",
            f"- **Priority:** {(fields.get('priority') or {}).get('name', 'N/A')}",
            f"- **Assignee:** {assignee_obj.get('displayName', 'Unassigned')}",
            f"- **Labels:** {', '.join(fields.get('labels', [])) or 'None'}",
        ]
        desc = fields.get("description")
        if desc:
            lines.append("")
            lines.append("### Description")
            if isinstance(desc, dict):
                for item in desc.get("content", []):
                    for inner in item.get("content", []):
                        if inner.get("type") == "text":
                            lines.append(inner.get("text", ""))
            elif isinstance(desc, str):
                lines.append(desc)
            else:
                lines.append(str(desc))

        return "\n".join(lines)


class _JiraSearchIssuesTool(BaseTool):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str = "jira_search_issues"
    description: str = (
        "Search Jira issues using JQL. Returns key, summary, status, and assignee "
        "for each matching issue."
    )
    args_schema: type[BaseModel] = JiraSearchInput  # pyright: ignore[reportIncompatibleVariableOverride]

    _domain: str = ""
    _email: str = ""
    _api_token: str = ""

    def _run(self, **kwargs: Any) -> str:
        raise NotImplementedError("Use _arun — Composer is async-only.")

    async def _arun(self, jql: str, max_results: int = 50) -> str:
        try:
            domain, email, api_token = _resolve_creds(self._domain, self._email, self._api_token)
        except MissingConfigError as exc:
            return f"Error: {exc}"

        body = {
            "jql": jql,
            "maxResults": max_results,
            "fields": ["summary", "status", "assignee", "priority"],
        }
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
                resp = await client.post(
                    _build_url(domain, "search"),
                    headers=_build_headers(email, api_token),
                    json=body,
                )
        except httpx.HTTPError as exc:
            return f"Error: Jira search failed ({type(exc).__name__}): {exc}"

        if resp.status_code >= 400:
            return f"Error: Jira search failed (HTTP {resp.status_code}): {resp.text[:500]}"

        data: dict[str, Any] = resp.json()
        issues = data.get("issues", [])
        total = data.get("total", 0)

        if not issues:
            return f"No issues found for JQL: {jql}"

        lines = [f"# {total} issues matching: {jql}\n"]
        for iss in issues:
            flds = iss.get("fields", {})
            key = iss.get("key", "?")
            summary = flds.get("summary", "")
            status = (flds.get("status") or {}).get("name", "")
            priority = (flds.get("priority") or {}).get("name", "")
            assignee = (flds.get("assignee") or {}).get("displayName", "Unassigned")
            lines.append(f"- [{key}] {summary}  | {status} | {priority} | {assignee}")
        return "\n".join(lines)


class _JiraTransitionIssueTool(BaseTool):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str = "jira_transition_issue"
    description: str = (
        "Transition a Jira issue to a new workflow status. Provide either a "
        "transition_id or transition_name (e.g. 'In Progress', 'Done')."
    )
    args_schema: type[BaseModel] = JiraTransitionInput  # pyright: ignore[reportIncompatibleVariableOverride]

    _domain: str = ""
    _email: str = ""
    _api_token: str = ""

    def _run(self, **kwargs: Any) -> str:
        raise NotImplementedError("Use _arun — Composer is async-only.")

    async def _arun(
        self,
        issue_key: str,
        transition_id: str | None = None,
        transition_name: str | None = None,
    ) -> str:
        try:
            domain, email, api_token = _resolve_creds(self._domain, self._email, self._api_token)
        except MissingConfigError as exc:
            return f"Error: {exc}"

        if not transition_id and not transition_name:
            return "Error: Must provide either transition_id or transition_name"

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
                if transition_id:
                    resp = await client.post(
                        _build_url(domain, f"issue/{issue_key}/transitions"),
                        headers=_build_headers(email, api_token),
                        json={"transition": {"id": transition_id}},
                    )
                else:
                    trans_resp = await client.get(
                        _build_url(domain, f"issue/{issue_key}/transitions"),
                        headers=_build_headers(email, api_token),
                    )
                    if trans_resp.status_code >= 400:
                        return f"Error: Failed to fetch transitions (HTTP {trans_resp.status_code})"

                    transitions = trans_resp.json().get("transitions", [])
                    match = next(
                        (
                            t
                            for t in transitions
                            if str(t.get("name", "")).lower() == str(transition_name).lower()
                        ),
                        None,
                    )
                    if not match:
                        available = ", ".join(
                            f"{t.get('name')} (id={t.get('id')})" for t in transitions
                        )
                        return (
                            f"Error: No transition named '{transition_name}'. "
                            f"Available: {available}"
                        )

                    resp = await client.post(
                        _build_url(domain, f"issue/{issue_key}/transitions"),
                        headers=_build_headers(email, api_token),
                        json={"transition": {"id": match["id"]}},
                    )
        except httpx.HTTPError as exc:
            return f"Error: Jira transition failed ({type(exc).__name__}): {exc}"

        if resp.status_code >= 400:
            return f"Error: Jira transition failed (HTTP {resp.status_code}): {resp.text[:500]}"

        name = transition_name or f"id={transition_id}"
        return f"Transitioned issue {issue_key} to '{name}'"


class _JiraAddCommentTool(BaseTool):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str = "jira_add_comment"
    description: str = "Add a comment to a Jira issue."
    args_schema: type[BaseModel] = JiraCommentInput  # pyright: ignore[reportIncompatibleVariableOverride]

    _domain: str = ""
    _email: str = ""
    _api_token: str = ""

    def _run(self, **kwargs: Any) -> str:
        raise NotImplementedError("Use _arun — Composer is async-only.")

    async def _arun(self, issue_key: str, body: str) -> str:
        try:
            domain, email, api_token = _resolve_creds(self._domain, self._email, self._api_token)
        except MissingConfigError as exc:
            return f"Error: {exc}"

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
                resp = await client.post(
                    _build_url(domain, f"issue/{issue_key}/comment"),
                    headers=_build_headers(email, api_token),
                    json={"body": body},
                )
        except httpx.HTTPError as exc:
            return f"Error: Jira add comment failed ({type(exc).__name__}): {exc}"

        if resp.status_code >= 400:
            return f"Error: Jira add comment failed (HTTP {resp.status_code}): {resp.text[:500]}"

        return f"Comment added to issue {issue_key}"


class _JiraUpdateIssueTool(BaseTool):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str = "jira_update_issue"
    description: str = (
        "Update fields on a Jira issue. Pass the issue_key and a JSON string of "
        'fields to update. Example fields: {"summary": "New title"}.'
    )
    args_schema: type[BaseModel] = JiraUpdateIssueInput  # pyright: ignore[reportIncompatibleVariableOverride]

    _domain: str = ""
    _email: str = ""
    _api_token: str = ""

    def _run(self, **kwargs: Any) -> str:
        raise NotImplementedError("Use _arun — Composer is async-only.")

    async def _arun(self, issue_key: str, fields: str) -> str:
        try:
            domain, email, api_token = _resolve_creds(self._domain, self._email, self._api_token)
        except MissingConfigError as exc:
            return f"Error: {exc}"

        try:
            fields_dict = json.loads(fields)
        except json.JSONDecodeError as exc:
            return f"Error: Invalid JSON in fields parameter: {exc}"

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
                resp = await client.put(
                    _build_url(domain, f"issue/{issue_key}"),
                    headers=_build_headers(email, api_token),
                    json={"fields": fields_dict},
                )
        except httpx.HTTPError as exc:
            return f"Error: Jira update issue failed ({type(exc).__name__}): {exc}"

        if resp.status_code >= 400:
            return f"Error: Jira update issue failed (HTTP {resp.status_code}): {resp.text[:500]}"

        return f"Updated issue {issue_key}"


# ─── Provider ─────────────────────────────────────────────────────

_TOOLS: dict[str, type[BaseTool]] = {
    "jira_create_issue": _JiraCreateIssueTool,
    "jira_get_issue": _JiraGetIssueTool,
    "jira_search_issues": _JiraSearchIssuesTool,
    "jira_update_issue": _JiraUpdateIssueTool,
    "jira_transition_issue": _JiraTransitionIssueTool,
    "jira_add_comment": _JiraAddCommentTool,
}


@register_tool_provider
class JiraProvider(ToolProvider):
    name = "jira"
    description = (
        "Jira Cloud — create, search, transition, and comment on issues via REST API v3. "
        "Requires jira_domain, jira_email, jira_api_token in workflow state."
    )
    category: str = "standard"  # pyright: ignore[reportIncompatibleVariableOverride]
    auth = ApiKeyAuth(env_var="JIRA_API_TOKEN", settings_field="jira_api_token")

    async def tools(self) -> list[ToolDefinition]:
        result: list[ToolDefinition] = []
        for name, tool_cls in _TOOLS.items():
            instance = tool_cls()
            result.append(
                ToolDefinition(
                    name=name,
                    description=instance.description,
                    args_schema=getattr(instance, "args_schema", BaseModel),
                )
            )
        return result

    async def build_tool(self, tool_name: str, context: BuildContext) -> BaseTool:
        tool_cls = _TOOLS.get(tool_name)
        if tool_cls is None:
            raise ValueError(f"JiraProvider has no tool named {tool_name!r}")

        tool = tool_cls()

        variables = context.state.get("variables") or {}
        domain = str(variables.get("jira_domain", ""))
        email = str(variables.get("jira_email", ""))
        api_token = str(variables.get("jira_api_token", ""))

        if domain:
            tool._domain = domain  # pyright: ignore[reportAttributeAccessIssue]
        if email:
            tool._email = email  # pyright: ignore[reportAttributeAccessIssue]
        if api_token:
            tool._api_token = api_token  # pyright: ignore[reportAttributeAccessIssue]

        return tool

    async def health_check(self) -> HealthStatus:
        try:
            domain, email, api_token = _resolve_creds()
        except MissingConfigError as exc:
            return HealthStatus(ok=False, message=str(exc))
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
                resp = await client.get(
                    _build_url(domain, "myself"),
                    headers=_build_headers(email, api_token),
                )
            if resp.status_code == 200:
                return HealthStatus(ok=True, message=f"Jira reachable ({domain})")
            return HealthStatus(
                ok=False,
                message=f"Jira returned HTTP {resp.status_code}: {resp.text[:200]}",
            )
        except httpx.HTTPError as exc:
            return HealthStatus(ok=False, message=f"Jira unreachable: {exc}")


__all__ = ["JiraProvider", "MissingConfigError"]
