# Phase 6c — Gamma-AI: Design

**Status.** Approved 2026-04-21.
**Related.** Phase 4a HTTP executor (pattern reference), Phase 1 Pydantic workflow models (ADR-0002), Phase 6b LLM-provider-backed executors (similar config).

No new ADR: Gamma is a straightforward HTTP integration. Polling cadence is behavioral (matches OAB) and documented in code comments; doesn't warrant an architectural ADR.

---

## 1. Goal

Ship the `gamma-ai` executor — calls the Gamma.app public API to generate a presentation (or doc/webpage) from a prompt, polls for completion, optionally waits for the PPTX/PDF export URL, and writes `lastOutput = downloadUrl or gammaUrl`.

## 2. Non-goals

- **No SDK.** Gamma publishes no Python SDK; direct HTTP via `httpx.AsyncClient` is the match for OAB.
- **No interrupt/resume semantics.** Gamma's wait is machine-bounded (up to ~5 minutes). It runs inline in the BackgroundTask; the API caller gets the response asynchronously via polling `/executions/{id}` or subscribing to SSE events (Phase 5b).
- **No streaming of intermediate artifacts.** Gamma doesn't expose that; OAB doesn't either.
- **No per-node API-key field.** Single API key via `settings.gamma_api_key` (env var `GAMMA_API_KEY`). User workflows don't carry credentials. If users want multi-tenant Gamma keys, that's a Phase 7/8 auth feature.
- **No UI.** Phase 10.

## 3. Architecture

```
┌──────────────────────────────────────────────────────────┐
│ GammaAiExecutor                                          │
│                                                          │
│  prompt = substitute(data.prompt, state.variables)       │
│                                                          │
│  POST /generations (create) → generationId               │
│                                                          │
│  sleep 60s (initial wait — OAB-compatible cadence)       │
│                                                          │
│  loop (max 4 more minutes):                              │
│    GET /generations/{id} → status                        │
│    if state == 'completed': break                        │
│    if state == 'failed': raise GammaNodeError            │
│    sleep 10s                                             │
│                                                          │
│  if exportAs in {'pptx', 'pdf'} and no downloadUrl:      │
│    loop (max 60s):                                       │
│      GET /generations/{id} → downloadUrl                 │
│      if ready: break                                     │
│      sleep 5s                                            │
│                                                          │
│  write lastOutput = downloadUrl || gammaUrl              │
│  write node_results[id].output = {generationId, urls...} │
└──────────────────────────────────────────────────────────┘
```

**Total max runtime:** 60s initial + 240s polling + 60s export = **6 minutes**. Slightly longer than OAB's 5 min because Composer's 60s export wait is additional rather than inside the 4-min window. Documented.

**Inline execution.** Running inside the BackgroundTask is fine. The checkpointer (`PrismaCheckpointSaver`) persists state before `compiled.ainvoke` — if the API worker dies mid-poll, LangGraph resumes from the checkpoint when the execution is retried (Phase 5a's `aget_state` path). No special handling.

## 4. `GammaAiNodeData` — Pydantic tightening

Current Phase 1 placeholder:
```python
class GammaAiNodeData(BaseNodeData):
    config: dict[str, Any] = Field(default_factory=dict)
```

Replace with explicit fields matching OAB `types.ts:77-85`:

```python
class GammaAiNodeData(BaseNodeData):
    model_config = ConfigDict(populate_by_name=True)

    prompt: str | None = Field(default=None)
    format: Literal["presentation", "document", "social"] = Field(default="presentation")
    text_mode: Literal["generate", "condense", "preserve"] = Field(
        default="generate", alias="textMode"
    )
    num_cards: int | None = Field(default=None, alias="numCards")
    text_amount: Literal["brief", "medium", "detailed"] | None = Field(
        default=None, alias="textAmount"
    )
    image_source: str | None = Field(default=None, alias="imageSource")
    language: str | None = Field(default=None)
    export_as: Literal["pptx", "pdf", "web"] = Field(default="web", alias="exportAs")
```

All fields optional; the API accepts request bodies with just `inputText` + `textMode` + `format`. Enum literals pinned per Gamma's public API docs.

## 5. `settings.gamma_api_key`

Add to `src/config.py`:
```python
gamma_api_key: str = Field(default="", description="Gamma.app API key (https://gamma.app).")
```

Executor reads it via `get_settings().gamma_api_key`; raises `GammaNodeError` if empty.

## 6. `GammaAiExecutor` — implementation

**File:** `src/executors/gamma_ai.py` (new).

```python
"""gamma-ai node executor.

Calls the Gamma.app public API to generate a presentation/document/
webpage from a prompt.  Two-step protocol: POST create → GET poll
until completed or failed.  Optionally waits for the PPTX/PDF
download URL after completion.

Runs inline in the BackgroundTask; total max runtime ~6 minutes.

See Phase 6c spec §3 + §6.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from src.config import get_settings
from src.engine.state import WorkflowStateDict
from src.engine.workflow import GammaAiNode
from src.executors.base import register_executor
from src.variable_substitution import substitute

GAMMA_API_BASE = "https://public-api.gamma.app/v1.0"

INITIAL_WAIT_SECONDS = 60.0
POLL_INTERVAL_SECONDS = 10.0
MAX_POLL_SECONDS = 240.0   # 4 more minutes after initial wait
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
            raise GammaNodeError(
                f"gamma-ai node {self.node.id!r}: GAMMA_API_KEY not configured"
            )

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
            timeout=30.0,
        ) as client:
            generation_id = await self._create_generation(client, request_body)
            status = await self._poll_until_complete(client, generation_id)
            if (
                self.node.data.export_as in {"pptx", "pdf"}
                and not status.get("downloadUrl")
            ):
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
                f"gamma-ai node {self.node.id!r}: Gamma API error "
                f"{resp.status_code}: {resp.text}"
            )
        payload = resp.json()
        generation_id = (
            payload.get("id")
            or payload.get("generationId")
            or (payload.get("data") or {}).get("id")
        )
        if not generation_id:
            raise GammaNodeError(
                f"gamma-ai node {self.node.id!r}: no generation id in response: "
                f"{payload}"
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
```

**Design notes:**

- **No early hard-error on transient GET failure during polling** — `continue` and retry. Gamma occasionally 502s under load; transient failures shouldn't fail the whole workflow.
- **Timeout behavior.** After 4 min of polling without `completed`/`failed`, we don't raise; we return the last known status (matches OAB). `lastOutput` may be empty string if no URL is available yet. Downstream nodes see whatever `lastOutput` is.
- **Variable substitution.** Only `prompt` — other fields are config (enums, integers).

## 7. Error model

| Condition                                 | Exception          | Execution outcome |
|-------------------------------------------|--------------------|-------------------|
| `GAMMA_API_KEY` not set                   | `GammaNodeError`   | `failed`          |
| POST /generations network error / 4xx/5xx | `GammaNodeError`   | `failed`          |
| No generation id in create response       | `GammaNodeError`   | `failed`          |
| Polled status `state='failed'`            | `GammaNodeError`   | `failed`          |
| Polling timeout (5 min)                   | (none)             | `completed` — `lastOutput` may be empty or webUrl depending on final GET |
| Export URL timeout (60s)                  | (none)             | `completed` — `lastOutput` = webUrl fallback |

## 8. Test plan

### 8.1 Unit tests (`tests/unit/executors/test_gamma_ai.py`)

Use `pytest-httpx` (already in deps — Phase 3a uses it). Mock the Gamma HTTP endpoints.

1. Successful create → immediate completion on first poll → `lastOutput = gammaUrl`.
2. Create returns `generationId` under various keys (`id`, `generationId`, `data.id`) — all parsed correctly.
3. Polling returns `state='pending'` twice, then `'completed'` → `lastOutput` correct.
4. Polling returns `state='failed'` → `GammaNodeError`.
5. `exportAs='pptx'` and completion returns no `downloadUrl` → enters export-wait loop → eventually returns `downloadUrl` → `lastOutput` = downloadUrl.
6. `exportAs='pptx'` and export wait times out → `lastOutput` = gammaUrl fallback.
7. Polling timeout (5 min elapsed simulated via monkeypatched `asyncio.sleep`) → returns with `state='processing'` message; execution completes (not fails).
8. Transient 502 during polling → retries; eventually completes.
9. Missing API key → `GammaNodeError` ("GAMMA_API_KEY not configured").
10. Variable substitution: prompt with `{{topic}}` → substituted correctly.
11. Request body construction: all optional fields map to correct Gamma API fields (`textOptions`, `numCards`, `imageOptions`, `exportAs` omitted when 'web').
12. Executor registered in `_REGISTRY`.

**Sleep-mocking:** monkeypatch `asyncio.sleep` to a no-op so tests don't actually wait 60s. Test the polling logic by stubbing responses in sequence.

### 8.2 Integration — skipped for Phase 6c

Gamma's API requires an active account + API key. If the user has one in `.env`, they can run the integration manually; we don't block phase exit on it. CHANGELOG documents this.

**Optional:** if `GAMMA_API_KEY` is set at phase-exit time, the controller (not a subagent) runs a minimal smoke test. If skipped, we note "integration manual" in CHANGELOG.

## 9. Phase-exit checklist

- [ ] All unit tests green.
- [ ] Ruff + format + pyright strict clean.
- [ ] (Optional) manual integration smoke test passed if `GAMMA_API_KEY` present.
- [ ] `CHANGELOG.md` — Phase 6c section.
- [ ] `CLAUDE.md` — phase table: 6c → ✅, next is 6d (arcade).

## 10. Self-review

- **Placeholders:** None.
- **Internal consistency:** Field names `prompt` / `format` / `text_mode` / `num_cards` / `text_amount` / `image_source` / `language` / `export_as` match across §4 Pydantic, §6 executor, §8 tests.
- **Scope:** One executor, one Pydantic tightening, one settings field. Single-plan territory.
- **Ambiguity:** Polling timeout behavior pinned (no raise, last-known status, `lastOutput` may be empty). Transient 502 behavior pinned (retry). Export-wait behavior pinned (fallback to gammaUrl after 60s).

## 11. Risks + future

- **5-minute execution time.** With the ExecutionEventBus (Phase 5b), subscribers see no intermediate events beyond `node-start(gamma-ai)`. A future improvement could emit a custom `gamma-ai-polling` event per poll iteration. Out of scope for 6c.
- **API key rotation.** Single `GAMMA_API_KEY` env var. Multi-tenant / per-user keys is Phase 7/8.
- **Gamma API schema drift.** Their public API is versioned `v1.0`. If they ship v1.1 with breaking changes, tests catch it.
