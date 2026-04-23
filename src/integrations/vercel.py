"""Thin async client for Vercel's env-var API.

Docs: https://vercel.com/docs/rest-api/reference/endpoints/projects
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class VercelEnvVar:
    key: str
    value: str
    target: list[str]  # ['production', 'preview']


class VercelClient:
    def __init__(
        self, api_token: str, project_id: str, base_url: str = "https://api.vercel.com"
    ) -> None:
        self.api_token = api_token
        self.project_id = project_id
        self.base_url = base_url

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_token}"}

    async def list_env(self) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
            resp = await client.get(
                f"{self.base_url}/v10/projects/{self.project_id}/env",
                headers=self._headers(),
            )
            resp.raise_for_status()
            body: dict[str, Any] = resp.json()
            envs: list[dict[str, Any]] = body.get("envs", [])
            return envs

    async def upsert_env(self, var: VercelEnvVar) -> None:
        """Create or update one env var.

        Strategy: look up by key; if exists, PATCH; else POST.
        """
        existing = await self.list_env()
        match = next((e for e in existing if e["key"] == var.key), None)
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
            if match is None:
                resp = await client.post(
                    f"{self.base_url}/v10/projects/{self.project_id}/env",
                    headers=self._headers(),
                    json={
                        "key": var.key,
                        "value": var.value,
                        "type": "encrypted",
                        "target": var.target,
                    },
                )
            else:
                resp = await client.patch(
                    f"{self.base_url}/v9/projects/{self.project_id}/env/{match['id']}",
                    headers=self._headers(),
                    json={"value": var.value, "target": var.target},
                )
            resp.raise_for_status()

    async def delete_env(self, key: str) -> bool:
        """Returns True if deleted, False if not present."""
        existing = await self.list_env()
        match = next((e for e in existing if e["key"] == key), None)
        if match is None:
            return False
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
            resp = await client.delete(
                f"{self.base_url}/v9/projects/{self.project_id}/env/{match['id']}",
                headers=self._headers(),
            )
            resp.raise_for_status()
        return True


__all__ = ["VercelClient", "VercelEnvVar"]
