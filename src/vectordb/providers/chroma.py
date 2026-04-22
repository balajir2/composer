"""Chroma provider for vector-db queries (Phase 6e).

Spec: POST {endpoint}/api/v1/collections/{collection}/query with
{query_embeddings: [[...]], n_results, where?}.  Response is
batch-indexed (outer list per-query, inner list per-result).
Score = 1 - distance.

See Phase 6e spec §6.3.
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
    url = f"{base}/api/v1/collections/{config.collection}/query"

    body: dict[str, Any] = {
        "query_embeddings": [embedding],
        "n_results": config.top_k,
    }
    if config.metadata_filter:
        body["where"] = config.metadata_filter
    if config.include_vector:
        body["include"] = ["metadatas", "distances", "documents", "embeddings"]

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise VectorDbProviderError(f"Chroma query request failed: {exc}") from exc
    if resp.status_code >= 400:
        raise VectorDbProviderError(f"Chroma query error {resp.status_code}: {resp.text}")

    payload = resp.json()
    ids_batch = payload.get("ids") or []
    if not ids_batch or not ids_batch[0]:
        return []
    ids = ids_batch[0]
    distances = (payload.get("distances") or [[]])[0]
    metadatas = (payload.get("metadatas") or [[]])[0]
    documents = (payload.get("documents") or [[]])[0]
    embeddings_batch = (payload.get("embeddings") or [[]])[0] if config.include_vector else []

    results: list[VectorDbResult] = []
    for i, doc_id in enumerate(ids):
        distance = distances[i] if i < len(distances) else 0.0
        metadata = metadatas[i] if i < len(metadatas) else {}
        text = documents[i] if i < len(documents) else ""
        vector = embeddings_batch[i] if i < len(embeddings_batch) else None
        if config.text_field and isinstance(metadata, dict) and config.text_field in metadata:
            text = metadata[config.text_field]
        results.append(
            VectorDbResult(
                id=str(doc_id),
                score=float(1.0 - (distance or 0.0)),
                text=str(text or ""),
                metadata=(
                    dict(metadata)
                    if (config.include_metadata and isinstance(metadata, dict))
                    else {}
                ),
                vector=list(vector) if vector is not None else None,
            )
        )
    return results


__all__ = ["query"]
