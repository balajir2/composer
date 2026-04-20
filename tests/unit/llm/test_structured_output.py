"""Tests for the structured-output wrapper shared by Agent + Extract."""

from unittest.mock import AsyncMock, MagicMock

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel

from src.llm.structured_output import structured_invoke


async def test_plain_text_returns_string() -> None:
    """Plain text mode (no schema, no json_mode) returns the content string."""
    fake = MagicMock(spec=BaseChatModel)
    fake.ainvoke = AsyncMock(return_value=AIMessage(content="Hello, world!"))

    result = await structured_invoke(fake, [HumanMessage(content="hi")])
    assert result == "Hello, world!"


async def test_json_mode_parses_openai_shape() -> None:
    """json_mode=True with json_mode support: parse JSON and return dict."""
    fake = MagicMock(spec=BaseChatModel)

    # Mock with_structured_output to return a structured model
    structured_mock = AsyncMock()
    structured_mock.ainvoke = AsyncMock(return_value='{"answer": 42}')
    fake.with_structured_output = MagicMock(return_value=structured_mock)

    result = await structured_invoke(
        fake,
        [HumanMessage(content="give me JSON")],
        json_mode=True,
    )
    assert result == {"answer": 42}
    fake.with_structured_output.assert_called_once_with(
        dict,
        method="json_mode",  # pyright: ignore[reportUnknownArgumentType]
    )


async def test_json_mode_unparseable_returns_raw_string() -> None:
    """json_mode=True but unparseable JSON: return the raw text (OAB fallback)."""
    fake = MagicMock(spec=BaseChatModel)

    # Mock with_structured_output to return unparseable text
    structured_mock = AsyncMock()
    structured_mock.ainvoke = AsyncMock(return_value="not valid json {{{")
    fake.with_structured_output = MagicMock(return_value=structured_mock)

    result = await structured_invoke(
        fake,
        [HumanMessage(content="give me JSON")],
        json_mode=True,
    )
    assert result == "not valid json {{{"


async def test_json_mode_with_provider_fallback() -> None:
    """json_mode=True but provider doesn't support it: fall back to plain invoke."""
    fake = MagicMock(spec=BaseChatModel)

    # with_structured_output raises NotImplementedError
    fake.with_structured_output = MagicMock(side_effect=NotImplementedError("not supported"))

    # Plain ainvoke still works and returns JSON text
    fake.ainvoke = AsyncMock(return_value=AIMessage(content='{"value": 100}'))

    result = await structured_invoke(
        fake,
        [HumanMessage(content="give me JSON")],
        json_mode=True,
    )
    assert result == {"value": 100}


async def test_schema_uses_with_structured_output() -> None:
    """When a schema is passed, route through with_structured_output."""

    class Answer(BaseModel):
        value: int

    fake = MagicMock(spec=BaseChatModel)

    # Mock with_structured_output to return a structured model
    structured_mock = AsyncMock()
    expected_answer = Answer(value=42)
    structured_mock.ainvoke = AsyncMock(return_value=expected_answer)
    fake.with_structured_output = MagicMock(return_value=structured_mock)

    result = await structured_invoke(
        fake,
        [HumanMessage(content="give me JSON")],
        schema=Answer,
    )
    assert result == expected_answer
    fake.with_structured_output.assert_called_once_with(Answer)
