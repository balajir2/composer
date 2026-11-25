"""Test the per-execution sessionmaker contextvar."""

from collections.abc import Iterator
from contextvars import copy_context
from typing import Any, cast

import pytest

from src.engine.context import (
    _current_sessionmaker,  # pyright: ignore[reportPrivateUsage]
    get_current_sessionmaker,
    set_current_sessionmaker,
)


@pytest.fixture(autouse=True)
def _reset_current_sessionmaker() -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
    """Reset the module-level contextvar after each test."""
    token = _current_sessionmaker.set(None)
    yield
    _current_sessionmaker.reset(token)


def _sm(name: str) -> Any:
    return cast("Any", name)


def test_default_is_none() -> None:
    assert get_current_sessionmaker() is None


def test_set_then_get() -> None:
    sentinel = _sm("sm")
    set_current_sessionmaker(sentinel)
    assert get_current_sessionmaker() is sentinel


def test_contextvar_isolation_across_contexts() -> None:
    set_current_sessionmaker(_sm("outer"))
    ctx = copy_context()

    def _inner() -> object:
        set_current_sessionmaker(_sm("inner"))
        return get_current_sessionmaker()

    inner_result = ctx.run(_inner)
    assert inner_result == "inner"
    # Outer context is not mutated
    assert get_current_sessionmaker() == "outer"
