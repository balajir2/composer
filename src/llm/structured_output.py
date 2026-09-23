"""Shared structured-output primitive for Agent (Phase 2) and Extract (Phase 4).

See ADR-0008; spec §6.
"""

import json
from typing import Any, cast

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage


async def structured_invoke(
    chat_model: BaseChatModel,
    messages: list[BaseMessage],
    *,
    schema: Any = None,
    json_mode: bool = False,
) -> Any:
    """Invoke `chat_model` and return a structured (or plain-text) result.

    Modes:
      - schema=None, json_mode=False  → plain text (str).
      - schema=None, json_mode=True   → with_structured_output(method="json_mode")
                                         then json.loads; fallback to raw text
                                         on parse failure (matches OAB).
      - schema=<pydantic|dict>        → with_structured_output(schema).
    """
    if schema is not None:
        structured = chat_model.with_structured_output(schema)
        return await structured.ainvoke(messages)  # pyright: ignore[reportAttributeAccessIssue]

    if json_mode:
        try:
            # OpenAI and similar providers support method="json_mode" via kwargs
            structured = chat_model.with_structured_output(dict, method="json_mode")  # pyright: ignore[reportUnknownArgumentType]
            result = await structured.ainvoke(messages)  # pyright: ignore[reportAttributeAccessIssue]
            if isinstance(result, str):
                try:
                    return json.loads(result)
                except json.JSONDecodeError:
                    return result
            return result
        except (NotImplementedError, ValueError, TypeError):
            # Provider doesn't support json_mode (e.g. Google schema-less):
            # fall back to plain invoke + best-effort json.loads.
            pass

    response = await chat_model.ainvoke(messages)
    content = cast(
        "str | list[Any]",
        response.content  # pyright: ignore[reportUnknownMemberType]
        if hasattr(response, "content")
        else str(response),
    )
    if json_mode:
        if isinstance(content, str):
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                return content
        return content
    return content if isinstance(content, str) else str(content)


__all__ = ["structured_invoke"]
