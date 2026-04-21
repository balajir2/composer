"""Tests for the LangGraphExecutor orchestrator.

Unit-level: mocks the Prisma client and verifies the orchestrator
creates the right rows, drives the compiled graph, and persists the
right fields on completion/failure.
"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from langgraph.checkpoint.memory import MemorySaver

from src.engine.langgraph_executor import LangGraphExecutor


def _workflow_row(workflow_dict: dict[str, Any]) -> SimpleNamespace:
    """Simulate a prisma.models.Workflow row with the fields the executor reads."""
    return SimpleNamespace(
        id=workflow_dict.get("id", "wf1"),
        name=workflow_dict["name"],
        nodes=workflow_dict["nodes"],
        edges=workflow_dict["edges"],
        # workflow row also has workflowId attribute on derived types; executor
        # reads the camelCase form from prisma client (execution.workflowId)
    )


def _start_to_end_workflow_dict() -> dict[str, Any]:
    return {
        "id": "wf1",
        "name": "Smoke",
        "nodes": [
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {"id": "e", "type": "end", "position": {"x": 100, "y": 0}, "data": {"label": "E"}},
        ],
        "edges": [{"id": "edge1", "source": "s", "target": "e"}],
    }


async def test_start_execution_creates_row_with_running_status() -> None:
    db = MagicMock()
    db.workflowexecution = MagicMock()
    db.workflowexecution.create = AsyncMock(
        return_value=SimpleNamespace(
            id="ex1",
            workflowId="wf1",
            status="running",
            threadId="t1",
        )
    )
    executor = LangGraphExecutor(db=db, checkpointer=MemorySaver())
    row = await executor.start_execution(workflow_id="wf1", input={"msg": "hi"}, user_id="dev")
    assert row.id == "ex1"
    assert row.status == "running"
    db.workflowexecution.create.assert_awaited_once()
    assert db.workflowexecution.create.await_args is not None
    create_kwargs = db.workflowexecution.create.await_args.kwargs["data"]
    assert create_kwargs["workflowId"] == "wf1"
    assert create_kwargs["userId"] == "dev"
    assert create_kwargs["status"] == "running"
    assert isinstance(create_kwargs["threadId"], str) and len(create_kwargs["threadId"]) > 0


async def test_run_completes_start_to_end() -> None:
    wf_dict = _start_to_end_workflow_dict()
    db = MagicMock()
    db.workflow = MagicMock()
    db.workflow.find_unique = AsyncMock(return_value=_workflow_row(wf_dict))
    db.workflowexecution = MagicMock()
    db.workflowexecution.find_unique = AsyncMock(
        return_value=SimpleNamespace(
            id="ex1",
            workflowId="wf1",
            threadId="t1",
            input="hello",
            userId=None,  # Phase 7a: nullable; executor checks before setting state
        )
    )
    db.workflowexecution.update = AsyncMock()

    executor = LangGraphExecutor(db=db, checkpointer=MemorySaver())
    await executor.run("ex1")

    db.workflowexecution.update.assert_awaited_once()
    assert db.workflowexecution.update.await_args is not None
    update_kwargs = db.workflowexecution.update.await_args.kwargs["data"]
    assert update_kwargs["status"] == "completed"
    # output/variables/nodeResults are wrapped in prisma.Json(...) before being
    # passed to Prisma — unwrap via the .data attribute for assertion.
    assert update_kwargs["output"].data == "hello"  # start sets lastOutput to parsed input
    assert "completedAt" in update_kwargs


async def test_run_marks_failed_on_exception() -> None:
    # Construct a workflow that will blow up at graph-build time (guardrails node — Phase 6).
    bad_wf = {
        "id": "wf1",
        "name": "Bad",
        "nodes": [
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {
                "id": "h",
                "type": "guardrails",
                "position": {"x": 0, "y": 0},
                "data": {"label": "H"},
            },
            {"id": "e", "type": "end", "position": {"x": 0, "y": 0}, "data": {"label": "E"}},
        ],
        "edges": [
            {"id": "e1", "source": "s", "target": "h"},
            {"id": "e2", "source": "h", "target": "e"},
        ],
    }
    db = MagicMock()
    db.workflow = MagicMock()
    db.workflow.find_unique = AsyncMock(return_value=_workflow_row(bad_wf))
    db.workflowexecution = MagicMock()
    db.workflowexecution.find_unique = AsyncMock(
        return_value=SimpleNamespace(
            id="ex1",
            workflowId="wf1",
            threadId="t1",
            input="",
            userId=None,  # Phase 7a: nullable; executor checks before setting state
        )
    )
    db.workflowexecution.update = AsyncMock()

    executor = LangGraphExecutor(db=db, checkpointer=MemorySaver())
    await executor.run("ex1")

    db.workflowexecution.update.assert_awaited_once()
    assert db.workflowexecution.update.await_args is not None
    update_kwargs = db.workflowexecution.update.await_args.kwargs["data"]
    assert update_kwargs["status"] == "failed"
    assert "Phase 6" in update_kwargs["error"]
