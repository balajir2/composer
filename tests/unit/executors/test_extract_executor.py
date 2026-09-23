"""Tests for the extract executor."""

from typing import Any
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage

from src.engine.state import initial_state
from src.engine.workflow import ExtractNode
from src.executors.extract import ExtractExecutor, ExtractNodeError


def _extract_node(**data: Any) -> ExtractNode:
    return ExtractNode.model_validate(
        {
            "id": "e",
            "type": "extract",
            "position": {"x": 0, "y": 0},
            "data": {"label": "E", **data},
        }
    )


async def test_extract_with_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    """structured_invoke returns a dict → extract passes it through."""
    from src.llm import structured_output as so

    async def _fake_invoke(*_a: Any, **_kw: Any) -> dict[str, Any]:
        return {"title": "Hello", "count": 3}

    monkeypatch.setattr(so, "structured_invoke", _fake_invoke)
    from src.llm import providers

    class _StubModel:
        async def ainvoke(self, *a: Any, **kw: Any) -> Any:  # pragma: no cover
            return AIMessage(content="{}")

    def _fake_build_chat_model(*a: Any, **kw: Any) -> _StubModel:
        return _StubModel()

    monkeypatch.setattr(providers, "build_chat_model", _fake_build_chat_model)

    node = _extract_node(
        input="Hello 3",
        jsonSchema={"type": "object", "properties": {"title": {"type": "string"}}},
    )
    delta = await ExtractExecutor(node).arun(initial_state())
    assert delta["variables"]["lastOutput"] == {"title": "Hello", "count": 3}


async def test_extract_uses_last_output_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    from src.llm import structured_output as so

    async def _fake_invoke(_model: Any, messages: Any, **kw: Any) -> dict[str, Any]:
        captured["prompt"] = messages[0].content if messages else ""
        return {"ok": True}

    monkeypatch.setattr(so, "structured_invoke", _fake_invoke)
    from src.llm import providers

    def _fake_build_chat_model(*a: Any, **kw: Any) -> object:
        return object()

    monkeypatch.setattr(providers, "build_chat_model", _fake_build_chat_model)

    node = _extract_node(jsonSchema={"type": "object"})
    state = initial_state()
    state["variables"]["lastOutput"] = "fallback input"
    await ExtractExecutor(node).arun(state)
    assert "fallback input" in captured["prompt"]


async def test_extract_empty_input_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # No input_text, and lastOutput is empty
    from src.llm import structured_output as so

    monkeypatch.setattr(so, "structured_invoke", AsyncMock())
    from src.llm import providers

    def _fake_build_chat_model(*a: Any, **kw: Any) -> object:
        return object()

    monkeypatch.setattr(providers, "build_chat_model", _fake_build_chat_model)

    node = _extract_node(jsonSchema={"type": "object"})
    with pytest.raises(ExtractNodeError, match="empty input"):
        await ExtractExecutor(node).arun(initial_state())


async def test_extract_coerces_aimessage_to_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.llm import structured_output as so

    async def _fake_invoke(*a: Any, **kw: Any) -> Any:
        return AIMessage(content='{"wrapped": true}')

    monkeypatch.setattr(so, "structured_invoke", _fake_invoke)
    from src.llm import providers

    def _fake_build_chat_model(*a: Any, **kw: Any) -> object:
        return object()

    monkeypatch.setattr(providers, "build_chat_model", _fake_build_chat_model)

    node = _extract_node(input="irrelevant", jsonSchema=None)
    delta = await ExtractExecutor(node).arun(initial_state())
    assert delta["variables"]["lastOutput"] == {"wrapped": True}


async def test_extract_executor_is_registered() -> None:
    import src.executors.extract  # noqa: F401  # pyright: ignore[reportUnusedImport]
    from src.executors.base import build_executor

    node = _extract_node(input="x", jsonSchema={"type": "object"})
    executor = build_executor(node)
    assert isinstance(executor, ExtractExecutor)
