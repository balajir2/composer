"""Test per-execution db contextvar."""

from collections.abc import Iterator
from contextvars import copy_context

import pytest

from src.engine.context import (
    _current_db,  # pyright: ignore[reportPrivateUsage]
    get_current_db,
    set_current_db,
)


@pytest.fixture(autouse=True)
def _reset_current_db() -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
    """Reset the module-level contextvar after each test."""
    token = _current_db.set(None)
    yield
    _current_db.reset(token)


def test_default_is_none() -> None:
    assert get_current_db() is None


def test_set_then_get() -> None:
    sentinel = object()
    set_current_db(sentinel)
    assert get_current_db() is sentinel


def test_contextvar_isolation_across_contexts() -> None:
    set_current_db("outer")
    ctx = copy_context()

    def _inner() -> str | None:
        set_current_db("inner")
        return get_current_db()

    inner_result = ctx.run(_inner)
    assert inner_result == "inner"
    # Outer context is not mutated
    assert get_current_db() == "outer"
