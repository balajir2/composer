"""transform node executor.

Evaluates a single simpleeval expression over state and writes the
result to lastOutput.  No variable substitution on the expression itself
— the whole point is that the expression reads state directly via the
`variables` / `lastOutput` / `node_results` names.

See Phase 4a spec §8.
"""

from typing import Any

from src.engine.state import WorkflowStateDict
from src.engine.workflow import TransformNode
from src.executors._eval import EvalError, evaluate
from src.executors.base import register_executor


class TransformNodeError(RuntimeError):
    """Wraps EvalError with node-id context."""


@register_executor("transform")
class TransformExecutor:
    def __init__(self, node: TransformNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        expr = self.node.data.transform_script
        if not expr:
            raise ValueError(f"transform node {self.node.id!r} requires transformScript")
        try:
            result: Any = evaluate(expr, state)
        except EvalError as exc:
            raise TransformNodeError(f"transform node {self.node.id!r}: {exc}") from exc

        return {
            "variables": {"lastOutput": result},
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "input": {"expression": expr},
                    "output": result,
                }
            },
        }


__all__ = ["TransformExecutor", "TransformNodeError"]
