"""LangGraphExecutor — the orchestrator that bridges HTTP and LangGraph.

Responsibilities:
  - Create WorkflowExecution rows (thread_id generation)
  - Compile the graph and drive execution
  - Persist terminal state (status, output, node_results, variables, error)
"""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver

from prisma import Json, Prisma  # pyright: ignore[reportAttributeAccessIssue]
from src.engine.context import set_current_db
from src.engine.graph_builder import build_graph
from src.engine.state import initial_state
from src.engine.workflow import Workflow

logger = logging.getLogger(__name__)


class LangGraphExecutor:
    """Orchestrates workflow execution via LangGraph and Prisma."""

    def __init__(
        self,
        db: Prisma,  # pyright: ignore[reportUnknownParameterType]
        checkpointer: BaseCheckpointSaver[Any],
    ) -> None:
        self.db = db
        self.checkpointer = checkpointer

    async def start_execution(self, *, workflow_id: str, input: Any, user_id: str | None) -> Any:
        """Create the execution row (status=running). Caller schedules `run()`."""
        thread_id = str(uuid.uuid4())
        return await self.db.workflowexecution.create(  # pyright: ignore[reportAttributeAccessIssue]
            data={
                "workflowId": workflow_id,
                "userId": user_id,
                "status": "running",
                "threadId": thread_id,
                "input": Json(input),
                "nodeResults": Json({}),
                "variables": Json({}),
            }
        )

    async def run(self, execution_id: str) -> None:
        """Drive the compiled graph to completion and persist results.

        Exceptions are caught here — they're recorded on the execution row so
        the API caller sees status='failed' rather than a background crash.
        """
        execution = await self.db.workflowexecution.find_unique(  # pyright: ignore[reportAttributeAccessIssue]
            where={"id": execution_id}
        )
        if execution is None:
            logger.error("Execution %s not found at run time", execution_id)
            return

        try:
            workflow_row = await self.db.workflow.find_unique(  # pyright: ignore[reportAttributeAccessIssue]
                where={"id": execution.workflowId}
            )
            if workflow_row is None:
                raise RuntimeError(f"Workflow {execution.workflowId!r} not found")

            workflow = Workflow.model_validate(
                {
                    "id": workflow_row.id,
                    "name": workflow_row.name,
                    "nodes": workflow_row.nodes,
                    "edges": workflow_row.edges,
                }
            )
            compiled = build_graph(workflow, self.checkpointer)

            state = initial_state(execution.input if execution.input is not None else "")
            set_current_db(self.db)
            final_state = await compiled.ainvoke(
                state,
                config={"configurable": {"thread_id": execution.threadId}},
            )

            final_vars: dict[str, Any] = final_state.get("variables") or {}
            await self.db.workflowexecution.update(  # pyright: ignore[reportAttributeAccessIssue]
                where={"id": execution_id},
                data={
                    "status": "completed",
                    "output": Json(final_vars.get("finalOutput")),
                    "variables": Json(final_vars),
                    "nodeResults": Json(final_state.get("node_results") or {}),
                    "completedAt": datetime.now(UTC),
                },
            )
        except Exception as exc:
            logger.exception("Execution %s failed", execution_id)
            await self.db.workflowexecution.update(  # pyright: ignore[reportAttributeAccessIssue]
                where={"id": execution_id},
                data={
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                    "completedAt": datetime.now(UTC),
                },
            )


__all__ = ["LangGraphExecutor"]
