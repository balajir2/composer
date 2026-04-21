"""Tests for the simpleeval wrapper (ADR-0012)."""

import pytest

from src.engine.state import initial_state
from src.executors._eval import EvalError, evaluate


def test_evaluate_arithmetic() -> None:
    state = initial_state()
    assert evaluate("1 + 2 * 3", state) == 7


def test_evaluate_reads_variables() -> None:
    state = initial_state()
    state["variables"]["x"] = 10
    state["variables"]["y"] = 32
    assert evaluate("variables['x'] + variables['y']", state) == 42


def test_evaluate_last_output_shorthand() -> None:
    state = initial_state()
    state["variables"]["lastOutput"] = "hello"
    assert evaluate("lastOutput + ' world'", state) == "hello world"


def test_evaluate_extra_names_scope() -> None:
    state = initial_state()
    # Simulates data-transform per-item scope
    assert evaluate("item * 2", state, extra_names={"item": 21}) == 42


def test_evaluate_undefined_name_raises() -> None:
    state = initial_state()
    with pytest.raises(EvalError, match="ghost"):
        evaluate("ghost + 1", state)


def test_evaluate_builtins_blocked() -> None:
    state = initial_state()
    with pytest.raises(EvalError):
        evaluate("__import__('os').listdir()", state)


def test_evaluate_dunder_access_blocked() -> None:
    """simpleeval blocks attribute names starting with __."""
    state = initial_state()
    state["variables"]["s"] = "hello"
    with pytest.raises(EvalError):
        evaluate("variables['s'].__class__", state)


def test_evaluate_syntax_error_raises_eval_error() -> None:
    state = initial_state()
    with pytest.raises(EvalError):
        evaluate("1 +* 2", state)
