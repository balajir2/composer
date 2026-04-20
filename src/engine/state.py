"""LangGraph state definition for Composer workflows.

Mirrors OAB's WorkflowStateAnnotation at lib/workflow/langgraph.ts:52-88.
See docs/superpowers/specs/2026-04-20-phase-1-execution-engine-design.md §5.3.
"""

from collections.abc import Callable
from operator import add
from typing import Annotated, Any, TypedDict


def merge_dict(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """Shallow-merge reducer: right overrides left on key collision."""
    return {**left, **right}


def last_wins(_left: Any, right: Any) -> Any:
    """Reducer that discards prior value and keeps the latest write."""
    return right


class ChatMessage(TypedDict):
    role: str
    content: str


class NodeExecutionResult(TypedDict, total=False):
    node_id: str
    status: str
    input: Any
    output: Any
    error: str | None
    started_at: str
    completed_at: str
    usage: dict[str, int]


# LangGraph consumes this TypedDict to build its StateGraph reducer tree.
# Each Annotated[T, reducer] field declares how concurrent writes merge.
class WorkflowStateDict(TypedDict):
    variables: Annotated[dict[str, Any], merge_dict]
    chat_history: Annotated[list[ChatMessage], add]
    current_node_id: Annotated[str, last_wins]
    node_results: Annotated[dict[str, NodeExecutionResult], merge_dict]
    pending_auth: Annotated[dict[str, Any] | None, last_wins]
    loop_results: Annotated[list[Any], add]


def initial_state(raw_input: Any = "") -> WorkflowStateDict:
    """Build the default starting state, matching OAB's Annotation defaults."""
    return {
        "variables": {"input": raw_input, "lastOutput": ""},
        "chat_history": [],
        "current_node_id": "",
        "node_results": {},
        "pending_auth": None,
        "loop_results": [],
    }


__all__ = [
    "ChatMessage",
    "NodeExecutionResult",
    "WorkflowStateDict",
    "initial_state",
    "last_wins",
    "merge_dict",
]

# Re-export `add` so callers don't need operator; kept at module level for clarity.
_list_add: Callable[[list[Any], list[Any]], list[Any]] = add
