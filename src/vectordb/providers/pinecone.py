"""Pinecone provider for vector-db queries (Phase 6e).

Spec: POST {endpoint}/query with {vector, topK, includeMetadata,
includeValues, namespace?, filter?}.  'Api-Key' header.

Response: {matches: [{id, score, metadata, values?}, ...]}.

See Phase 6e spec §6.1.
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
    url = config.endpoint.rstrip("/") + "/query"
    body: dict[str, Any] = {
        "vector": embedding,
        "topK": config.top_k,
        "includeMetadata": True,  # always-on per OAB
        "includeValues": bool(config.include_vector),
    }
    if config.namespace:
        body["namespace"] = config.namespace
    if config.metadata_filter:
        body["filter"] = config.metadata_filter

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if config.api_key:
        headers["Api-Key"] = config.api_key

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise VectorDbProviderError(f"Pinecone query request failed: {exc}") from exc
    if resp.status_code >= 400:
        raise VectorDbProviderError(f"Pinecone query error {resp.status_code}: {resp.text}")

    payload = resp.json()
    matches = payload.get("matches") or []
    text_key = config.text_field or "text"
    results: list[VectorDbResult] = []
    for match in matches:
        metadata_raw = match.get("metadata") or {}
        metadata = dict(metadata_raw) if isinstance(metadata_raw, dict) else {}
        text = metadata.get(text_key) or metadata.get("content") or ""
        results.append(
            VectorDbResult(
                id=str(match.get("id", "")),
                score=float(match.get("score") or 0.0),
                text=str(text),
                metadata=metadata if config.include_metadata else {},
                vector=(match.get("values") if config.include_vector else None),
            )
        )
    return results


__all__ = ["query"]
