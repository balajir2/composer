"""Approve-via-email: renders and sends the emailed decision links.

Called exactly once per genuine pause (from LangGraphExecutor.run's and
.resume's "just detected a fresh interrupt" branches) — never from inside
UserApprovalExecutor itself, since LangGraph replays a node's function body
on resume and a send here would be a non-idempotent side effect re-fired
on every resume. See docs/archive/phase-history/specs/2026-07-11-approval-email-notifications-design.md §C.
"""

import base64
import html
import logging
from pathlib import Path
from typing import Any

from src.config import Settings, get_settings
from src.integrations.email.resend import ResendEmailProvider, ResendEmailProviderError
from src.security.jwt import create_approval_email_token

logger = logging.getLogger(__name__)


def _build_attachment(
    attachment_path: str,
    *,
    execution_id: str,
    node_id: str,
    settings: Settings,
) -> dict[str, str] | None:
    """Read + base64-encode `attachment_path` for the Resend payload.

    `attachment_path` is substituted from workflow state (typically
    {{lastOutput}} from an upstream file-write node), so it carries the same
    trust level as any other state-substituted value that reaches a
    filesystem read — mirroring src/executors/file_write.py's path-traversal
    guard and src/conversion/markdown_to_pdf.py's SSRF guard. It is bounded
    to `settings.approval_attachment_root`: `Path.resolve()` collapses `..`
    segments and symlinks before the containment check runs, so a path
    outside the root (however it's spelled) is skipped rather than read.
    Every failure mode here is a skip-with-warning, never a raise — an
    attachment problem must never prevent the approval email itself from
    sending.
    """
    resolved = Path(attachment_path).resolve()
    root = Path(settings.approval_attachment_root).resolve()

    if not resolved.is_relative_to(root):
        logger.warning(
            "Skipping approval-email attachment for execution_id=%s node_id=%s: "
            "attachment_path %s resolves outside approval_attachment_root %s.",
            execution_id,
            node_id,
            resolved,
            root,
        )
        return None

    if not resolved.is_file():
        logger.warning(
            "Skipping approval-email attachment for execution_id=%s node_id=%s: "
            "%s does not exist or is not a file.",
            execution_id,
            node_id,
            resolved,
        )
        return None

    try:
        size = resolved.stat().st_size
        if size > settings.approval_attachment_max_bytes:
            logger.warning(
                "Skipping approval-email attachment for execution_id=%s node_id=%s: "
                "%s is %d bytes, exceeding approval_attachment_max_bytes=%d.",
                execution_id,
                node_id,
                resolved,
                size,
                settings.approval_attachment_max_bytes,
            )
            return None
        content = base64.b64encode(resolved.read_bytes()).decode("ascii")
    except Exception:
        logger.warning(
            "Skipping approval-email attachment for execution_id=%s node_id=%s: "
            "failed to read %s.",
            execution_id,
            node_id,
            resolved,
            exc_info=True,
        )
        return None

    return {"filename": resolved.name, "content": content}


async def send_approval_email(
    *,
    execution_id: str,
    node_id: str,
    prompt: str,
    approver_email: str,
    approver_cc: str | None,
    pending_since: str,
    attachment_path: str | None = None,
) -> None:
    """No-ops if approver_email is empty — email approval is opt-in per node.

    `pending_since` binds both issued tokens to this specific pause instance
    (not just `node_id`), so a `while`-loop node that pauses repeatedly at the
    same node_id can't have a stale token from an earlier iteration resolve a
    later one.

    `attachment_path`, if given, is bounded to `settings.approval_attachment_root`
    (see `_build_attachment`) — anything outside that root, missing, or over
    `approval_attachment_max_bytes` is silently skipped; the email still sends.
    """
    if not approver_email:
        return

    settings = get_settings()
    approve_token = create_approval_email_token(
        execution_id, node_id, "approved", approver_email, pending_since
    )
    reject_token = create_approval_email_token(
        execution_id, node_id, "rejected", approver_email, pending_since
    )
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

    if attachment_path:
        attachment = _build_attachment(
            attachment_path,
            execution_id=execution_id,
            node_id=node_id,
            settings=settings,
        )
        if attachment is not None:
            payload["attachments"] = [attachment]

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
