"""Executor wrapper that emits node_started / node_completed events.

Applied inside graph_builder.build_graph so every executor participates
automatically — no per-executor changes needed.

See Phase 5b spec §6.2 (event types updated to DES-007 in Phase 9a).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.engine.context import get_current_event_bus, get_current_execution_id
from src.engine.events import ExecutionEvent

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from src.engine.state import WorkflowStateDict
    from src.engine.workflow import WorkflowNode
    from src.executors.base import Executor


def wrap_executor_with_events(
    executor: Executor,
    node: WorkflowNode,
) -> Callable[[WorkflowStateDict], Awaitable[dict[str, Any]]]:
    """Return an `arun(state)` callable that emits events around the executor."""

    node_info: dict[str, Any] = {"nodeId": node.id, "nodeName": node.type}

    async def _arun(state: WorkflowStateDict) -> dict[str, Any]:
        execution_id = get_current_execution_id()
        bus = get_current_event_bus()

        if bus is not None and execution_id is not None:
            await bus.emit(
                ExecutionEvent(
                    type="node_started",
                    execution_id=execution_id,
                    payload=dict(node_info),
                )
            )

        result = await executor.arun(state)

        if bus is not None and execution_id is not None:
            await bus.emit(
                ExecutionEvent(
                    type="node_completed",
                    execution_id=execution_id,
                    payload=dict(node_info),
                )
            )

        return result

    return _arun


__all__ = ["wrap_executor_with_events"]
