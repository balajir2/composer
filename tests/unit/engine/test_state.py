"""Tests for WorkflowState reducer helpers."""

from src.engine.state import initial_state, last_wins, merge_dict


def test_merge_dict_right_wins_on_key_conflict() -> None:
    left = {"a": 1, "b": 2}
    right = {"b": 99, "c": 3}
    assert merge_dict(left, right) == {"a": 1, "b": 99, "c": 3}


def test_merge_dict_handles_empty_sides() -> None:
    assert merge_dict({}, {"a": 1}) == {"a": 1}
    assert merge_dict({"a": 1}, {}) == {"a": 1}
    assert merge_dict({}, {}) == {}


def test_merge_dict_does_not_mutate_inputs() -> None:
    left = {"a": 1}
    right = {"b": 2}
    merge_dict(left, right)
    assert left == {"a": 1}
    assert right == {"b": 2}


def test_last_wins_returns_right() -> None:
    assert last_wins("old", "new") == "new"
    assert last_wins(None, "something") == "something"
    assert last_wins(42, 0) == 0


def test_initial_state_defaults() -> None:
    s = initial_state()
    assert s["variables"] == {"input": "", "lastOutput": ""}
    assert s["chat_history"] == []
    assert s["current_node_id"] == ""
    assert s["node_results"] == {}
    assert s["pending_auth"] is None
    assert s["loop_results"] == []


def test_initial_state_carries_input() -> None:
    s = initial_state({"user_message": "hi"})
    assert s["variables"]["input"] == {"user_message": "hi"}


def test_initial_state_includes_final_outputs() -> None:
    s = initial_state()
    assert s["final_outputs"] == {}
