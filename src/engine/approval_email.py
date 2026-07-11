"""Approve-via-email: renders and sends the emailed decision links.

Called exactly once per genuine pause (from LangGraphExecutor.run's and
.resume's "just detected a fresh interrupt" branches) — never from inside
UserApprovalExecutor itself, since LangGraph replays a node's function body
on resume and a send here would be a non-idempotent side effect re-fired
on every resume. See docs/archive/phase-history/specs/2026-07-11-approval-email-notifications-design.md §C.
"""

import html
import logging
from typing import Any

from src.config import get_settings
from src.integrations.email.resend import ResendEmailProvider, ResendEmailProviderError
from src.security.jwt import create_approval_email_token

logger = logging.getLogger(__name__)


async def send_approval_email(
    *,
    execution_id: str,
    node_id: str,
    prompt: str,
    approver_email: str,
    approver_cc: str | None,
) -> None:
    """No-ops if approver_email is empty — email approval is opt-in per node."""
    if not approver_email:
        return

    settings = get_settings()
    approve_token = create_approval_email_token(execution_id, node_id, "approved")
    reject_token = create_approval_email_token(execution_id, node_id, "rejected")
    approve_url = f"{settings.backend_public_url}/approvals/email/{approve_token}"
    reject_url = f"{settings.backend_public_url}/approvals/email/{reject_token}"

    email_html = (
        f"<p>{html.escape(prompt)}</p>"
        f'<p><a href="{approve_url}" style="background:#16a34a;color:#fff;padding:10px 20px;'
        'text-decoration:none;border-radius:6px;margin-right:12px;">Approve</a>'
        f'<a href="{reject_url}" style="background:#dc2626;color:#fff;padding:10px 20px;'
        'text-decoration:none;border-radius:6px;">Reject</a></p>'
        f"<p style='color:#666;font-size:12px'>This link expires in "
        f"{settings.approval_link_ttl_hours} hours. If it expires, you (or "
        "an admin) can still approve or reject from within Composer.</p>"
    )

    payload: dict[str, Any] = {
        "from": settings.resend_from_email,
        "to": [approver_email],
        "subject": "Approval needed",
        "html": email_html,
    }
    if approver_cc:
        payload["cc"] = [approver_cc]

    try:
        await ResendEmailProvider(settings.resend_api_key).send_email(payload)
    except ResendEmailProviderError:
        logger.exception(
            "Failed to send approval email for execution_id=%s node_id=%s; "
            "in-app approval remains available.",
            execution_id,
            node_id,
        )


__all__ = ["send_approval_email"]
