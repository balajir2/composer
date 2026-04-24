"""Fetch the current list of chat models from each LLM provider.

For every supported provider we hit its `/models` endpoint with the
admin-configured API key.  Results are cached per-provider for 5 minutes
so the dropdown doesn't round-trip on every Agent edit.

Exposed as `GET /llm-models/available?provider=<p>` — used by the
Designer's Agent panel to populate its model dropdown dynamically.  When
a provider call fails (bad key, network, 404) we return the DB-curated
fallback from the LlmModel table so the designer isn't left empty-handed.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from src.config import get_settings
from src.security.auth import get_current_user_id
from src.storage.db import get_db

if TYPE_CHECKING:
    from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]

logger = logging.getLogger(__name__)

router = APIRouter(tags=["llm-models"])

_CACHE_TTL_SECONDS = 300
_HTTP_TIMEOUT = 10.0

# provider name → (fetched_at_unix_epoch, models)
_cache: dict[str, tuple[float, list[LiveModel]]] = {}
_cache_lock = asyncio.Lock()


class LiveModel(BaseModel):
    """Normalized model entry returned to the frontend dropdown."""

    # Alias so the wire shape matches every other LLM-models endpoint
    # (camelCase).  protected_namespaces=() silences Pydantic's warning
    # that field names starting with "model_" collide with its reserved
    # namespace.
    model_config = ConfigDict(populate_by_name=True, protected_namespaces=())

    model_id: str = Field(..., alias="modelId")
    label: str | None = None
    source: str  # "live" (from provider API) or "db" (admin-curated fallback)


class AvailableModelsResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    provider: str
    models: list[LiveModel]


# ─── Provider-specific fetchers ─────────────────────────────────────────────


@dataclass(frozen=True)
class _ProviderSpec:
    method: str
    url: str
    headers_fn: Any
    parser: Any
    settings_key: str


def _anthropic_headers(key: str) -> dict[str, str]:
    return {"x-api-key": key, "anthropic-version": "2023-06-01"}


def _bearer_headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _no_headers(_key: str) -> dict[str, str]:
    return {}


def _parse_anthropic(data: dict[str, Any]) -> list[LiveModel]:
    """Anthropic returns {data: [{id, display_name, type}, ...]}."""
    out: list[LiveModel] = []
    for item in data.get("data") or []:
        if not isinstance(item, dict):
            continue
        mid = item.get("id")
        if not isinstance(mid, str) or not mid:
            continue
        label = item.get("display_name") if isinstance(item.get("display_name"), str) else None
        out.append(LiveModel(modelId=mid, label=label, source="live"))
    return out


def _parse_openai_compat(data: dict[str, Any]) -> list[LiveModel]:
    """OpenAI + Groq use the same envelope: {data: [{id, owned_by, ...}]}.

    Filters out non-chat models (embeddings, audio, images, moderation).
    """
    non_chat_markers = (
        "embedding",
        "whisper",
        "tts",
        "dall-e",
        "stable-",
        "omni-moderation",
    )
    out: list[LiveModel] = []
    for item in data.get("data") or []:
        if not isinstance(item, dict):
            continue
        mid = item.get("id")
        if not isinstance(mid, str) or not mid:
            continue
        lowered = mid.lower()
        if any(marker in lowered for marker in non_chat_markers):
            continue
        out.append(LiveModel(modelId=mid, source="live"))
    return out


def _parse_google(data: dict[str, Any]) -> list[LiveModel]:
    """Gemini: {models: [{name: "models/gemini-1.5-pro", supportedGenerationMethods}]}.

    Keep only models that support generateContent (chat-capable).
    """
    out: list[LiveModel] = []
    for item in data.get("models") or []:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name:
            continue
        methods = item.get("supportedGenerationMethods") or []
        if isinstance(methods, list) and "generateContent" not in methods:
            continue
        # "models/gemini-1.5-pro" → "gemini-1.5-pro"
        mid = name.split("/", 1)[1] if "/" in name else name
        label = item.get("displayName") if isinstance(item.get("displayName"), str) else None
        out.append(LiveModel(modelId=mid, label=label, source="live"))
    return out


_PROVIDERS: dict[str, _ProviderSpec] = {
    "anthropic": _ProviderSpec(
        method="GET",
        url="https://api.anthropic.com/v1/models",
        headers_fn=_anthropic_headers,
        parser=_parse_anthropic,
        settings_key="anthropic_api_key",
    ),
    "openai": _ProviderSpec(
        method="GET",
        url="https://api.openai.com/v1/models",
        headers_fn=_bearer_headers,
        parser=_parse_openai_compat,
        settings_key="openai_api_key",
    ),
    "groq": _ProviderSpec(
        method="GET",
        url="https://api.groq.com/openai/v1/models",
        headers_fn=_bearer_headers,
        parser=_parse_openai_compat,
        settings_key="groq_api_key",
    ),
    "google": _ProviderSpec(
        method="GET",
        url="https://generativelanguage.googleapis.com/v1beta/models",
        headers_fn=_no_headers,
        parser=_parse_google,
        settings_key="google_api_key",
    ),
}


async def _fetch_live(provider: str) -> list[LiveModel]:
    """Call the provider's /models endpoint and return a normalized list.

    Raises on any failure — callers decide whether to fall back or surface.
    """
    spec = _PROVIDERS[provider]
    settings = get_settings()
    key = getattr(settings, spec.settings_key, "") or ""
    if not key:
        raise RuntimeError(f"{provider.upper()} API key not configured")

    url = spec.url
    if provider == "google":
        # Gemini takes the key as a query param.
        url = f"{spec.url}?key={key}"

    async with httpx.AsyncClient(timeout=httpx.Timeout(_HTTP_TIMEOUT, connect=5.0)) as client:
        resp = await client.request(
            spec.method, url, headers=spec.headers_fn(key) if provider != "google" else {}
        )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"{provider} /models returned HTTP {resp.status_code}: {resp.text[:200]}"
        )
    data: dict[str, Any] = resp.json()
    models: list[LiveModel] = spec.parser(data)
    # Stable, alphabetical so the dropdown isn't a moving target.
    models.sort(key=lambda m: m.model_id.lower())
    return models


async def _db_fallback(db: Prisma, provider: str) -> list[LiveModel]:  # pyright: ignore[reportUnknownParameterType]
    rows = await db.llmmodel.find_many(  # pyright: ignore[reportAttributeAccessIssue]
        where={"provider": provider, "enabled": True},
        order=[{"modelId": "asc"}],
    )
    return [
        LiveModel(
            modelId=r.modelId,
            label=r.label,
            source="db",
        )
        for r in rows
    ]


@router.get("/llm-models/available", response_model=AvailableModelsResponse)
async def list_available_models(
    provider: str = Query(..., description="Provider id: anthropic|openai|google|groq"),
    refresh: bool = Query(
        default=False,
        description="Bypass the 5-minute in-process cache and re-hit the provider.",
    ),
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    _user_id: str = Depends(get_current_user_id),
) -> AvailableModelsResponse:  # pyright: ignore[reportUnusedFunction]
    if provider not in _PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported provider: {provider}",
        )

    now = time.monotonic()
    if not refresh:
        cached = _cache.get(provider)
        if cached and now - cached[0] < _CACHE_TTL_SECONDS:
            return AvailableModelsResponse(provider=provider, models=cached[1])

    async with _cache_lock:
        # Double-check inside the lock in case another request filled the cache
        # while we waited (skipped when caller forced a refresh).
        if not refresh:
            cached = _cache.get(provider)
            if cached and now - cached[0] < _CACHE_TTL_SECONDS:
                return AvailableModelsResponse(provider=provider, models=cached[1])
        try:
            models = await _fetch_live(provider)
            _cache[provider] = (now, models)
            return AvailableModelsResponse(provider=provider, models=models)
        except Exception as exc:
            logger.warning(
                "live model fetch failed for %s — falling back to DB. %s",
                provider,
                exc,
            )
            fallback = await _db_fallback(db, provider)
            # Don't cache failures; try live again on next request.
            return AvailableModelsResponse(provider=provider, models=fallback)


__all__ = ["AvailableModelsResponse", "LiveModel", "router"]
