"""Resend email delivery integration."""

from typing import Any, cast

import httpx

RESEND_API_BASE = "https://api.resend.com"


class ResendEmailProviderError(RuntimeError):
    """Raised when Resend rejects or cannot process an email send."""


class ResendEmailProvider:
    """Small HTTP wrapper around Resend's POST /emails API."""

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    async def send_email(
        self,
        payload: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key

        try:
            async with httpx.AsyncClient(
                base_url=RESEND_API_BASE,
                headers=headers,
                timeout=httpx.Timeout(30.0, connect=5.0),
            ) as client:
                resp = await client.post("/emails", json=payload)
        except httpx.HTTPError as exc:
            raise ResendEmailProviderError(
                f"Resend transport failed: {type(exc).__name__}: {exc}"
            ) from exc

        if resp.status_code >= 400:
            raise ResendEmailProviderError(
                f"Resend API error {resp.status_code}: {resp.text[:500]}"
            )

        data = resp.json()
        if not isinstance(data, dict):
            raise ResendEmailProviderError(f"Resend response missing id: {data}")
        data = cast("dict[str, Any]", data)
        if not data.get("id"):
            raise ResendEmailProviderError(f"Resend response missing id: {data}")
        return data


async def send_password_reset_email(to: str, reset_link: str) -> None:
    """Send a password-reset email via Resend.

    Errors are intentionally allowed to propagate to the caller — the
    /auth/forgot-password route catches them and still returns 204 (see
    ADR-0027/spec: never let email-delivery failure leak account-existence
    information to the caller).
    """
    from src.config import get_settings

    settings = get_settings()
    html = (
        "<p>Someone requested a password reset for your Composer account.</p>"
        f'<p><a href="{reset_link}">Click here to set a new password</a>. '
        "This link expires in 30 minutes.</p>"
        "<p>If you didn't request this, you can safely ignore this email.</p>"
    )
    payload: dict[str, Any] = {
        "from": settings.resend_from_email,
        "to": [to],
        "subject": "Reset your Composer password",
        "html": html,
    }
    await ResendEmailProvider(settings.resend_api_key).send_email(payload)


__all__ = [
    "RESEND_API_BASE",
    "ResendEmailProvider",
    "ResendEmailProviderError",
    "send_password_reset_email",
]
