"""Tests for the transform executor."""

from typing import Any

import pytest

from src.engine.state import initial_state
from src.engine.workflow import TransformNode
from src.executors.transform import TransformExecutor, TransformNodeError


def _transform_node(**data: Any) -> TransformNode:
    return TransformNode.model_validate(
        {
            "id": "t",
            "type": "transform",
            "position": {"x": 0, "y": 0},
            "data": {"label": "T", **data},
        }
    )


async def test_transform_arithmetic() -> None:
    node = _transform_node(transformScript="variables['x'] * 2")
    state = initial_state()
    state["variables"]["x"] = 21
    delta = await TransformExecutor(node).arun(state)
    assert delta["variables"]["lastOutput"] == 42


async def test_transform_last_output_shorthand() -> None:
    node = _transform_node(transformScript="lastOutput.upper()")
    state = initial_state()
    state["variables"]["lastOutput"] = "hello"
    delta = await TransformExecutor(node).arun(state)
    assert delta["variables"]["lastOutput"] == "HELLO"


async def test_transform_missing_script_raises() -> None:
    node = _transform_node()  # no transformScript
    with pytest.raises(ValueError, match="transformScript"):
        await TransformExecutor(node).arun(initial_state())


async def test_transform_syntax_error_raises() -> None:
    node = _transform_node(transformScript="1 +* 2")
    with pytest.raises(TransformNodeError):
        await TransformExecutor(node).arun(initial_state())


async def test_transform_dunder_access_blocked() -> None:
    node = _transform_node(transformScript="variables['x'].__class__")
    state = initial_state()
    state["variables"]["x"] = "hello"
    with pytest.raises(TransformNodeError):
        await TransformExecutor(node).arun(state)


async def test_transform_executor_is_registered() -> None:
    import src.executors.transform  # noqa: F401  # pyright: ignore[reportUnusedImport]
    from src.executors.base import build_executor

    node = _transform_node(transformScript="1")
    executor = build_executor(node)
    assert isinstance(executor, TransformExecutor)


# outputKey — write the result to a named state variable in addition to
# `lastOutput`.  Lets designers compute and persist in one node, which
# collapses loop bodies and accumulator patterns from 2 nodes-per-step
# into 1.


async def test_output_key_writes_named_variable() -> None:
    node = _transform_node(transformScript="variables['x'] + 1", outputKey="counter")
    state = initial_state()
    state["variables"]["x"] = 41
    delta = await TransformExecutor(node).arun(state)
    # Both lastOutput AND the named variable receive the value.
    assert delta["variables"]["lastOutput"] == 42
    assert delta["variables"]["counter"] == 42


async def test_output_key_omitted_preserves_legacy_behaviour() -> None:
    """Workflows from before outputKey existed must still work — when
    outputKey is unset, only lastOutput is written."""
    node = _transform_node(transformScript="2 + 2")
    delta = await TransformExecutor(node).arun(initial_state())
    assert delta["variables"]["lastOutput"] == 4
    # No extra named variable bleeds into the delta.
    assert set(delta["variables"].keys()) == {"lastOutput"}


async def test_output_key_blank_string_treated_as_unset() -> None:
    """Designers leave the panel field empty — the saved value can be
    "" or whitespace, both should mean 'no named output'."""
    node = _transform_node(transformScript="1", outputKey="   ")
    delta = await TransformExecutor(node).arun(initial_state())
    assert set(delta["variables"].keys()) == {"lastOutput"}


async def test_output_key_reserved_name_rejected() -> None:
    """Reserved scope aliases must not be shadowed — that would silently
    break expressions that rely on `variables` / `lastOutput` /
    `node_results` resolving to their built-in meanings."""
    for reserved in ("variables", "lastOutput", "node_results"):
        node = _transform_node(transformScript="1", outputKey=reserved)
        with pytest.raises(TransformNodeError, match="reserved"):
            await TransformExecutor(node).arun(initial_state())


async def test_output_key_underscore_prefix_rejected() -> None:
    """Leading underscore is reserved for engine internals like
    `_while_iterations` and `_guardrails_result`.  Don't let user
    transforms collide."""
    node = _transform_node(transformScript="1", outputKey="_my_var")
    with pytest.raises(TransformNodeError, match="reserved for engine internals"):
        await TransformExecutor(node).arun(initial_state())


async def test_output_key_invalid_identifier_rejected() -> None:
    """Output keys must be valid Python identifiers so they're
    referenceable as `{{my_var}}` or `my_var` in downstream expressions."""
    node = _transform_node(transformScript="1", outputKey="bad-name!")
    with pytest.raises(TransformNodeError, match="valid identifier"):
        await TransformExecutor(node).arun(initial_state())


async def test_output_key_resolves_in_downstream_expression() -> None:
    """End-to-end: a value written via outputKey is reachable by a
    subsequent transform via the eval scope's top-level spread."""
    # Step 1: write counter=10
    step1 = _transform_node(transformScript="10", outputKey="counter")
    state = initial_state()
    delta1 = await TransformExecutor(step1).arun(state)
    state["variables"].update(delta1["variables"])

    # Step 2: read it directly by name (no `variables[...]` ceremony)
    # — relies on the eval-scope spread that ships with the Mustache fix.
    step2 = _transform_node(transformScript="counter * 2", outputKey="doubled")
    delta2 = await TransformExecutor(step2).arun(state)
    assert delta2["variables"]["doubled"] == 20
    assert delta2["variables"]["lastOutput"] == 20
