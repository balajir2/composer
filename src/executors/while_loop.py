"""while node executor — iteration-bounded loop.

Each traversal through the while node:
  1. Bumps state.variables["_while_iterations"][node_id]
  2. Raises WhileMaxIterationsError if the count exceeds max_iterations
  3. Evaluates `condition` via _eval.evaluate; writes {"taken": "body"|"exit"}
     into node_results for audit

The actual next-node selection is made by the router closure
(graph_builder._route_while, Task 4).

The loop terminates when `condition` becomes false OR the cap is hit.
Infinite loops are prevented by the cap.

See Phase 4b spec §8.
"""

from typing import Any

from src.engine.state import WorkflowStateDict
from src.engine.workflow import WhileNode
from src.executors._eval import EvalError, evaluate
from src.executors.base import register_executor

ITER_STATE_KEY = "_while_iterations"


class WhileNodeError(RuntimeError):
    """Wraps EvalError with node-id context."""


class WhileMaxIterationsError(RuntimeError):
    """Raised when a while loop exceeds its configured max_iterations."""


@register_executor("while")
class WhileExecutor:
    def __init__(self, node: WhileNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        expr = self.node.data.condition
        if not expr:
            raise ValueError(f"while node {self.node.id!r} requires condition")

        # Bump iteration counter; enforce cap BEFORE evaluating condition.
        existing = (state.get("variables") or {}).get(ITER_STATE_KEY) or {}
        counts: dict[str, int] = dict(existing)
        counts[self.node.id] = int(counts.get(self.node.id, 0)) + 1
        if counts[self.node.id] > self.node.data.max_iterations:
            raise WhileMaxIterationsError(
                f"while node {self.node.id!r} exceeded max_iterations="
                f"{self.node.data.max_iterations} (count={counts[self.node.id]})"
            )

        try:
            result = evaluate(expr, state)
        except EvalError as exc:
            raise WhileNodeError(f"while node {self.node.id!r}: {exc}") from exc

        taken = "body" if result else "exit"
        return {
            "variables": {ITER_STATE_KEY: counts},
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "input": {"condition": expr, "iteration": counts[self.node.id]},
                    "output": {"taken": taken, "evaluated": bool(result)},
                }
            },
        }


__all__ = [
    "ITER_STATE_KEY",
    "WhileExecutor",
    "WhileMaxIterationsError",
    "WhileNodeError",
]
