"""Weaviate provider for vector-db queries (Phase 6e).

Spec: POST {endpoint}/v1/graphql with GraphQL query including
_additional {id, distance, vector}.  Score = 1 - distance.

Class names are capitalized PascalCase per Weaviate conventions.

See Phase 6e spec §6.4.
"""

from __future__ import annotations

import json as _json
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


def _format_class_name(name: str) -> str:
    """Weaviate class names are capitalized PascalCase."""
    return name[:1].upper() + name[1:]


async def query(
    embedding: list[float],
    config: QueryConfig,
) -> list[VectorDbResult]:
    base = config.endpoint.rstrip("/")
    url = f"{base}/v1/graphql"
    class_name = _format_class_name(config.collection)
    text_field = config.text_field or "text"

    additional = "id distance"
    if config.include_vector:
        additional += " vector"

    where_clause = ""
    if config.metadata_filter:
        where_json = _json.dumps(config.metadata_filter)
        where_clause = f", where: {where_json}"

    graphql_query = (
        "{ Get { "
        f"{class_name}(nearVector: {{vector: {list(embedding)}}}, "
        f"limit: {config.top_k}{where_clause}) "
        f"{{ _additional {{ {additional} }} {text_field} content page_content }} "
        "} }"
    )

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
        try:
            resp = await client.post(url, headers=headers, json={"query": graphql_query})
        except httpx.HTTPError as exc:
            raise VectorDbProviderError(f"Weaviate query request failed: {exc}") from exc
    if resp.status_code >= 400:
        raise VectorDbProviderError(f"Weaviate query error {resp.status_code}: {resp.text}")

    payload = resp.json()
    if payload.get("errors"):
        raise VectorDbProviderError(
            f"Weaviate GraphQL error: {payload['errors'][0].get('message', 'unknown')}"
        )

    items: list[dict[str, Any]] = (payload.get("data") or {}).get("Get", {}).get(class_name, [])
    results: list[VectorDbResult] = []
    for item in items:
        additional_obj: dict[str, Any] = item.get("_additional", {}) or {}
        distance = float(additional_obj.get("distance") or 0.0)
        text = item.get(text_field) or item.get("content") or item.get("page_content") or ""
        metadata = {k: v for k, v in item.items() if k != "_additional"}
        results.append(
            VectorDbResult(
                id=str(additional_obj.get("id", "")),
                score=float(1.0 - distance),
                text=str(text),
                metadata=metadata if config.include_metadata else {},
                vector=(additional_obj.get("vector") if config.include_vector else None),
            )
        )
    return results


async def upsert(
    documents: list[UpsertDocument],
    config: UpsertConfig,
) -> UpsertResult:
    """Weaviate POST /v1/batch/objects.

    Body: `{objects: [{class, vector, properties: {<text_field>: ...}}, ...]}`.
    Class names are PascalCase per Weaviate convention; we normalise.
    Object IDs must be UUIDs — when the caller doesn't supply one we
    derive a stable UUIDv5 from the chunk text so re-upserts overwrite.
    """
    import hashlib
    import uuid

    if not documents:
        return UpsertResult(inserted_count=0, ids=[])

    _NS = uuid.UUID("00000000-0000-0000-0000-00636f6d706f73")  # stable namespace
    class_name = _format_class_name(config.collection)

    objects: list[dict[str, Any]] = []
    out_ids: list[str] = []
    for doc in documents:
        if doc.id:
            obj_id = doc.id
        elif doc.text:
            obj_id = str(uuid.uuid5(_NS, hashlib.sha1(doc.text.encode()).hexdigest()))
        else:
            obj_id = str(uuid.uuid4())
        out_ids.append(obj_id)

        properties: dict[str, Any] = dict(doc.metadata)
        properties[config.text_field] = doc.text
        objects.append(
            {
                "class": class_name,
                "id": obj_id,
                "vector": doc.embedding,
                "properties": properties,
            }
        )

    base = config.endpoint.rstrip("/")
    url = f"{base}/v1/batch/objects"

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
        try:
            resp = await client.post(url, headers=headers, json={"objects": objects})
        except httpx.HTTPError as exc:
            raise VectorDbProviderError(f"Weaviate upsert request failed: {exc}") from exc
    if resp.status_code >= 400:
        raise VectorDbProviderError(f"Weaviate upsert error {resp.status_code}: {resp.text}")

    # Weaviate's batch endpoint returns a per-object result with
    # individual success/failure status — count only successes.
    payload = resp.json()
    if isinstance(payload, list):
        # When all succeed Weaviate returns the list directly; if any
        # fail each item carries a `result.errors` key.
        successful = sum(
            1
            for item in payload
            if isinstance(item, dict) and (item.get("result") or {}).get("errors") in (None, {})
        )
    else:
        successful = len(out_ids)
    return UpsertResult(inserted_count=successful, ids=out_ids[:successful])


__all__ = ["query", "upsert"]
