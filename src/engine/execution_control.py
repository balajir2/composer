"""Cooperative stop signals at workflow execution boundaries."""

from langgraph.errors import GraphBubbleUp
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db.repos import executions as executions_repo


class ExecutionStopped(GraphBubbleUp):
    """Stop graph progression without recording an execution failure."""

    def __init__(self, execution_id: str, status: str) -> None:
        self.execution_id = execution_id
        self.status = status
        super().__init__(f"execution {execution_id} stopped with status {status}")


async def ensure_execution_running(
    sessionmaker: async_sessionmaker[AsyncSession], execution_id: str
) -> None:
    """Read the authoritative execution status before proceeding.

    One short read in its own session, closed before the caller does anything slow.
    """
    async with sessionmaker() as session:
        current = await executions_repo.get_status(session, execution_id)
    status = current if current is not None else "missing"
    if status != "running":
        raise ExecutionStopped(execution_id, status)
