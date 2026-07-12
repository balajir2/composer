"""Tests for the Jira executor — node parsing, credential decryption, LangSmith threading."""

from typing import Any

import pytest
from langchain_core.messages import AIMessage

from src.engine.context import LangSmithConfig, set_current_langsmith
from src.engine.state import initial_state
from src.engine.workflow import JiraNode, encrypt_jira_api_token
from src.executors.jira import JiraExecutor
from src.security.encryption import decrypt


def _jira_node_json(**data_overrides: Any) -> dict[str, Any]:
    """Shape matches what frontend/components/composer/canvas/node-panels/jira.tsx
    actually serializes: camelCase keys straight off the node's `data` object."""
    data: dict[str, Any] = {
        "label": "Jira",
        "domain": "test.atlassian.net",
        "email": "test@example.com",
        "apiToken": "plaintext-token",
        "instructions": "Get issue PROJ-1",
        "model": "anthropic/claude-haiku-4-5-20251001",
    }
    data.update(data_overrides)
    return {
        "id": "j1",
        "type": "jira",
        "position": {"x": 0, "y": 0},
        "data": data,
    }


def test_camelcase_api_token_populates_field() -> None:
    """Regression test: the frontend sends `apiToken` (camelCase). Before the
    alias fix, JiraNodeData.api_token had no alias and this silently stayed
    None, so the node never authenticated against Jira."""
    node = JiraNode.model_validate(_jira_node_json())
    assert node.data.api_token == "plaintext-token"


class _TextOnlyFake:
    """Fake chat model: no tool calls, returns text immediately."""

    def __init__(self, text: str) -> None:
        self._text = text
        self.bind_tools_call_count = 0

    def bind_tools(self, tools: Any) -> "_TextOnlyFake":
        self.bind_tools_call_count += 1
        return self

    async def ainvoke(self, messages: Any) -> AIMessage:
        return AIMessage(content=self._text)


async def test_arun_decrypts_stored_token_before_use(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tokens are stored encrypted (post-fix); arun must decrypt before handing
    them to JiraProvider, and must never leak ciphertext into state.variables."""
    encrypted = encrypt_jira_api_token("real-secret-token")
    node = JiraNode.model_validate(_jira_node_json(apiToken=encrypted))

    captured_tokens: list[str] = []

    from src.tools.base import BuildContext
    from src.tools.providers import jira as jira_provider_module

    original_build_tool = jira_provider_module.JiraProvider.build_tool

    async def _capturing_build_tool(self: Any, tool_name: str, context: BuildContext) -> Any:
        variables = context.state.get("variables") or {}
        captured_tokens.append(str(variables.get("jira_api_token", "")))
        return await original_build_tool(self, tool_name, context)

    monkeypatch.setattr(jira_provider_module.JiraProvider, "build_tool", _capturing_build_tool)

    fake = _TextOnlyFake("done")
    from src.llm import providers

    monkeypatch.setattr(providers, "build_chat_model", lambda *a, **kw: fake)  # pyright: ignore[reportUnknownLambdaType]

    state = initial_state()
    delta = await JiraExecutor(node).arun(state)

    assert captured_tokens, "JiraProvider.build_tool was never called"
    assert all(t == "real-secret-token" for t in captured_tokens)
    assert all(t != encrypted for t in captured_tokens)
    assert delta["variables"]["lastOutput"]["text"] == "done"
    assert delta["node_results"]["j1"]["status"] == "completed"


async def test_node_configured_domain_wins_over_state_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P0-7: an external caller (POST /api/run/{slug} or /executions) can
    inject arbitrary keys into state.variables, including jira_domain.
    A node with its own configured domain must never let that external
    value redirect where tickets get created."""
    node = JiraNode.model_validate(_jira_node_json(domain="configured.atlassian.net"))

    captured_domains: list[str] = []

    from src.tools.base import BuildContext
    from src.tools.providers import jira as jira_provider_module

    original_build_tool = jira_provider_module.JiraProvider.build_tool

    async def _capturing_build_tool(self: Any, tool_name: str, context: BuildContext) -> Any:
        variables = context.state.get("variables") or {}
        captured_domains.append(str(variables.get("jira_domain", "")))
        return await original_build_tool(self, tool_name, context)

    monkeypatch.setattr(jira_provider_module.JiraProvider, "build_tool", _capturing_build_tool)

    fake = _TextOnlyFake("done")
    from src.llm import providers

    monkeypatch.setattr(providers, "build_chat_model", lambda *a, **kw: fake)  # pyright: ignore[reportUnknownLambdaType]

    state = initial_state()
    state["variables"]["jira_domain"] = "attacker-controlled.atlassian.net"
    await JiraExecutor(node).arun(state)

    assert captured_domains, "JiraProvider.build_tool was never called"
    assert all(d == "configured.atlassian.net" for d in captured_domains)


async def test_falls_back_to_state_variable_when_node_has_no_domain_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A workflow with no domain baked into the node (relying on an
    earlier Set State node, or an admin-controlled default) keeps
    working — the fallback exists for this legitimate case."""
    node = JiraNode.model_validate(_jira_node_json(domain=""))

    captured_domains: list[str] = []

    from src.tools.base import BuildContext
    from src.tools.providers import jira as jira_provider_module

    original_build_tool = jira_provider_module.JiraProvider.build_tool

    async def _capturing_build_tool(self: Any, tool_name: str, context: BuildContext) -> Any:
        variables = context.state.get("variables") or {}
        captured_domains.append(str(variables.get("jira_domain", "")))
        return await original_build_tool(self, tool_name, context)

    monkeypatch.setattr(jira_provider_module.JiraProvider, "build_tool", _capturing_build_tool)

    fake = _TextOnlyFake("done")
    from src.llm import providers

    monkeypatch.setattr(providers, "build_chat_model", lambda *a, **kw: fake)  # pyright: ignore[reportUnknownLambdaType]

    state = initial_state()
    state["variables"]["jira_domain"] = "set-state-configured.atlassian.net"
    await JiraExecutor(node).arun(state)

    assert captured_domains, "JiraProvider.build_tool was never called"
    assert all(d == "set-state-configured.atlassian.net" for d in captured_domains)


class _FakeJiraTool:
    """Stands in for a real Jira tool (jira_create_issue etc.) — returns a
    canned result string matching the real tools' success/"Error: ..."
    convention (src/tools/providers/jira.py) without hitting HTTP."""

    def __init__(self, name: str, result: str) -> None:
        self.name = name
        self._result = result

    async def ainvoke(self, args: dict[str, Any]) -> str:
        return self._result


def _stub_jira_provider(monkeypatch: pytest.MonkeyPatch, tool_name: str, result: str) -> None:
    from src.tools.base import BuildContext, ToolDefinition
    from src.tools.providers import jira as jira_provider_module

    class _StubField:
        pass

    async def _tools(self: Any) -> list[ToolDefinition]:
        return [ToolDefinition(name=tool_name, description="stub", args_schema=_StubField)]  # pyright: ignore[reportArgumentType]

    async def _build_tool(self: Any, name: str, context: BuildContext) -> _FakeJiraTool:
        return _FakeJiraTool(name, result)

    monkeypatch.setattr(jira_provider_module.JiraProvider, "tools", _tools)
    monkeypatch.setattr(jira_provider_module.JiraProvider, "build_tool", _build_tool)


class _ToolCallThenTextFake:
    """First ainvoke emits one tool call; second returns plain text."""

    def __init__(self, tool_name: str, args: dict[str, Any] | None = None) -> None:
        self._tool_name = tool_name
        self._args = args or {}
        self.call_count = 0

    def bind_tools(self, tools: list[Any]) -> "_ToolCallThenTextFake":
        return self

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        self.call_count += 1
        if self.call_count == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": self._tool_name,
                        "args": self._args,
                        "id": "call_1",
                        "type": "tool_call",
                    }
                ],
            )
        return AIMessage(content="Done.")


async def test_best_effort_policy_allows_response_with_no_tool_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default policy — unchanged behavior, a pure-text response (e.g. a
    read-only search / clarification) succeeds."""
    node = JiraNode.model_validate(_jira_node_json())
    fake = _TextOnlyFake("just a clarifying question, no action taken")
    from src.llm import providers

    monkeypatch.setattr(providers, "build_chat_model", lambda *a, **kw: fake)  # pyright: ignore[reportUnknownLambdaType]
    delta = await JiraExecutor(node).arun(initial_state())
    assert delta["node_results"]["j1"]["status"] == "completed"


async def test_require_tool_call_raises_when_no_tool_calls_made(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P0-2: a clarification/explanation response must not be mistaken
    for a successful action when the policy demands one."""
    from src.executors.jira import JiraActionPolicyError

    node = JiraNode.model_validate(_jira_node_json(actionPolicy="require_tool_call"))
    fake = _TextOnlyFake("I couldn't find enough information to create the issue.")
    from src.llm import providers

    monkeypatch.setattr(providers, "build_chat_model", lambda *a, **kw: fake)  # pyright: ignore[reportUnknownLambdaType]
    with pytest.raises(JiraActionPolicyError, match="no tool call"):
        await JiraExecutor(node).arun(initial_state())


async def test_require_successful_tool_call_raises_when_all_calls_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P0-2: a failed Jira API call (returned as an 'Error: ...' string,
    not an exception — see src/tools/providers/jira.py) must not
    silently satisfy require_successful_tool_call."""
    from src.executors.jira import JiraActionPolicyError

    _stub_jira_provider(
        monkeypatch, "jira_create_issue", "Error: Jira create issue failed (HTTP 403): forbidden"
    )
    node = JiraNode.model_validate(_jira_node_json(actionPolicy="require_successful_tool_call"))
    fake = _ToolCallThenTextFake("jira_create_issue", {"project_key": "PROJ", "summary": "x"})
    from src.llm import providers

    monkeypatch.setattr(providers, "build_chat_model", lambda *a, **kw: fake)  # pyright: ignore[reportUnknownLambdaType]
    with pytest.raises(JiraActionPolicyError, match="0 of 1"):
        await JiraExecutor(node).arun(initial_state())


async def test_require_successful_tool_call_passes_and_extracts_issue_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_jira_provider(
        monkeypatch,
        "jira_create_issue",
        "Created Jira issue PROJ-42: https://x.atlassian.net/browse/PROJ-42",
    )
    node = JiraNode.model_validate(_jira_node_json(actionPolicy="require_successful_tool_call"))
    fake = _ToolCallThenTextFake("jira_create_issue", {"project_key": "PROJ", "summary": "x"})
    from src.llm import providers

    monkeypatch.setattr(providers, "build_chat_model", lambda *a, **kw: fake)  # pyright: ignore[reportUnknownLambdaType]
    delta = await JiraExecutor(node).arun(initial_state())

    output = delta["node_results"]["j1"]["output"]
    assert output["successCount"] == 1
    assert output["failureCount"] == 0
    assert output["toolCallCount"] == 1
    assert output["createdIssueKeys"] == ["PROJ-42"]


async def test_minimum_successful_calls_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    """One successful call isn't enough when minimumSuccessfulCalls=2."""
    from src.executors.jira import JiraActionPolicyError

    _stub_jira_provider(
        monkeypatch,
        "jira_create_issue",
        "Created Jira issue PROJ-1: https://x.atlassian.net/browse/PROJ-1",
    )
    node = JiraNode.model_validate(
        _jira_node_json(actionPolicy="require_successful_tool_call", minimumSuccessfulCalls=2)
    )
    fake = _ToolCallThenTextFake("jira_create_issue", {"project_key": "PROJ", "summary": "x"})
    from src.llm import providers

    monkeypatch.setattr(providers, "build_chat_model", lambda *a, **kw: fake)  # pyright: ignore[reportUnknownLambdaType]
    with pytest.raises(JiraActionPolicyError, match="1 of 1"):
        await JiraExecutor(node).arun(initial_state())


def test_sanitize_args_for_logging_never_includes_values() -> None:
    """P0-2: tool args can contain full BRD/comment/description text —
    logging must never include values, only key names."""
    from src.executors.jira import _sanitize_args_for_logging  # pyright: ignore[reportPrivateUsage]

    sanitized = _sanitize_args_for_logging(
        {"summary": "confidential customer content", "project_key": "PROJ"}
    )
    assert "confidential customer content" not in str(sanitized)
    assert "PROJ" not in str(sanitized)
    assert sanitized == ["project_key", "summary"]


async def test_arun_tolerates_legacy_plaintext_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tokens saved before the encryption fix shipped are plaintext; decrypt
    must pass them through unchanged rather than erroring."""
    node = JiraNode.model_validate(_jira_node_json(apiToken="legacy-plaintext-token"))

    captured_tokens: list[str] = []
    from src.tools.base import BuildContext
    from src.tools.providers import jira as jira_provider_module

    original_build_tool = jira_provider_module.JiraProvider.build_tool

    async def _capturing_build_tool(self: Any, tool_name: str, context: BuildContext) -> Any:
        variables = context.state.get("variables") or {}
        captured_tokens.append(str(variables.get("jira_api_token", "")))
        return await original_build_tool(self, tool_name, context)

    monkeypatch.setattr(jira_provider_module.JiraProvider, "build_tool", _capturing_build_tool)

    fake = _TextOnlyFake("done")
    from src.llm import providers

    monkeypatch.setattr(providers, "build_chat_model", lambda *a, **kw: fake)  # pyright: ignore[reportUnknownLambdaType]

    delta = await JiraExecutor(node).arun(initial_state())
    assert captured_tokens == ["legacy-plaintext-token"] * len(captured_tokens)
    assert delta["variables"]["lastOutput"]["text"] == "done"


async def test_arun_threads_langsmith_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """LangSmith config must be threaded explicitly (CLAUDE.md fix #6), not
    left to fall back on process env vars."""
    node = JiraNode.model_validate(_jira_node_json())
    config = LangSmithConfig(
        tracing_v2=True, project="composer-test", endpoint="https://api.smith.test", api_key="k"
    )
    set_current_langsmith(config)
    try:
        received: dict[str, Any] = {}

        def _fake_build_chat_model(*args: Any, **kwargs: Any) -> _TextOnlyFake:
            received["langsmith_config"] = kwargs.get("langsmith_config")
            return _TextOnlyFake("done")

        from src.llm import providers

        monkeypatch.setattr(providers, "build_chat_model", _fake_build_chat_model)

        await JiraExecutor(node).arun(initial_state())
        assert received["langsmith_config"] == config
    finally:
        set_current_langsmith(None)


async def test_encrypt_jira_api_token_round_trips() -> None:
    from src.engine.workflow import decrypt_jira_api_token, is_jira_api_token_encrypted

    encrypted = encrypt_jira_api_token("hello")
    assert is_jira_api_token_encrypted(encrypted)
    assert not is_jira_api_token_encrypted("hello")
    assert decrypt_jira_api_token(encrypted) == "hello"
    assert decrypt(encrypted.split(":", 2)[2]) == "hello"
    # Legacy/plaintext values pass through unchanged.
    assert decrypt_jira_api_token("hello") == "hello"
