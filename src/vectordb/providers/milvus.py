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


__all__ = ["query"]
