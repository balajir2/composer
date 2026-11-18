"""LangGraphExecutor — the orchestrator that bridges HTTP and LangGraph.

Responsibilities:
  - Create WorkflowExecution rows (thread_id generation)
  - Compile the graph and drive execution
  - Persist terminal state (status, output, node_results, variables, error)
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import Any, cast

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command  # pyright: ignore[reportUnknownVariableType]

from prisma import Json, Prisma  # pyright: ignore[reportAttributeAccessIssue]
from src.config import get_settings
from src.engine.approval_email import send_approval_email
from src.engine.context import (
    LangSmithConfig,
    set_current_db,
    set_current_event_bus,
    set_current_execution_id,
    set_current_langsmith,
)
from src.engine.events import EventType, ExecutionEvent
from src.engine.events_notify import notify_execution_event
from src.engine.events_pg import PostgresEventStore
from src.engine.execution_control import ExecutionStopped, ensure_execution_running
from src.engine.graph_builder import build_graph
from src.engine.state import initial_state
from src.engine.workflow import Workflow

logger = logging.getLogger(__name__)


class _ExecutionDeadlineExceeded(TimeoutError):
    """Composer's graph-invocation budget expired."""


def _transition_succeeded(execution_id: str, intended_status: str, updated: int) -> bool:
    """Report a lost compare-and-set without exposing execution payloads."""
    if updated != 1:
        logger.info(
            "Execution %s did not transition to %s",
            execution_id,
            intended_status,
            extra={
                "execution_id": execution_id,
                "transition_source": "executor",
                "previous_status": "running",
                "intended_status": intended_status,
                "transition_outcome": "lost",
            },
        )
    return updated == 1


def _extract_pending_info(snapshot: Any) -> dict[str, Any]:
    """Extract {node_id, prompt} from snapshot.tasks[*].interrupts.

    Returns an empty dict if no interrupt payload is found.
    """
    tasks = getattr(snapshot, "tasks", None) or ()
    for task in tasks:
        interrupts = getattr(task, "interrupts", None) or ()
        for interrupt_obj in interrupts:
            value = getattr(interrupt_obj, "value", None)
            if isinstance(value, dict):
                return cast("dict[str, Any]", value)
    return {}


class LangGraphExecutor:
    """Orchestrates workflow execution via LangGraph and Prisma."""

    def __init__(
        self,
        db: Prisma,  # pyright: ignore[reportUnknownParameterType]
        checkpointer: BaseCheckpointSaver[Any],
        event_bus: PostgresEventStore | None = None,
    ) -> None:
        self.db = db
        self.checkpointer = checkpointer
        self.event_bus = event_bus

    async def _emit(
        self,
        event_type: EventType,
        execution_id: str,
        payload: dict[str, Any],
    ) -> None:
        """Append + notify, but never raise.

        `run()`/`resume()` call this AFTER state-mutating writes (e.g.
        `_mark_completed`, `_mark_waiting_approval`) have already committed
        the real outcome to Postgres. A network hiccup on the event-log
        side (Prisma insert or the asyncpg NOTIFY call) must not propagate
        into the outer `try/except`, which would overwrite an already-
        correct "completed"/"waiting_approval" status with "failed" —
        corrupting the real execution outcome over a telemetry failure.
        Self-guarding here means every call site (including the
        `workflow_started` emit in both `run()` and `resume()`) is safe by construction.
        """
        if self.event_bus is None:
            return
        try:
            event = ExecutionEvent(type=event_type, execution_id=execution_id, payload=payload)
            seq = await self.event_bus.append(event)
            await notify_execution_event(execution_id, seq=seq)
        except Exception:
            logger.exception("Failed to emit %s event for execution %s", event_type, execution_id)

    async def start_execution(
        self,
        *,
        workflow_id: str,
        input: Any,
        user_id: str | None,
        idempotency_key: str | None = None,
    ) -> Any:
        """Create the execution row (status=queued).

        P1-2: the row starts life as `queued`, not `running` — the caller
        (src/api/executions.py's create_execution) enqueues a Cloud Task
        rather than running this execution inline, and the row only
        becomes `running` once POST /internal/claim-and-run actually
        claims it (src/api/internal.py). A row that never gets claimed
        (e.g. the Cloud Tasks enqueue itself fails) stays visibly
        `queued` rather than lying about being `running`.
        """
        thread_id = str(uuid.uuid4())
        data: dict[str, Any] = {
            "workflowId": workflow_id,
            "userId": user_id,
            "status": "queued",
            "threadId": thread_id,
            "input": Json(input),
            "nodeResults": Json({}),
            "variables": Json({}),
        }
        if idempotency_key is not None:
            data["idempotencyKey"] = idempotency_key
        return await self.db.workflowexecution.create(  # pyright: ignore[reportAttributeAccessIssue]
            data=data  # pyright: ignore[reportArgumentType]
        )

    async def _load_execution(self, execution_id: str) -> Any:
        """Load and return the execution row, or None if not found."""
        return await self.db.workflowexecution.find_unique(  # pyright: ignore[reportAttributeAccessIssue]
            where={"id": execution_id}
        )

    async def _prepare_compiled(self, execution: Any) -> tuple[Any, Any, dict[str, Any]]:
        """Load workflow, build graph, prepare state + LangGraph config.

        Returns (compiled_graph, state, langgraph_config).
        """
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
        if execution.userId:
            state["user_id"] = execution.userId  # Phase 7a

        settings = get_settings()
        ls_config = (
            LangSmithConfig(
                tracing_v2=settings.langchain_tracing_v2,
                project=settings.langchain_project,
                endpoint=settings.langchain_endpoint,
                api_key=settings.langchain_api_key,
            )
            if settings.langchain_tracing_v2
            else None
        )
        set_current_langsmith(ls_config)
        set_current_db(self.db)
        set_current_execution_id(execution.id)
        set_current_event_bus(self.event_bus)

        lg_config: dict[str, Any] = {"configurable": {"thread_id": execution.threadId}}
        return compiled, state, lg_config

    async def _mark_completed(self, execution_id: str, final_state: dict[str, Any]) -> bool:
        """Persist completion results only while the execution is running."""
        final_vars: dict[str, Any] = final_state.get("variables") or {}
        final_outputs: dict[str, Any] = final_state.get("final_outputs") or {}
        # final_outputs is keyed by End-node id (see src/executors/end.py).
        # The common case is exactly one End node -- collapse to a plain
        # scalar so the persisted `output` is byte-for-byte identical to
        # every existing single-End workflow's historical behavior.
        # Genuinely multiple End nodes (e.g. independent parallel
        # branches, or if-else branches each with their own End)
        # persist the full dict instead of picking one arbitrarily.
        # Tuple-unpack / falsy checks (not `or`) so a legitimately falsy
        # value (0, False, "", [], {}) isn't discarded (P0-7).
        if len(final_outputs) == 1:
            (output_value,) = final_outputs.values()
        elif len(final_outputs) > 1:
            output_value = final_outputs
        else:
            output_value = final_vars.get("lastOutput")
        updated = await self.db.workflowexecution.update_many(  # pyright: ignore[reportAttributeAccessIssue]
            where={"id": execution_id, "status": "running"},
            data={
                "status": "completed",
                "output": Json(output_value),
                "variables": Json(final_vars),
                "nodeResults": Json(final_state.get("node_results") or {}),
                "completedAt": datetime.now(UTC),
                "leaseOwner": None,
                "leaseExpiresAt": None,
                "runningSince": None,
            },
        )
        return _transition_succeeded(execution_id, "completed", updated)

    async def _mark_waiting_approval(
        self,
        execution_id: str,
        pending_info: dict[str, Any],
        existing_vars: dict[str, Any] | None,
        pending_since: str,
    ) -> bool:
        """Persist waiting_approval status + pending node/prompt into variables.

        Merges pending markers into existing_vars so pre-interrupt state is preserved.

        `pending_since` is computed once by the caller (not here) so the exact
        same timestamp is stamped into `variables` AND threaded into the
        emailed approve/reject tokens' `pending_since` claim — the two must
        match byte-for-byte for the approval-email endpoint's pause-instance
        guard to accept a token issued for this pause.
        """
        merged: dict[str, Any] = {**(existing_vars or {})}
        merged["_pending_approval_node"] = pending_info.get("node_id")
        merged["_pending_approval_prompt"] = pending_info.get("prompt")
        merged["_pending_approval_since"] = pending_since

        updated = await self.db.workflowexecution.update_many(  # pyright: ignore[reportAttributeAccessIssue]
            where={"id": execution_id, "status": "running"},
            data={
                "status": "waiting_approval",
                "variables": Json(merged),
                "leaseOwner": None,
                "leaseExpiresAt": None,
                "runningSince": None,
            },
        )
        return _transition_succeeded(execution_id, "waiting_approval", updated)

    async def _mark_failed(
        self, execution_id: str, exc: Exception, *, failure_code: str = "execution_error"
    ) -> bool:
        """Persist failure details only while the execution is running."""
        updated = await self.db.workflowexecution.update_many(  # pyright: ignore[reportAttributeAccessIssue]
            where={"id": execution_id, "status": "running"},
            data={
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "failureCode": failure_code,
                "completedAt": datetime.now(UTC),
                "leaseOwner": None,
                "leaseExpiresAt": None,
                "runningSince": None,
            },
        )
        return _transition_succeeded(execution_id, "failed", updated)

    async def _invoke_with_timeout(self, awaitable: Awaitable[dict[str, Any]]) -> dict[str, Any]:
        runtime = get_settings().execution_max_runtime_seconds
        deadline = asyncio.timeout(runtime)
        try:
            async with deadline:
                return await awaitable
        except TimeoutError as exc:
            if deadline.expired():
                raise _ExecutionDeadlineExceeded(
                    f"Execution exceeded its {runtime}-second processing budget"
                ) from exc
            raise

    async def run(self, execution_id: str) -> None:
        """Drive the compiled graph to completion and persist results.

        Exceptions are caught here — they're recorded on the execution row so
        the API caller sees status='failed' rather than a background crash.

        LangGraph >= 0.2.x catches GraphInterrupt internally in ainvoke and
        returns cleanly with pre-interrupt state.  After ainvoke returns, we
        inspect compiled.aget_state(config) to detect a pending pause:
        snapshot.next is a non-empty tuple when the graph is paused.
        """
        execution = await self._load_execution(execution_id)
        if execution is None:
            logger.error("Execution %s not found at run time", execution_id)
            return

        try:
            await ensure_execution_running(self.db, execution_id)
            await self._emit("workflow_started", execution_id, {"status": "running"})
            compiled, state, lg_config = await self._prepare_compiled(execution)

            final_state = await self._invoke_with_timeout(compiled.ainvoke(state, config=lg_config))

            snapshot = await compiled.aget_state(lg_config)  # pyright: ignore[reportUnknownMemberType]
            if snapshot.next:  # non-empty tuple → graph is paused at an interrupt
                logger.info("Execution %s paused at user-approval node", execution_id)
                pending_info = _extract_pending_info(snapshot)
                existing_vars: dict[str, Any] = {}
                if isinstance(final_state.get("variables"), dict):
                    existing_vars = final_state["variables"]
                pending_since = datetime.now(UTC).isoformat()
                if await self._mark_waiting_approval(
                    execution_id, pending_info, existing_vars, pending_since
                ):
                    await send_approval_email(
                        execution_id=execution_id,
                        node_id=str(pending_info.get("node_id", "")),
                        prompt=str(pending_info.get("prompt", "")),
                        approver_email=str(pending_info.get("approver_email", "")),
                        approver_cc=str(pending_info.get("approver_cc", "")) or None,
                        pending_since=pending_since,
                        attachment_path=str(pending_info.get("attachment_path", "")) or None,
                    )
                    await self._emit(
                        "approval_required",
                        execution_id,
                        {
                            "node_id": pending_info.get("node_id"),
                            "prompt": pending_info.get("prompt"),
                            "status": "waiting_approval",
                        },
                    )
                return

            if await self._mark_completed(execution_id, final_state):
                await self._emit(
                    "workflow_completed",
                    execution_id,
                    {"status": "completed"},
                )

        except ExecutionStopped as stopped:
            logger.info("Execution %s stopped with status %s", execution_id, stopped.status)
            return
        except _ExecutionDeadlineExceeded as exc:
            logger.warning("Execution %s timed out", execution_id)
            if await self._mark_failed(execution_id, exc, failure_code="execution_timeout"):
                await self._emit("workflow_completed", execution_id, {"status": "failed"})
        except Exception as exc:
            logger.exception("Execution %s failed", execution_id)
            if await self._mark_failed(execution_id, exc):
                # _emit is self-guarding (never raises) — no outer try/except needed.
                await self._emit(
                    "workflow_completed",
                    execution_id,
                    {"status": "failed"},
                )

    async def resume(self, execution_id: str, decision: str) -> None:
        """Continue a paused execution with a user decision (approved/rejected).

        Loads the execution checkpoint via LangGraph's PrismaCheckpointSaver and
        feeds the decision via Command(resume=decision).  If another user-approval
        node is hit during resume, the execution is marked waiting_approval again.
        """
        execution = await self._load_execution(execution_id)
        if execution is None:
            logger.error("Execution %s not found at resume time", execution_id)
            return

        try:
            await ensure_execution_running(self.db, execution_id)
            await self._emit("workflow_started", execution_id, {"status": "running"})
            compiled, _state, lg_config = await self._prepare_compiled(execution)

            final_state = await self._invoke_with_timeout(
                compiled.ainvoke(
                    Command(resume=decision),  # pyright: ignore[reportArgumentType]
                    config=lg_config,
                )
            )

            snapshot = await compiled.aget_state(lg_config)  # pyright: ignore[reportUnknownMemberType]
            if snapshot.next:  # non-empty tuple → chained pause (another user-approval downstream)
                logger.info("Execution %s paused again at another user-approval node", execution_id)
                pending_info = _extract_pending_info(snapshot)
                existing_vars: dict[str, Any] = {}
                if isinstance(final_state.get("variables"), dict):
                    existing_vars = final_state["variables"]
                pending_since = datetime.now(UTC).isoformat()
                if await self._mark_waiting_approval(
                    execution_id, pending_info, existing_vars, pending_since
                ):
                    await send_approval_email(
                        execution_id=execution_id,
                        node_id=str(pending_info.get("node_id", "")),
                        prompt=str(pending_info.get("prompt", "")),
                        approver_email=str(pending_info.get("approver_email", "")),
                        approver_cc=str(pending_info.get("approver_cc", "")) or None,
                        pending_since=pending_since,
                        attachment_path=str(pending_info.get("attachment_path", "")) or None,
                    )
                    await self._emit(
                        "approval_required",
                        execution_id,
                        {
                            "node_id": pending_info.get("node_id"),
                            "prompt": pending_info.get("prompt"),
                            "status": "waiting_approval",
                        },
                    )
                return

            if await self._mark_completed(execution_id, final_state):
                await self._emit(
                    "workflow_completed",
                    execution_id,
                    {"status": "completed"},
                )

        except ExecutionStopped as stopped:
            logger.info(
                "Execution %s stopped during resume with status %s", execution_id, stopped.status
            )
            return
        except _ExecutionDeadlineExceeded as exc:
            logger.warning("Execution %s timed out during resume", execution_id)
            if await self._mark_failed(execution_id, exc, failure_code="execution_timeout"):
                await self._emit("workflow_completed", execution_id, {"status": "failed"})
        except Exception as exc:
            logger.exception("Execution %s failed during resume", execution_id)
            if await self._mark_failed(execution_id, exc):
                # _emit is self-guarding (never raises) — no outer try/except needed.
                await self._emit(
                    "workflow_completed",
                    execution_id,
                    {"status": "failed"},
                )


__all__ = ["LangGraphExecutor"]
