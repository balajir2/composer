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
import io
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx

from src.storage_providers.local import LocalFilesystemProvider

if TYPE_CHECKING:
    from src.storage_providers.base import FileRef, FileStorageProvider

logger = logging.getLogger(__name__)

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


class UnsupportedFileTypeError(ValueError):
    """Raised when a claimed file's extension has no known text-extraction path."""


def _extract_text(filename: str, raw: bytes) -> str:
    lower = filename.lower()
    if lower.endswith((".txt", ".md", ".markdown")):
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("utf-8-sig")
    if lower.endswith(".pdf"):
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(raw))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    if lower.endswith(".docx"):
        from docx import Document

        doc = Document(io.BytesIO(raw))
        return "\n".join(p.text for p in doc.paragraphs)
    raise UnsupportedFileTypeError(f"no extraction path for {filename!r}")


async def _trigger_workflow(*, config: WatchConfig, input_payload: dict[str, str]) -> None:
    url = f"{config.workflow_api_url}/api/run/{config.external_slug}"
    headers = {"Authorization": f"Bearer {config.api_key}"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
        resp = await client.post(url, headers=headers, json={"input": input_payload})
    if resp.status_code >= 400:
        raise RuntimeError(f"trigger failed (HTTP {resp.status_code}): {resp.text[:500]}")


async def claim_file(provider: FileStorageProvider, ref: FileRef, config: WatchConfig) -> None:
    """Process exactly one claimed file: extract, trigger, then move to
    dest (success) or error (extraction or trigger failure). One file's
    failure never raises — it's logged and the file lands in error_path."""
    try:
        raw = await provider.read_file(ref)
        text = _extract_text(ref.name, raw)
        await _trigger_workflow(config=config, input_payload={config.target_input_variable: text})
    except Exception:
        logger.exception("composer watch: failed to claim %s", ref.name)
        await provider.move_file(ref, config.error_path)
        return

    await provider.move_file(ref, config.dest_path)


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
            refs = await provider.list_new_files(config.source_path)
            for ref in refs:
                await claim_file(provider, ref, config)
        except Exception:
            logger.exception("composer watch: unexpected error in poll loop")
        await asyncio.sleep(config.poll_interval_seconds)


__all__ = ["WatchConfig", "claim_file", "run_watch_loop"]
