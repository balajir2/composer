"""approvals: one row per approval decision, written in the resume transaction."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.errors import add_or_referenced_row_missing
from src.db.models import Approval, ApprovalDecision


@dataclass(frozen=True)
class ApprovalRecord:
    id: str
    execution_id: str
    node_id: str
    approver_user_id: str | None
    approver_email: str | None
    via_email_link: bool
    decision: ApprovalDecision
    note: str | None
    created_at: datetime


def _record(row: Approval) -> ApprovalRecord:
    return ApprovalRecord(
        id=row.id,
        execution_id=row.execution_id,
        node_id=row.node_id,
        approver_user_id=row.approver_user_id,
        approver_email=row.approver_email,
        via_email_link=row.via_email_link,
        decision=ApprovalDecision(row.decision),
        note=row.note,
        created_at=row.created_at,
    )


async def create(
    session: AsyncSession,
    *,
    execution_id: str,
    node_id: str,
    decision: ApprovalDecision,
    approver_user_id: str | None,
    approver_email: str | None,
    via_email_link: bool,
    note: str | None,
) -> ApprovalRecord:
    """Insert one decision. Callers run it in the same transaction as the guarded resume."""
    row = Approval(
        execution_id=execution_id,
        node_id=node_id,
        decision=decision,
        approver_user_id=approver_user_id,
        approver_email=approver_email,
        via_email_link=via_email_link,
        note=note,
    )
    await add_or_referenced_row_missing(session, row)
    return _record(row)


__all__ = ["ApprovalRecord", "create"]
