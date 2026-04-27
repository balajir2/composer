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


# Mustache-style references in expressions: {{a.b.c}} → a["b"]["c"]
# Designers come from the prompt-substitution world where {{...}} is the
# universal reference syntax; the eval scope must accept it too or
# users will hit "Set is not available in this evaluator" — Python's AST
# parses `{...}` as a set literal otherwise.


def test_mustache_top_level_reference_resolves() -> None:
    state = initial_state()
    state["variables"]["check_guard"] = {"passed": True, "violations": []}
    assert evaluate("{{check_guard.passed}}", state) is True


def test_mustache_nested_path_resolves() -> None:
    state = initial_state()
    state["variables"]["user"] = {"profile": {"name": "Ada"}}
    assert evaluate("{{user.profile.name}}", state) == "Ada"


def test_mustache_works_in_compound_expression() -> None:
    """{{x.y}} can be combined with operators and other names."""
    state = initial_state()
    state["variables"]["check_guard"] = {"passed": False}
    state["variables"]["mode"] = "strict"
    # `not {{check_guard.passed}} and mode == "strict"`
    assert evaluate('not {{check_guard.passed}} and mode == "strict"', state) is True


def test_native_subscript_still_works() -> None:
    """Without Mustache — designers can also write the simpleeval form."""
    state = initial_state()
    state["variables"]["check_guard"] = {"passed": True}
    assert evaluate('check_guard["passed"]', state) is True


def test_mustache_with_unknown_name_raises_eval_error() -> None:
    """Original expression text appears in the error so the user can debug."""
    state = initial_state()
    with pytest.raises(EvalError, match=r"\{\{nonexistent\.field\}\}"):
        evaluate("{{nonexistent.field}}", state)


def test_mustache_with_non_identifier_path_left_as_literal() -> None:
    """Bail-out on weird paths — e.g. `{{1+1}}` stays as `{1+1}` and
    simpleeval reports its own error rather than a silent rewrite."""
    state = initial_state()
    with pytest.raises(EvalError):
        evaluate("{{1+1}}", state)


def test_reserved_names_not_shadowed_by_user_variable() -> None:
    """A user variable called `variables` must not clobber the reserved
    scope alias — otherwise expressions relying on the reserved alias
    would silently break."""
    state = initial_state()
    state["variables"]["variables"] = "user-set"
    state["variables"]["other"] = 42
    # `variables` resolves to the dict alias (reserved), not the user value
    assert evaluate("variables", state)["other"] == 42
