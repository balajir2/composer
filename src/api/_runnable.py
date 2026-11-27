"""Run/publish gate shared by the API routes: a stored workflow must be runnable as drawn.

Saving a half-built canvas must succeed, so graph validation on save stays
lenient (see `collect_missing_branches`, `find_join_problems`). Starting an
execution, the external invoke, the file-trigger poller and publishing all call
`refuse_unrunnable` BEFORE they create anything, so a canvas with an
unconnected required branch or a Join that cannot wait correctly answers 422
with a user-facing message instead of becoming a failed run.
"""

from typing import Any, Literal, NamedTuple

from fastapi import HTTPException, status

from src.engine.graph_builder import (
    WorkflowValidationError,
    find_join_problems,
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


class RunnableWarning(NamedTuple):
    """One reason a stored canvas cannot run yet."""

    node_id: str
    code: Literal["missing_branch", "join_mixed_inputs", "join_lane_escapes", "join_collision"]
    branch: str | None  # the unconnected branch for `missing_branch`, else None
    message: str


def runnable_warnings(row: Any) -> list[RunnableWarning]:
    """Why a stored canvas cannot run yet (empty = nothing to warn about).

    Exactly the problems `validate_workflow_runnable` adds on top of the
    structural rules, so a warning shown on save is a refusal at run.
    """
    workflow = parse_stored_workflow(row)
    if workflow is None:
        return []
    return [
        *(
            RunnableWarning(m.node_id, "missing_branch", m.branch, m.message)
            for m in find_missing_branches(workflow)
        ),
        *(
            RunnableWarning(p.node_id, p.code, None, p.message)
            for p in find_join_problems(workflow)
        ),
    ]


def refuse_unrunnable(workflow: Workflow) -> None:
    """422 with the user-facing message when the canvas cannot run as drawn."""
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
