"""Tests for OAB-parity variable substitution."""

from src.engine.state import initial_state
from src.variable_substitution import substitute


def test_flat_key_from_variables() -> None:
    state = initial_state()
    state["variables"]["name"] = "Ada"
    assert substitute("Hello, {{name}}!", state) == "Hello, Ada!"


def test_unresolved_renders_literal() -> None:
    state = initial_state()
    assert substitute("Hello, {{ghost}}!", state) == "Hello, {{ghost}}!"


def test_dotted_path_under_variables() -> None:
    state = initial_state()
    state["variables"]["user"] = {"name": "Ada", "age": 37}
    assert substitute("{{user.name}} is {{user.age}}", state) == "Ada is 37"


def test_explicit_state_variables_prefix() -> None:
    state = initial_state()
    state["variables"]["foo"] = {"bar": "baz"}
    assert substitute("{{state.variables.foo.bar}}", state) == "baz"


def test_explicit_state_nodeResults_prefix() -> None:
    state = initial_state()
    state["node_results"]["n1"] = {"output": "hello"}
    assert substitute("{{state.nodeResults.n1.output}}", state) == "hello"


def test_prototype_pollution_blocked() -> None:
    state = initial_state()
    state["variables"]["__proto__"] = "evil"
    assert substitute("{{__proto__}}", state) == "{{__proto__}}"


def test_python_unsafe_segments_blocked() -> None:
    state = initial_state()
    assert substitute("{{__class__}}", state) == "{{__class__}}"


def test_whitespace_trimmed_inside_braces() -> None:
    state = initial_state()
    state["variables"]["x"] = "y"
    assert substitute("{{ x }}", state) == "y"


def test_multiple_substitutions_in_one_template() -> None:
    state = initial_state()
    state["variables"]["a"] = "A"
    state["variables"]["b"] = "B"
    assert substitute("{{a}}-{{b}}-{{a}}", state) == "A-B-A"


def test_dict_value_renders_as_json() -> None:
    state = initial_state()
    state["variables"]["cfg"] = {"k": "v"}
    assert substitute("{{cfg}}", state) == '{"k": "v"}'


def test_no_braces_returns_template_unchanged() -> None:
    state = initial_state()
    assert substitute("plain text", state) == "plain text"
