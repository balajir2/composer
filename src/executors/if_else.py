"""if-else node executor.

Evaluates a simpleeval boolean condition and records which branch will be
taken.  The actual routing decision is made by a router closure installed
into the LangGraph StateGraph by `graph_builder._route_if_else`; the
executor's job is to produce an audit row and populate node_results.

See Phase 4b spec §7.
"""

from typing import Any

from src.engine.state import WorkflowStateDict
from src.engine.workflow import IfElseNode
from src.executors._eval import EvalError, evaluate
from src.executors.base import register_executor


class IfElseNodeError(RuntimeError):
    """Wraps EvalError with node-id context."""


@register_executor("if-else")
class IfElseExecutor:
    def __init__(self, node: IfElseNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        expr = self.node.data.condition
        if not expr:
            raise ValueError(f"if-else node {self.node.id!r} requires condition")
        try:
            result = evaluate(expr, state)
        except EvalError as exc:
            raise IfElseNodeError(f"if-else node {self.node.id!r}: {exc}") from exc

        taken = "true" if result else "false"
        return {
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "input": {"condition": expr},
                    "output": {"taken": taken, "evaluated": bool(result)},
                }
            },
        }


__all__ = ["IfElseExecutor", "IfElseNodeError"]
