"""`composer watch` — polls a FileStorageProvider source, extracts text from
newly-claimed files, triggers a production workflow via the existing
external-invoke API, and moves the file to dest/error based on outcome.

Reuses POST /api/run/{slug} (ck_ bearer auth) rather than a new endpoint —
this is precisely the automated, non-interactive triggering mechanism that
endpoint already exists for. See
docs/archive/phase-history/specs/2026-07-11-file-storage-provider-framework-design.md §B.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx

from src.storage_providers.local import LocalFilesystemProvider
from src.storage_providers.text_extraction import extract_text
from src.warn_throttle import WarnThrottle

if TYPE_CHECKING:
    from collections.abc import Callable

    from src.storage_providers.base import FileRef, FileStorageProvider

logger = logging.getLogger(__name__)

_NOT_RUNNABLE_WARNINGS = WarnThrottle(interval_seconds=900)

# Per-workflow back-off after a 422. The window starts at the poll interval and
# doubles on each consecutive 422, capped here, so a workflow fixed in the Designer
# is picked up within BACKOFF_MAX_SECONDS (or one poll interval if that is longer).
BACKOFF_MAX_SECONDS = 900.0
BACKOFF_FACTOR = 2.0


class SlugBackoff:
    """Per-slug back-off: `blocked(slug)` is True until the window set by the latest
    `record_unrunnable` expires; `reset(slug)` clears it and returns True if it was active."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._failures: dict[str, int] = {}
        self._retry_at: dict[str, float] = {}

    def blocked(self, slug: str) -> bool:
        return self._clock() < self._retry_at.get(slug, 0.0)

    def retry_at(self, slug: str) -> float:
        return self._retry_at.get(slug, 0.0)

    def record_unrunnable(self, slug: str, base_seconds: float) -> tuple[float, bool]:
        """Extend the window; return (window seconds, whether this is the first 422)."""
        failures = self._failures.get(slug, 0)
        window = min(
            base_seconds * BACKOFF_FACTOR**failures, max(BACKOFF_MAX_SECONDS, base_seconds)
        )
        self._failures[slug] = failures + 1
        self._retry_at[slug] = self._clock() + window
        return window, failures == 0

    def reset(self, slug: str) -> bool:
        self._retry_at.pop(slug, None)
        return self._failures.pop(slug, 0) > 0


_BACKOFF = SlugBackoff()


class WorkflowNotRunnableError(RuntimeError):
    """The server answered 422: the workflow cannot run as drawn (an unconnected branch)."""


class TriggerFailedError(RuntimeError):
    """The server answered with another 4xx/5xx: it is reachable and the workflow is not
    the problem the 422 reports."""


_PROVIDERS: dict[str, type[FileStorageProvider]] = {
    "local": LocalFilesystemProvider,
}


@dataclass(frozen=True, kw_only=True)
class WatchConfig:
    workflow_api_url: str  # backend base URL, e.g. https://api.example.com
    external_slug: str
    api_key: str  # ck_...
    provider: str
    source_path: str
    dest_path: str
    error_path: str
    target_input_variable: str
    poll_interval_seconds: int


async def _trigger_workflow(*, config: WatchConfig, input_payload: dict[str, str]) -> None:
    url = f"{config.workflow_api_url}/api/run/{config.external_slug}"
    headers = {"Authorization": f"Bearer {config.api_key}"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
        resp = await client.post(url, headers=headers, json={"input": input_payload})
    if resp.status_code == 422:
        raise WorkflowNotRunnableError(resp.text[:500])
    if resp.status_code >= 400:
        raise TriggerFailedError(f"trigger failed (HTTP {resp.status_code}): {resp.text[:500]}")


async def claim_file(provider: FileStorageProvider, ref: FileRef, config: WatchConfig) -> None:
    """Process exactly one claimed file: extract, trigger, then move to
    dest (success) or error (extraction or trigger failure). One file's
    failure never raises — it's logged and the file lands in error_path.

    Back-off: a 422 starts or doubles the slug's back-off window (the caller skips the
    slug's files until it expires). Any answer from the server that is not a 422 (success,
    5xx, other 4xx) resets it: those mean the workflow is runnable or the failure is the
    file's or the network's, and the file moves to error_path so it is not re-read anyway.
    An extraction failure never reached the server, so it leaves the back-off unchanged."""
    slug = config.external_slug
    try:
        raw = await provider.read_file(ref)
        text = extract_text(ref.name, raw)
        await _trigger_workflow(config=config, input_payload={config.target_input_variable: text})
    except WorkflowNotRunnableError as exc:
        # The workflow is unfinished, not the file: leave it in place so it is picked
        # up once the workflow is fixed (same as the server-side poller). The warning
        # is throttled and the slug backs off so the file is not re-read every poll.
        window, first = _BACKOFF.record_unrunnable(slug, config.poll_interval_seconds)
        if first:
            logger.info(
                "composer watch: workflow %s backing off %.0fs after HTTP 422 (doubles per "
                "consecutive 422, max %.0fs); files are left in place",
                slug,
                window,
                BACKOFF_MAX_SECONDS,
            )
        if _NOT_RUNNABLE_WARNINGS.allow(slug):
            logger.warning(
                "composer watch: workflow %s cannot run yet (%s); leaving %s in place "
                "and retrying until it is fixed",
                slug,
                exc,
                ref.name,
            )
        return
    except Exception as exc:
        if isinstance(exc, TriggerFailedError):
            _note_recovered(slug)
        logger.exception("composer watch: failed to claim %s", ref.name)
        await provider.move_file(ref, config.error_path)
        return

    _note_recovered(slug)
    await provider.move_file(ref, config.dest_path)


def _note_recovered(slug: str) -> None:
    if _BACKOFF.reset(slug):
        logger.info("composer watch: workflow %s recovered; back-off cleared", slug)


async def poll_once(provider: FileStorageProvider, config: WatchConfig) -> None:
    """One poll: claim each pending file unless the slug is backing off.

    A backed-off slug skips the whole pass (no read, extract or POST of its files),
    but the listing still runs so the provider's stability tracking stays current."""
    refs = await provider.list_new_files(config.source_path)
    for ref in refs:
        if _BACKOFF.blocked(config.external_slug):
            break
        await claim_file(provider, ref, config)


async def run_watch_loop(config: WatchConfig) -> None:
    """Poll forever until cancelled (Ctrl+C)."""
    provider_cls = _PROVIDERS.get(config.provider)
    if provider_cls is None:
        raise ValueError(f"unknown provider {config.provider!r}; registered: {sorted(_PROVIDERS)}")
    provider = provider_cls()

    logger.info(
        "composer watch: polling %s every %ds -> workflow slug %s",
        config.source_path,
        config.poll_interval_seconds,
        config.external_slug,
    )
    while True:
        try:
            await poll_once(provider, config)
        except Exception:
            logger.exception("composer watch: unexpected error in poll loop")
        await asyncio.sleep(config.poll_interval_seconds)


__all__ = ["WatchConfig", "claim_file", "run_watch_loop"]
