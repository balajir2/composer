"""Milvus provider for vector-db queries (Phase 6e).

Spec: POST {endpoint}/v1/vector/search with {collectionName, vector,
limit, filter, outputFields: ['*']}.  Bearer auth.

Response: {data: [{id, distance, ...fields}, ...]}.

Milvus uses DSL string filters, not JSON dicts — OAB logs a warning
and skips when metadata_filter dict is non-empty.  We match.

See Phase 6e spec §6.5.
"""

from __future__ import annotations

import logging
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

logger = logging.getLogger(__name__)


async def query(
    embedding: list[float],
    config: QueryConfig,
) -> list[VectorDbResult]:
    base = config.endpoint.rstrip("/")
    url = f"{base}/v1/vector/search"

    # Milvus expects a DSL string filter, not a JSON object.  We log + skip.
    if config.metadata_filter:
        logger.warning(
            "milvus provider: metadata_filter requires a DSL expression string; "
            "dict filter will be ignored"
        )

    body: dict[str, Any] = {
        "collectionName": config.collection,
        "vector": embedding,
        "limit": config.top_k,
        "filter": "",
        "outputFields": ["*"],
    }

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
        try:
            resp = await client.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise VectorDbProviderError(f"Milvus query request failed: {exc}") from exc
    if resp.status_code >= 400:
        raise VectorDbProviderError(f"Milvus query error {resp.status_code}: {resp.text}")

    payload = resp.json()
    items = payload.get("data") or []
    text_key = config.text_field or "text"
    results: list[VectorDbResult] = []
    for item in items:
        item_dict = dict(item) if isinstance(item, dict) else {}
        text = item_dict.get(text_key) or item_dict.get("content") or ""
        results.append(
            VectorDbResult(
                id=str(item_dict.get("id", "")),
                score=float(item_dict.get("distance") or 0.0),
                text=str(text),
                metadata=(item_dict if config.include_metadata else {}),
                vector=(item_dict.get("vector") if config.include_vector else None),
            )
        )
    return results


async def upsert(
    documents: list[UpsertDocument],
    config: UpsertConfig,
) -> UpsertResult:
    """Milvus POST /v1/vector/insert (Zilliz Cloud REST shape).

    Body: `{collectionName, data: [{id?, vector, ...metadata}, ...]}`.
    Milvus stores top-level fields per the collection schema, so the
    chunk text and any metadata keys ride alongside `vector`.  Without
    a stored id Milvus auto-generates an integer id; we hash text into
    a positive 63-bit int so re-upserts are stable.
    """
    import hashlib

    if not documents:
        return UpsertResult(inserted_count=0, ids=[])

    rows: list[dict[str, Any]] = []
    out_ids: list[str] = []
    for doc in documents:
        if doc.id:
            row_id: int | str = doc.id
            out_ids.append(str(doc.id))
        elif doc.text:
            # 63-bit positive int from sha1 prefix — Milvus accepts
            # int64 ids; signed bit cleared to dodge negative-id
            # complaints on some collection schemas.
            digest = hashlib.sha1(doc.text.encode("utf-8")).digest()
            row_id = int.from_bytes(digest[:8], "big") & ((1 << 63) - 1)
            out_ids.append(str(row_id))
        else:
            # Let Milvus auto-generate when both id and text are empty
            # (degenerate case but handled cleanly).
            row_id = ""
            out_ids.append("")

        row: dict[str, Any] = {
            "vector": doc.embedding,
            config.text_field: doc.text,
        }
        if row_id != "":
            row["id"] = row_id
        if doc.metadata:
            for key, value in doc.metadata.items():
                row.setdefault(key, value)
        rows.append(row)

    base = config.endpoint.rstrip("/")
    url = f"{base}/v1/vector/insert"

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"

    body: dict[str, Any] = {
        "collectionName": config.collection,
        "data": rows,
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
        try:
            resp = await client.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise VectorDbProviderError(f"Milvus upsert request failed: {exc}") from exc
    if resp.status_code >= 400:
        raise VectorDbProviderError(f"Milvus upsert error {resp.status_code}: {resp.text}")

    payload = resp.json()
    # Milvus returns {"code": 0, "data": {"insertCount": N, "insertIds": [...]}}
    data = payload.get("data") or {}
    insert_count = int(data.get("insertCount") or len(rows))
    insert_ids = data.get("insertIds")
    if isinstance(insert_ids, list) and insert_ids:
        # Server-generated ids — return what Milvus actually wrote.
        out_ids = [str(x) for x in insert_ids]
    return UpsertResult(inserted_count=insert_count, ids=out_ids)


__all__ = ["query", "upsert"]
