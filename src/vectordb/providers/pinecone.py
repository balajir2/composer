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
    UpsertConfig,
    UpsertDocument,
    UpsertResult,
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

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
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


async def upsert(
    documents: list[UpsertDocument],
    config: UpsertConfig,
) -> UpsertResult:
    """Pinecone POST {endpoint}/vectors/upsert.

    Body shape: `{vectors: [{id, values, metadata}, ...], namespace?}`.
    The chunk text is merged into the metadata under the configured
    `text_field` so the query path can return it via the same key.
    Pinecone needs an `id` for every vector; we hash the text when
    the caller didn't supply one so re-runs of the same content
    overwrite cleanly instead of duplicating.
    """
    import hashlib
    import uuid

    if not documents:
        return UpsertResult(inserted_count=0, ids=[])

    vectors: list[dict[str, Any]] = []
    out_ids: list[str] = []
    for doc in documents:
        if doc.id:
            doc_id = doc.id
        elif doc.text:
            # Stable hash so the same chunk re-upserted gets the same
            # id — Pinecone overwrites by id, so this turns repeat
            # ingestions into idempotent updates.
            doc_id = hashlib.sha1(doc.text.encode("utf-8")).hexdigest()[:32]
        else:
            doc_id = uuid.uuid4().hex
        out_ids.append(doc_id)

        merged_metadata: dict[str, Any] = dict(doc.metadata)
        merged_metadata[config.text_field] = doc.text
        vectors.append(
            {
                "id": doc_id,
                "values": doc.embedding,
                "metadata": merged_metadata,
            }
        )

    body: dict[str, Any] = {"vectors": vectors}
    if config.namespace:
        body["namespace"] = config.namespace

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if config.api_key:
        headers["Api-Key"] = config.api_key

    url = config.endpoint.rstrip("/") + "/vectors/upsert"
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
        try:
            resp = await client.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise VectorDbProviderError(f"Pinecone upsert request failed: {exc}") from exc
    if resp.status_code >= 400:
        raise VectorDbProviderError(f"Pinecone upsert error {resp.status_code}: {resp.text}")

    payload = resp.json()
    # Pinecone returns {"upsertedCount": N}; we trust our own count too
    # since we know exactly which vectors we sent.  upsertedCount can
    # exceed our document count when filter-driven deletes happened
    # (won't on a plain upsert, but we use len(out_ids) to be safe).
    inserted = int(payload.get("upsertedCount") or len(out_ids))
    return UpsertResult(inserted_count=inserted, ids=out_ids)


__all__ = ["query", "upsert"]
