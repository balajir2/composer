"""Qdrant provider for vector-db queries (Phase 6e).

Spec: POST {endpoint}/collections/{collection}/points/search with
{vector, limit, with_payload, with_vector, filter?}.  'api-key' header
optional.

Response: {result: [{id, score, payload, vector?}, ...]}.

See Phase 6e spec §6.2.
"""

from __future__ import annotations

from typing import Any, cast

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

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
        try:
            resp = await client.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise VectorDbProviderError(f"Qdrant query request failed: {exc}") from exc
    if resp.status_code >= 400:
        raise VectorDbProviderError(f"Qdrant query error {resp.status_code}: {resp.text}")

    payload: dict[str, Any] = resp.json()
    raw_results = cast("list[Any]", payload.get("result") or [])
    text_key = config.text_field or "text"
    results: list[VectorDbResult] = []
    for item in raw_results:
        payload_raw = cast("Any", item.get("payload") or {})
        md: dict[str, Any] = (
            dict(cast("dict[str, Any]", payload_raw)) if isinstance(payload_raw, dict) else {}
        )
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


async def upsert(
    documents: list[UpsertDocument],
    config: UpsertConfig,
) -> UpsertResult:
    """Qdrant PUT /collections/{collection}/points (upsert mode).

    Body: `{points: [{id, vector, payload}, ...]}`.  Qdrant requires
    point ids to be either UUIDs or unsigned 64-bit ints; we hash the
    chunk text into a UUIDv5 derived from a stable namespace so re-
    upserts of the same content overwrite cleanly.  The chunk text is
    written into the payload under `text_field` so the query path can
    return it.
    """
    import hashlib
    import uuid

    if not documents:
        return UpsertResult(inserted_count=0, ids=[])

    # Stable namespace UUID — anything constant works; the value matters
    # only for the uuid5 derivation function being deterministic.
    _NS = uuid.UUID("00000000-0000-0000-0000-00636f6d706f73")  # 'compos' suffix

    points: list[dict[str, Any]] = []
    out_ids: list[str] = []
    for doc in documents:
        if doc.id:
            point_id = doc.id
        elif doc.text:
            digest = hashlib.sha1(doc.text.encode("utf-8")).hexdigest()
            point_id = str(uuid.uuid5(_NS, digest))
        else:
            point_id = str(uuid.uuid4())
        out_ids.append(point_id)

        payload: dict[str, Any] = dict(doc.metadata)
        payload[config.text_field] = doc.text
        points.append({"id": point_id, "vector": doc.embedding, "payload": payload})

    base = config.endpoint.rstrip("/")
    url = f"{base}/collections/{config.collection}/points?wait=true"

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if config.api_key:
        headers["api-key"] = config.api_key

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
        try:
            resp = await client.put(url, headers=headers, json={"points": points})
        except httpx.HTTPError as exc:
            raise VectorDbProviderError(f"Qdrant upsert request failed: {exc}") from exc
    if resp.status_code >= 400:
        raise VectorDbProviderError(f"Qdrant upsert error {resp.status_code}: {resp.text}")

    return UpsertResult(inserted_count=len(out_ids), ids=out_ids)


__all__ = ["query", "upsert"]
