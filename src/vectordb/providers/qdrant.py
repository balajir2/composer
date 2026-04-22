"""Qdrant provider for vector-db queries (Phase 6e).

Spec: POST {endpoint}/collections/{collection}/points/search with
{vector, limit, with_payload, with_vector, filter?}.  'api-key' header
optional.

Response: {result: [{id, score, payload, vector?}, ...]}.

See Phase 6e spec §6.2.
"""

from __future__ import annotations

from typing import Any

import httpx

from src.vectordb.providers.base import (
    QueryConfig,
    VectorDbProviderError,
    VectorDbResult,
)


async def query(
    embedding: list[float],
    config: QueryConfig,
) -> list[VectorDbResult]:
    base = config.endpoint.rstrip("/")
    url = f"{base}/collections/{config.collection}/points/search"

    body: dict[str, Any] = {
        "vector": embedding,
        "limit": config.top_k,
        "with_payload": True,
        "with_vector": bool(config.include_vector),
    }
    if config.metadata_filter:
        body["filter"] = config.metadata_filter

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if config.api_key:
        headers["api-key"] = config.api_key

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise VectorDbProviderError(f"Qdrant query request failed: {exc}") from exc
    if resp.status_code >= 400:
        raise VectorDbProviderError(f"Qdrant query error {resp.status_code}: {resp.text}")

    payload = resp.json()
    raw_results = payload.get("result") or []
    text_key = config.text_field or "text"
    results: list[VectorDbResult] = []
    for item in raw_results:
        payload_raw = item.get("payload") or {}
        md = dict(payload_raw) if isinstance(payload_raw, dict) else {}
        text = md.get(text_key) or md.get("content") or md.get("page_content") or ""
        results.append(
            VectorDbResult(
                id=str(item.get("id", "")),
                score=float(item.get("score") or 0.0),
                text=str(text),
                metadata=md if config.include_metadata else {},
                vector=(item.get("vector") if config.include_vector else None),
            )
        )
    return results


__all__ = ["query"]
