"""JudgmentProvider abstraction backing the Decision node executor.

Mirrors src/executors/base.py's Executor/register_executor/build_executor
pattern one layer down: a *provider* a node's executor calls, not the
node's executor itself. LLMJudgmentProvider is the only implementation a
Decision node needs to work end to end; TypeSafeJudgmentProvider (Task 9)
is a second, isolated implementation — see
docs/superpowers/specs/2026-09-22-decision-node-design.md.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import httpx
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, create_model

from src.config import get_settings
from src.engine.context import get_current_langsmith
from src.executors.guardrails import DEFAULT_MODEL
from src.llm.providers import build_chat_model
from src.llm.structured_output import structured_invoke

if TYPE_CHECKING:
    from src.engine.workflow import DecisionExample, DecisionOption

_SYSTEM_PROMPT = "You are a decision-making assistant. Follow the schema exactly."

# TypeSafe's /v1/systemone rejects a request with no `model` field (422
# "Field required") — the docs claim the Python SDK defaults to jev-latest,
# but that default is client-side only, not applied by the raw HTTP API.
# Confirmed live 2026-09-23. Always send this when the caller didn't supply
# their own model string.
_DEFAULT_TYPESAFE_MODEL = "jev-latest"


class JudgmentProviderError(RuntimeError):
    """Raised by a JudgmentProvider on backend failure (network, bad response, etc.)."""


@runtime_checkable
class JudgmentProvider(Protocol):
    async def decide_binary(
        self, *, instruction: str, examples: list[DecisionExample], text: str, model: str | None
    ) -> tuple[bool, float]: ...

    async def decide_choice(
        self,
        *,
        instruction: str,
        options: list[DecisionOption],
        examples: list[DecisionExample],
        text: str,
        model: str | None,
    ) -> tuple[str, float]: ...


_REGISTRY: dict[str, type[Any]] = {}


def register_judgment_provider(name: str):
    def _wrap(cls: type[Any]) -> type[Any]:
        _REGISTRY[name] = cls
        return cls

    return _wrap


def build_judgment_provider(name: str) -> JudgmentProvider:
    cls = _REGISTRY.get(name)
    if cls is None:
        raise JudgmentProviderError(f"unknown judgment provider {name!r}")
    return cls()


def _format_prompt(
    instruction: str, examples: list[DecisionExample], text: str, *, value_key: str
) -> str:
    lines = [instruction]
    if examples:
        lines.append("")
        lines.append("Examples:")
        for i, ex in enumerate(examples, start=1):
            value = ex.result if value_key == "result" else ex.option
            lines.append(f'{i}. Text: "{ex.input}" -> {value}')
    lines.append("")
    lines.append("Now decide:")
    lines.append(f"Text: {text}")
    return "\n".join(lines)


class _BinaryDecision(BaseModel):
    result: bool
    confidence: float


@register_judgment_provider("llm")
class LLMJudgmentProvider:
    async def decide_binary(
        self, *, instruction: str, examples: list[DecisionExample], text: str, model: str | None
    ) -> tuple[bool, float]:
        llm = build_chat_model(
            model or DEFAULT_MODEL,
            temperature=0.0,
            langsmith_config=get_current_langsmith(),
        )
        prompt = _format_prompt(instruction, examples, text, value_key="result")
        try:
            parsed = await structured_invoke(
                llm,
                [SystemMessage(content=_SYSTEM_PROMPT), HumanMessage(content=prompt)],
                schema=_BinaryDecision,
            )
        except Exception as exc:
            raise JudgmentProviderError(f"LLM binary decision failed: {exc}") from exc
        return parsed.result, parsed.confidence

    async def decide_choice(
        self,
        *,
        instruction: str,
        options: list[DecisionOption],
        examples: list[DecisionExample],
        text: str,
        model: str | None,
    ) -> tuple[str, float]:
        labels = tuple(opt.label for opt in options)
        from typing import Literal

        schema = create_model(
            "ChoiceDecision",
            option=(Literal[labels], ...),
            confidence=(float, ...),
        )
        llm = build_chat_model(
            model or DEFAULT_MODEL,
            temperature=0.0,
            langsmith_config=get_current_langsmith(),
        )
        prompt = _format_prompt(instruction, examples, text, value_key="option")
        try:
            parsed = await structured_invoke(
                llm,
                [SystemMessage(content=_SYSTEM_PROMPT), HumanMessage(content=prompt)],
                schema=schema,
            )
        except Exception as exc:
            raise JudgmentProviderError(f"LLM choice decision failed: {exc}") from exc
        return parsed.option, parsed.confidence


_TYPESAFE_URL = "https://api.typesafe.ai/v1/systemone"
_TYPESAFE_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


def _format_instructions(
    instruction: str, examples: list[DecisionExample], *, value_key: str
) -> str:
    if not examples:
        return instruction
    lines = [instruction, "", "Examples:"]
    for i, ex in enumerate(examples, start=1):
        value = ex.result if value_key == "result" else ex.option
        lines.append(f'{i}. "{ex.input}" -> {value}')
    return "\n".join(lines)


@register_judgment_provider("typesafe")
class TypeSafeJudgmentProvider:
    async def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        api_key = get_settings().typesafe_api_key or ""
        async with httpx.AsyncClient(timeout=_TYPESAFE_TIMEOUT) as client:
            resp = await client.post(
                _TYPESAFE_URL, json=body, headers={"Authorization": f"Bearer {api_key}"}
            )
        if resp.status_code >= 400:
            raise JudgmentProviderError(f"TypeSafe HTTP {resp.status_code}: {resp.text[:240]}")
        return resp.json()

    async def decide_binary(
        self, *, instruction: str, examples: list[DecisionExample], text: str, model: str | None
    ) -> tuple[bool, float]:
        body: dict[str, Any] = {
            "state": text,
            "questions": {
                "decision": {
                    "type": "noul",
                    "instructions": _format_instructions(instruction, examples, value_key="result"),
                }
            },
        }
        body["model"] = model or _DEFAULT_TYPESAFE_MODEL
        data = await self._post(body)
        noul = data["answers"]["decision"]["noul"]
        result = noul >= 0.5
        confidence = noul if result else 1 - noul
        return result, confidence

    async def decide_choice(
        self,
        *,
        instruction: str,
        options: list[DecisionOption],
        examples: list[DecisionExample],
        text: str,
        model: str | None,
    ) -> tuple[str, float]:
        criteria = {opt.label: opt.description for opt in options if opt.description}
        question: dict[str, Any] = {
            "type": "choice",
            "instructions": _format_instructions(instruction, examples, value_key="option"),
        }
        if criteria:
            question["criteria"] = criteria
        body: dict[str, Any] = {"state": text, "questions": {"decision": question}}
        body["model"] = model or _DEFAULT_TYPESAFE_MODEL
        data = await self._post(body)
        answer = data["answers"]["decision"]
        return answer["choice"], answer["confidence"]


__all__ = [
    "JudgmentProvider",
    "JudgmentProviderError",
    "LLMJudgmentProvider",
    "TypeSafeJudgmentProvider",
    "build_judgment_provider",
    "register_judgment_provider",
]
