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
    assert delta["variables"]["lastOutput"] == "done"
    assert delta["node_results"]["j1"]["status"] == "completed"


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
    assert delta["variables"]["lastOutput"] == "done"


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
