"""Resend email delivery integration."""

from typing import Any

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
        if not isinstance(data, dict) or not data.get("id"):
            raise ResendEmailProviderError(f"Resend response missing id: {data}")
        return data


__all__ = ["RESEND_API_BASE", "ResendEmailProvider", "ResendEmailProviderError"]
