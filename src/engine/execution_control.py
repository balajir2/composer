"""Cooperative stop signals at workflow execution boundaries."""

from typing import Any

from langgraph.errors import GraphBubbleUp


class ExecutionStopped(GraphBubbleUp):
    """Stop graph progression without recording an execution failure."""

    def __init__(self, execution_id: str, status: str) -> None:
        self.execution_id = execution_id
        self.status = status
        super().__init__(f"execution {execution_id} stopped with status {status}")


async def ensure_execution_running(db: Any, execution_id: str) -> None:
    """Read the authoritative execution status before proceeding."""
    row = await db.workflowexecution.find_unique(where={"id": execution_id})
    status = str(row.status) if row is not None else "missing"
    if status != "running":
        raise ExecutionStopped(execution_id, status)
