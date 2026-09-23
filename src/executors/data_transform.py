"""data-transform node executor — map / filter / reduce via simpleeval.

Operation = 'map' | 'filter' | 'reduce'.  For each item, evaluate the
per-item expression with the item bound to `itemVar` (default 'item').
Reduce additionally exposes `acc` in scope.

See Phase 4a spec §9.
"""

from typing import Any, cast

from src.engine.state import WorkflowStateDict
from src.engine.workflow import DataTransformNode
from src.executors._eval import EvalError, evaluate
from src.executors.base import register_executor


class DataTransformNodeError(RuntimeError):
    """Raised for unknown operation, non-iterable collection, or eval failure."""


SUPPORTED_OPS = {"map", "filter", "reduce"}


def run_map_filter_reduce(
    op: str,
    coll: list[Any] | tuple[Any, ...],
    expr: str,
    item_var: str,
    initial: Any,
    state: WorkflowStateDict,
) -> Any:
    """Run map/filter/reduce over `coll`, evaluating `expr` per item.

    Shared by DataTransformExecutor (real execution) and the
    evaluate-data-transform test-preview endpoint (src/api/expressions.py),
    so the preview can never drift from real execution behavior. Assumes
    `op` is already validated to be one of SUPPORTED_OPS. Raises EvalError
    on any per-item evaluation failure.
    """
    if op == "map":
        return [evaluate(expr, state, extra_names={item_var: x}) for x in coll]
    if op == "filter":
        return [x for x in coll if evaluate(expr, state, extra_names={item_var: x})]
    # reduce
    acc: Any = initial
    for x in coll:
        acc = evaluate(expr, state, extra_names={item_var: x, "acc": acc})
    return acc


@register_executor("data-transform")
class DataTransformExecutor:
    def __init__(self, node: DataTransformNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        op = self.node.data.operation
        if op not in SUPPORTED_OPS:
            raise DataTransformNodeError(
                f"data-transform node {self.node.id!r} operation {op!r} not "
                f"supported (need map / filter / reduce)"
            )
        if not self.node.data.collection:
            raise ValueError(f"data-transform node {self.node.id!r} requires collection expression")
        if not self.node.data.expression:
            raise ValueError(f"data-transform node {self.node.id!r} requires expression")

        try:
            coll = evaluate(self.node.data.collection, state)
        except EvalError as exc:
            raise DataTransformNodeError(
                f"data-transform node {self.node.id!r} collection: {exc}"
            ) from exc

        if not isinstance(coll, (list, tuple)):
            raise DataTransformNodeError(
                f"data-transform node {self.node.id!r} collection evaluated to "
                f"non-iterable type {type(coll).__name__}"
            )

        coll = cast("list[Any] | tuple[Any, ...]", coll)
        item_var = self.node.data.item_var
        expr = self.node.data.expression

        try:
            output = run_map_filter_reduce(op, coll, expr, item_var, self.node.data.initial, state)
        except EvalError as exc:
            raise DataTransformNodeError(
                f"data-transform node {self.node.id!r} per-item expression: {exc}"
            ) from exc

        return {
            "variables": {"lastOutput": output},
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "input": {
                        "operation": op,
                        "expression": expr,
                        "itemVar": item_var,
                    },
                    "output": output,
                }
            },
        }


__all__ = [
    "SUPPORTED_OPS",
    "DataTransformExecutor",
    "DataTransformNodeError",
    "run_map_filter_reduce",
]
