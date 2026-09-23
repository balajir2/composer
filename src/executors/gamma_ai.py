"""gamma-ai node executor.

Calls the Gamma.app public API to generate a presentation/document/
webpage from a prompt.  Two-step protocol: POST create → GET poll
until completed or failed.  Optionally waits for the PPTX/PDF
download URL after completion.

Runs inline in the BackgroundTask; total max runtime ~6 minutes.

Module-scope constants (INITIAL_WAIT_SECONDS etc.) are monkeypatched
by tests to avoid actually sleeping 60s+.

See Phase 6c spec §3 + §6.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import httpx

from src.config import get_settings
from src.executors.base import register_executor
from src.variable_substitution import substitute

if TYPE_CHECKING:
    from src.engine.state import WorkflowStateDict
    from src.engine.workflow import GammaAiNode

GAMMA_API_BASE = "https://public-api.gamma.app/v1.0"

INITIAL_WAIT_SECONDS = 60.0
POLL_INTERVAL_SECONDS = 10.0
MAX_POLL_SECONDS = 240.0  # 4 more minutes after initial wait
EXPORT_WAIT_SECONDS = 60.0
EXPORT_POLL_INTERVAL = 5.0


class GammaNodeError(RuntimeError):
    """Raised when the Gamma API call fails or returns failed state."""


@register_executor("gamma-ai")
class GammaAiExecutor:
    def __init__(self, node: GammaAiNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        api_key = get_settings().gamma_api_key
        if not api_key:
            raise GammaNodeError(f"gamma-ai node {self.node.id!r}: GAMMA_API_KEY not configured")

        prompt_template = self.node.data.prompt or "Create a presentation about AI"
        prompt = substitute(prompt_template, state)

        request_body: dict[str, Any] = {
            "inputText": prompt,
            "textMode": self.node.data.text_mode,
            "format": self.node.data.format,
        }
        text_options: dict[str, Any] = {}
        if self.node.data.text_amount:
            text_options["amount"] = self.node.data.text_amount
        if self.node.data.language:
            text_options["language"] = self.node.data.language
        if text_options:
            request_body["textOptions"] = text_options
        if self.node.data.num_cards is not None:
            request_body["numCards"] = self.node.data.num_cards
        if self.node.data.image_source:
            request_body["imageOptions"] = {"source": self.node.data.image_source}
        if self.node.data.export_as != "web":
            request_body["exportAs"] = self.node.data.export_as

        async with httpx.AsyncClient(
            base_url=GAMMA_API_BASE,
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            timeout=httpx.Timeout(30.0, connect=5.0),
        ) as client:
            generation_id = await self._create_generation(client, request_body)
            status = await self._poll_until_complete(client, generation_id)
            if self.node.data.export_as in {"pptx", "pdf"} and not status.get("downloadUrl"):
                status = await self._wait_for_export_url(client, generation_id, status)

        url = status.get("gammaUrl") or status.get("url") or status.get("webUrl")
        download_url = status.get("downloadUrl")
        output_url = download_url or url

        output = {
            "generationId": generation_id,
            "url": url,
            "downloadUrl": download_url,
            "status": status.get("state") or status.get("status"),
        }

        return {
            "variables": {"lastOutput": output_url or ""},
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "input": {
                        "prompt": prompt,
                        "format": self.node.data.format,
                        "export_as": self.node.data.export_as,
                    },
                    "output": output,
                }
            },
        }

    async def _create_generation(
        self,
        client: httpx.AsyncClient,
        body: dict[str, Any],
    ) -> str:
        try:
            resp = await client.post("/generations", json=body)
        except httpx.HTTPError as exc:
            raise GammaNodeError(
                f"gamma-ai node {self.node.id!r}: POST /generations failed: {exc}"
            ) from exc
        if resp.status_code >= 400:
            raise GammaNodeError(
                f"gamma-ai node {self.node.id!r}: Gamma API error {resp.status_code}: {resp.text}"
            )
        payload: dict[str, Any] = resp.json()
        nested_data: dict[str, Any] = payload.get("data") or {}
        generation_id = payload.get("id") or payload.get("generationId") or nested_data.get("id")
        if not generation_id:
            raise GammaNodeError(
                f"gamma-ai node {self.node.id!r}: no generation id in response: {payload}"
            )
        return str(generation_id)

    async def _poll_until_complete(
        self,
        client: httpx.AsyncClient,
        generation_id: str,
    ) -> dict[str, Any]:
        # OAB-compatible cadence: 60s initial wait, then 10s interval for 4 more min.
        await asyncio.sleep(INITIAL_WAIT_SECONDS)

        deadline = asyncio.get_running_loop().time() + MAX_POLL_SECONDS
        while asyncio.get_running_loop().time() < deadline:
            try:
                resp = await client.get(f"/generations/{generation_id}")
            except httpx.HTTPError:
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                continue
            if resp.status_code >= 400:
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                continue

            status = resp.json()
            state_val = status.get("state") or status.get("status")
            if state_val == "completed":
                return status
            if state_val == "failed":
                raise GammaNodeError(
                    f"gamma-ai node {self.node.id!r}: generation failed: "
                    f"{status.get('error', 'unknown error')}"
                )
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

        # Timeout — return last known status (OAB behavior: workflow continues)
        try:
            final = await client.get(f"/generations/{generation_id}")
            if final.status_code < 400:
                return final.json()
        except httpx.HTTPError:
            pass
        return {"state": "processing", "message": "timeout reached"}

    async def _wait_for_export_url(
        self,
        client: httpx.AsyncClient,
        generation_id: str,
        last_status: dict[str, Any],
    ) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + EXPORT_WAIT_SECONDS
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(EXPORT_POLL_INTERVAL)
            try:
                resp = await client.get(f"/generations/{generation_id}")
            except httpx.HTTPError:
                continue
            if resp.status_code >= 400:
                continue
            status = resp.json()
            if status.get("downloadUrl"):
                return status
        return last_status


__all__ = ["GAMMA_API_BASE", "GammaAiExecutor", "GammaNodeError"]
