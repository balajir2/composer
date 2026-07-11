"""Tests for the user-approval executor."""

from typing import Any

import pytest

from src.engine.state import initial_state
from src.engine.workflow import UserApprovalNode
from src.executors.user_approval import UserApprovalExecutor, UserApprovalNodeError


def _user_approval_node(**data: Any) -> UserApprovalNode:
    return UserApprovalNode.model_validate(
        {
            "id": "ua",
            "type": "user-approval",
            "position": {"x": 0, "y": 0},
            "data": {"label": "UA", **data},
        }
    )


async def test_user_approval_raises_interrupt_on_first_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First call to arun() must propagate GraphInterrupt (LangGraph raises it
    inside interrupt() when there's no prior checkpoint to resume from)."""
    from langgraph.errors import GraphInterrupt

    node = _user_approval_node(approvalMessage="Please approve")

    # Stub interrupt() to simulate LangGraph's first-pass behavior
    import src.executors.user_approval as ua_mod

    def _fake_interrupt(value: Any) -> Any:
        raise GraphInterrupt(value)

    monkeypatch.setattr(ua_mod, "interrupt", _fake_interrupt)

    with pytest.raises(GraphInterrupt):
        await UserApprovalExecutor(node).arun(initial_state())


async def test_user_approval_returns_delta_on_resume_approved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When resume value is 'approved', executor records it in variables."""
    node = _user_approval_node(approvalMessage="Approve?")

    import src.executors.user_approval as ua_mod

    monkeypatch.setattr(ua_mod, "interrupt", lambda value: "approved")  # pyright: ignore[reportUnknownLambdaType]

    delta = await UserApprovalExecutor(node).arun(initial_state())
    assert delta["variables"]["_approval_ua"] == "approved"
    assert delta["current_node_id"] == "ua"
    assert delta["node_results"]["ua"]["output"]["decision"] == "approved"


async def test_user_approval_returns_delta_on_resume_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node = _user_approval_node(approvalMessage="Approve?")

    import src.executors.user_approval as ua_mod

    monkeypatch.setattr(ua_mod, "interrupt", lambda value: "rejected")  # pyright: ignore[reportUnknownLambdaType]

    delta = await UserApprovalExecutor(node).arun(initial_state())
    assert delta["variables"]["_approval_ua"] == "rejected"
    assert delta["node_results"]["ua"]["output"]["decision"] == "rejected"


async def test_user_approval_invalid_decision_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node = _user_approval_node(approvalMessage="Approve?")

    import src.executors.user_approval as ua_mod

    monkeypatch.setattr(ua_mod, "interrupt", lambda value: "maybe")  # pyright: ignore[reportUnknownLambdaType]

    with pytest.raises(UserApprovalNodeError, match="maybe"):
        await UserApprovalExecutor(node).arun(initial_state())


async def test_user_approval_prompt_substitution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node = _user_approval_node(approvalMessage="Approve {{user_name}}'s request?")

    captured: dict[str, Any] = {}

    def _fake_interrupt(value: Any) -> Any:
        captured["value"] = value
        return "approved"

    import src.executors.user_approval as ua_mod

    monkeypatch.setattr(ua_mod, "interrupt", _fake_interrupt)

    state = initial_state()
    state["variables"]["user_name"] = "Alice"
    await UserApprovalExecutor(node).arun(state)

    assert captured["value"]["prompt"] == "Approve Alice's request?"
    assert captured["value"]["node_id"] == "ua"


async def test_arun_includes_approver_fields_in_interrupt_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task 5: approver_email/approver_cc (substituted) must ride along in the
    interrupt() payload so a later resume-side task can read them for email."""
    node = _user_approval_node(
        approvalMessage="Approve {{thing}}?",
        approverEmail="reviewer@example.com",
        approverCc="manager@example.com",
    )

    captured: dict[str, Any] = {}

    def _fake_interrupt(value: Any) -> Any:
        captured.update(value)
        return "approved"

    import src.executors.user_approval as ua_mod

    monkeypatch.setattr(ua_mod, "interrupt", _fake_interrupt)

    state = initial_state()
    state["variables"]["thing"] = "the BRD"
    await UserApprovalExecutor(node).arun(state)

    assert captured["prompt"] == "Approve the BRD?"
    assert captured["approver_email"] == "reviewer@example.com"
    assert captured["approver_cc"] == "manager@example.com"


async def test_user_approval_executor_is_registered() -> None:
    import src.executors.user_approval  # noqa: F401  # pyright: ignore[reportUnusedImport]
    from src.executors.base import build_executor

    node = _user_approval_node(approvalMessage="Approve?")
    executor = build_executor(node)
    assert isinstance(executor, UserApprovalExecutor)
