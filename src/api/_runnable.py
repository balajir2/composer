"""Run/publish gate shared by the API routes: a stored workflow must be runnable as drawn.

Saving a half-built canvas must succeed, so graph validation on save stays
lenient (see `collect_missing_branches`). Starting an execution, the external
invoke, the file-trigger poller and publishing all call `refuse_unrunnable`
BEFORE they create anything, so a canvas with an unconnected required branch
answers 422 with a user-facing message instead of becoming a failed run.
"""

from typing import Any

from fastapi import HTTPException, status

from src.engine.graph_builder import (
    MissingBranch,
    WorkflowValidationError,
    find_missing_branches,
    validate_workflow_runnable,
)
from src.engine.workflow import Workflow


def parse_stored_workflow(row: Any) -> Workflow | None:
    """The engine workflow for a stored row, or None if it does not parse."""
    try:
        return Workflow.model_validate({"name": row.name, "nodes": row.nodes, "edges": row.edges})
    except ValueError:
        return None


def runnable_warnings(row: Any) -> list[MissingBranch]:
    """Why a stored canvas cannot run yet (empty = nothing to warn about)."""
    workflow = parse_stored_workflow(row)
    return find_missing_branches(workflow) if workflow is not None else []


def refuse_unrunnable(workflow: Workflow) -> None:
    """422 with the user-facing message when a required branch is unconnected."""
    try:
        validate_workflow_runnable(workflow)
    except WorkflowValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc


def refuse_unrunnable_row(row: Any) -> None:
    """`refuse_unrunnable` for a stored row; a row that does not parse is left to fail in the run."""
    workflow = parse_stored_workflow(row)
    if workflow is not None:
        refuse_unrunnable(workflow)
