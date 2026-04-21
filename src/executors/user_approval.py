"""user-approval node executor.

Calls LangGraph's `interrupt()` to pause execution.  On first pass,
interrupt() raises GraphInterrupt (caught by LangGraphExecutor.run,
which marks the execution 'waiting_approval').  On resume, interrupt()
returns the value passed to Command(resume=<decision>) — "approved" or
"rejected".

The executor records `_approval_<node_id>` in variables so that
graph_builder's router (built in Task 3) can dispatch to the
'approved' or 'rejected' branch.

Note: UserApprovalNodeData uses `approval_message` (not `prompt`) as
the field name — this executor reads `.data.approval_message` and passes
the rendered value as `prompt` in the interrupt payload.

See Phase 5a spec §6, ADR-0016.
"""

from typing import Any

from langgraph.types import interrupt  # pyright: ignore[reportUnknownVariableType]

from src.engine.state import WorkflowStateDict
from src.engine.workflow import UserApprovalNode
from src.executors.base import register_executor
from src.variable_substitution import substitute


class UserApprovalNodeError(RuntimeError):
    """Raised when the resumed decision is neither 'approved' nor 'rejected'."""


@register_executor("user-approval")
class UserApprovalExecutor:
    def __init__(self, node: UserApprovalNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        prompt_template = self.node.data.approval_message or "Please approve"
        prompt = substitute(prompt_template, state)

        decision = interrupt({"node_id": self.node.id, "prompt": prompt})

        if decision not in {"approved", "rejected"}:
            raise UserApprovalNodeError(
                f"user-approval node {self.node.id!r} received invalid decision "
                f"{decision!r}; expected 'approved' or 'rejected'"
            )

        return {
            "variables": {f"_approval_{self.node.id}": decision},
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "input": {"prompt": prompt},
                    "output": {"decision": decision},
                }
            },
        }


__all__ = ["UserApprovalExecutor", "UserApprovalNodeError"]
