"""transform node executor.

Evaluates a single simpleeval expression over state and writes the
result to lastOutput.  No variable substitution on the expression itself
— the whole point is that the expression reads state directly via the
`variables` / `lastOutput` / `node_results` names.

When `output_key` is set, the result is *also* written to that named
state variable.  This is the "compute-and-persist in one node"
primitive that loops + accumulators rely on.

See Phase 4a spec §8.
"""

import re
from typing import Any

from src.engine.state import WorkflowStateDict
from src.engine.workflow import TransformNode
from src.executors._eval import EvalError, evaluate
from src.executors.base import register_executor


class TransformNodeError(RuntimeError):
    """Wraps EvalError with node-id context."""


# Names the executor must not let transform overwrite — these carry
# special meaning in the workflow state (the eval scope alias dict, the
# auto-flowed lastOutput, the node-results map).  Designers get a clear
# validation error instead of a silently-broken workflow.
_RESERVED_OUTPUT_KEYS: frozenset[str] = frozenset({"variables", "lastOutput", "node_results"})
_IDENT_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@register_executor("transform")
class TransformExecutor:
    def __init__(self, node: TransformNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        expr = self.node.data.transform_script
        if not expr:
            raise ValueError(f"transform node {self.node.id!r} requires transformScript")

        output_key = self.node.data.output_key
        if output_key is not None:
            output_key = output_key.strip()
            if not output_key:
                output_key = None
        if output_key is not None:
            if not _IDENT_PATTERN.match(output_key):
                raise TransformNodeError(
                    f"transform node {self.node.id!r}: outputKey {output_key!r} is "
                    "not a valid identifier (must match [A-Za-z_][A-Za-z0-9_]*)."
                )
            if output_key in _RESERVED_OUTPUT_KEYS:
                raise TransformNodeError(
                    f"transform node {self.node.id!r}: outputKey {output_key!r} is "
                    f"reserved (one of {sorted(_RESERVED_OUTPUT_KEYS)}). "
                    "Pick a different variable name."
                )
            if output_key.startswith("_"):
                # `_while_iterations` and similar engine internals — keep them
                # writable only by the engine, not by user expressions.
                raise TransformNodeError(
                    f"transform node {self.node.id!r}: outputKey {output_key!r} starts "
                    "with '_' (reserved for engine internals). Pick a name without "
                    "the leading underscore."
                )

        try:
            result: Any = evaluate(expr, state)
        except EvalError as exc:
            raise TransformNodeError(f"transform node {self.node.id!r}: {exc}") from exc

        # `lastOutput` is always written so chained nodes that consume
        # `{{lastOutput}}` keep working unchanged.  When `output_key` is
        # set, ALSO write to that name — letting designers compute and
        # persist in one node (the whole point of this feature).
        variables_delta: dict[str, Any] = {"lastOutput": result}
        if output_key is not None:
            variables_delta[output_key] = result

        return {
            "variables": variables_delta,
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "input": {"expression": expr, "outputKey": output_key},
                    "output": result,
                }
            },
        }


__all__ = ["TransformExecutor", "TransformNodeError"]
