"""Email node executor."""

import re
from typing import Any

from src.config import get_settings
from src.engine.state import WorkflowStateDict
from src.engine.workflow import EmailNode
from src.executors.base import register_executor
from src.integrations.email.resend import ResendEmailProvider, ResendEmailProviderError
from src.variable_substitution import substitute

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MAX_RECIPIENTS = 50


class EmailNodeError(RuntimeError):
    """Raised when the email node is misconfigured or delivery fails."""


def _split_addresses(raw: str | None) -> list[str]:
    if not raw:
        return []
    addresses = [part.strip() for part in re.split(r"[,;\n]+", raw) if part.strip()]
    invalid = [addr for addr in addresses if not _EMAIL_RE.match(addr)]
    if invalid:
        raise EmailNodeError(f"invalid email address(es): {', '.join(invalid)}")
    return addresses


def _validate_from(raw: str) -> str:
    value = raw.strip()
    if not value:
        raise EmailNodeError("email node has no from address")
    match = re.search(r"<([^>]+)>", value)
    addr = match.group(1).strip() if match else value
    if not _EMAIL_RE.match(addr):
        raise EmailNodeError(f"invalid from address: {value}")
    return value


@register_executor("email")
class EmailExecutor:
    def __init__(self, node: EmailNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        if self.node.data.provider != "resend":
            raise EmailNodeError(f"unsupported email provider: {self.node.data.provider}")

        api_key = get_settings().resend_api_key
        if not api_key:
            raise EmailNodeError("RESEND_API_KEY is not configured")

        from_email = _validate_from(substitute(self.node.data.from_email, state))
        to = _split_addresses(substitute(self.node.data.to, state))
        cc = _split_addresses(substitute(self.node.data.cc or "", state))
        bcc = _split_addresses(substitute(self.node.data.bcc or "", state))
        reply_to = _split_addresses(substitute(self.node.data.reply_to or "", state))
        recipients = [*to, *cc, *bcc]
        if not to:
            raise EmailNodeError("email node must have at least one To recipient")
        if len(recipients) > _MAX_RECIPIENTS:
            raise EmailNodeError(
                f"email node has {len(recipients)} recipients; max is {_MAX_RECIPIENTS}"
            )

        subject = substitute(self.node.data.subject, state).strip()
        if not subject:
            raise EmailNodeError("email node has no subject")

        body = substitute(self.node.data.body, state)
        if not body.strip():
            raise EmailNodeError("email node has no body")

        payload: dict[str, Any] = {
            "from": from_email,
            "to": to,
            "subject": subject,
            self.node.data.body_type: body,
        }
        if cc:
            payload["cc"] = cc
        if bcc:
            payload["bcc"] = bcc
        if reply_to:
            payload["reply_to"] = reply_to

        idempotency_key = None
        if self.node.data.idempotency_key:
            idempotency_key = substitute(self.node.data.idempotency_key, state).strip() or None

        try:
            result = await ResendEmailProvider(api_key).send_email(
                payload,
                idempotency_key=idempotency_key,
            )
        except ResendEmailProviderError as exc:
            raise EmailNodeError(f"email node {self.node.id!r} failed: {exc}") from exc

        output = {
            "provider": "resend",
            "messageId": result["id"],
            "to": to,
            "cc": cc,
            "bccCount": len(bcc),
            "subject": subject,
            "status": "sent",
        }

        return {
            "variables": {"lastOutput": output},
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "input": {
                        "provider": "resend",
                        "from": from_email,
                        "to": to,
                        "cc": cc,
                        "bccCount": len(bcc),
                        "subject": subject,
                    },
                    "output": output,
                }
            },
        }


__all__ = ["EmailExecutor", "EmailNodeError"]
