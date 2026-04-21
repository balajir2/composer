"""Tests for LangSmithConfig threading (fix #6)."""

from collections.abc import Iterator
from contextvars import copy_context

import pytest

from src.engine.context import (
    LangSmithConfig,
    _current_langsmith,  # pyright: ignore[reportPrivateUsage]
    get_current_langsmith,
    set_current_langsmith,
)


@pytest.fixture(autouse=True)
def _reset_langsmith() -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
    token = _current_langsmith.set(None)
    yield
    _current_langsmith.reset(token)


def test_default_is_none() -> None:
    assert get_current_langsmith() is None


def test_set_then_get() -> None:
    cfg = LangSmithConfig(tracing_v2=True, project="test", endpoint="https://smith", api_key="key")
    set_current_langsmith(cfg)
    assert get_current_langsmith() is cfg


def test_contextvar_isolation() -> None:
    outer = LangSmithConfig(
        tracing_v2=True, project="outer", endpoint="https://smith", api_key="k1"
    )
    set_current_langsmith(outer)
    ctx = copy_context()

    def _inner() -> LangSmithConfig | None:
        set_current_langsmith(
            LangSmithConfig(tracing_v2=True, project="inner", endpoint="", api_key="")
        )
        return get_current_langsmith()

    inner = ctx.run(_inner)
    assert inner is not None and inner.project == "inner"
    assert get_current_langsmith() is outer  # outer unchanged


def test_build_chat_model_wraps_when_config_given(monkeypatch: pytest.MonkeyPatch) -> None:
    """When langsmith_config is passed, build_chat_model wraps via with_config."""
    from unittest.mock import MagicMock

    from src.llm import providers as providers_mod

    inner_model = MagicMock()
    wrapped = MagicMock()
    inner_model.with_config = MagicMock(return_value=wrapped)

    monkeypatch.setattr(
        providers_mod,
        "_build_raw_chat_model",
        lambda model_string, **kw: inner_model,  # pyright: ignore[reportUnknownLambdaType]
    )

    cfg = LangSmithConfig(tracing_v2=True, project="p1", endpoint="https://smith", api_key="k")
    result = providers_mod.build_chat_model(
        "anthropic/claude-haiku-4-5-20251001", langsmith_config=cfg
    )
    assert result is wrapped
    inner_model.with_config.assert_called_once()
    config_arg = inner_model.with_config.call_args.args[0]
    assert config_arg["metadata"] == {"langsmith_project": "p1"}


def test_build_chat_model_no_wrap_when_config_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Back-compat: with config=None, return the raw model untouched."""
    from unittest.mock import MagicMock

    from src.llm import providers as providers_mod

    inner_model = MagicMock()
    inner_model.with_config = MagicMock()
    monkeypatch.setattr(
        providers_mod,
        "_build_raw_chat_model",
        lambda model_string, **kw: inner_model,  # pyright: ignore[reportUnknownLambdaType]
    )

    result = providers_mod.build_chat_model("anthropic/claude-haiku-4-5-20251001")
    assert result is inner_model
    inner_model.with_config.assert_not_called()
