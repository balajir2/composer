# Phase 6e — Vector-DB: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development`. Steps use checkbox (`- [ ]`).

**Goal:** Ship `vector-db` executor with 5 providers (Pinecone, Qdrant, Chroma, Weaviate, Milvus) + OpenAI embeddings. Last Phase 6 executor.

**Architecture.** `src/vectordb/` module — `embedding.py` (OpenAI) + `providers/` (5 files with `async def query()`). Executor dispatches via dict map. See ADR-0020.

**Tech Stack:** httpx, pytest-httpx, existing executor registry + `substitute()`.

**Spec:** [`docs/superpowers/specs/2026-04-21-phase-6e-vector-db-design.md`](../specs/2026-04-21-phase-6e-vector-db-design.md)
**ADR:** [ADR-0020](../../design/decisions.md#adr-0020-vectordb-provider-framework--one-file-per-provider)

---

## Sequencing + discipline

7 tasks, one commit each. Every task ends green on:

```bash
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m ruff format --check src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

**⚠️ Forbidden files:** `pyproject.toml`, `.github/workflows/*`, `CLAUDE.md` (except Task 7), `docs/design/*` (except Task 7 for ADR backfill), `docs/superpowers/plans/*`, `docs/superpowers/specs/*`, Prisma schema + migrations.

Vector-db is the last sentinel type. After Task 5, `vector-db` is registered and `tests/unit/executors/test_registry.py` / `test_graph_builder.py` / `test_langgraph_executor.py` sentinel tests need migration to an **out-of-phase type** — recommend using `"guardrails"` as the stale sentinel … wait, that's already shipped. Use **a made-up placeholder type** that's guaranteed unshipped: check `_PHASE_FOR_TYPE` in `src/executors/base.py`. If any type remains at "unshipped" status there, use it; otherwise Task 5's sentinel migration deletes the sentinel tests entirely (Phase 6 finishes the executor catalog — sentinel becomes vacuous).

Actually check `src/executors/base.py` `_PHASE_FOR_TYPE` at Task 5 time. If it still has an entry mapped to a phase ≥7 with no executor, use it. Else: delete the sentinel tests with a rationale commit message.

Commit footer every commit:
```
Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
```

Stay on `main`.

---

## Task 1: Pydantic tightening + embedding utility

**Files:**
- Modify: `src/engine/workflow.py` — tighten `VectorDbNodeData`
- Create: `src/vectordb/__init__.py` (empty)
- Create: `src/vectordb/embedding.py`
- Create: `tests/unit/vectordb/__init__.py` (empty)
- Create: `tests/unit/vectordb/test_embedding.py`
- Modify: `tests/unit/engine/test_workflow_models.py` (append Pydantic tests)

- [ ] **Step 1: Replace `VectorDbNodeData`**

Current placeholder (around line 281):
```python
class VectorDbNodeData(BaseNodeData):
    config: dict[str, Any] = Field(default_factory=dict)
```

Replace with ~18 fields — copy from Phase 6e spec §4 verbatim. Also add the literal aliases `VectorDbProvider` and `EmbeddingProvider` above the class:

```python
VectorDbProvider = Literal["pinecone", "qdrant", "chroma", "weaviate", "milvus"]
EmbeddingProvider = Literal["openai", "cohere", "jina", "pinecone-inference"]
```

If there are existing callers importing `VectorDbProvider` from elsewhere (unlikely), keep the name. Otherwise, these type aliases live in `src/engine/workflow.py`.

- [ ] **Step 2: Create `src/vectordb/embedding.py`**

Copy the code from spec §5 verbatim. Exports: `embed_text_openai`, `EmbeddingError`, `OPENAI_EMBEDDINGS_URL`.

- [ ] **Step 3: Create `tests/unit/vectordb/test_embedding.py`**

```python
"""Tests for OpenAI embeddings utility (vector-db, Phase 6e)."""

from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from src.vectordb.embedding import (
    OPENAI_EMBEDDINGS_URL,
    EmbeddingError,
    embed_text_openai,
)


async def test_embed_text_openai_happy_path(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST",
        url=OPENAI_EMBEDDINGS_URL,
        json={"data": [{"embedding": [0.1, 0.2, 0.3]}]},
        status_code=200,
    )
    vector = await embed_text_openai(
        "hello", model="text-embedding-3-small", api_key="key", dimension=3
    )
    assert vector == [0.1, 0.2, 0.3]


async def test_embed_text_openai_sends_dimensions_for_v3_models(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(
        method="POST", url=OPENAI_EMBEDDINGS_URL,
        json={"data": [{"embedding": [0.0]}]}, status_code=200,
    )
    await embed_text_openai("x", model="text-embedding-3-small", api_key="k", dimension=1536)

    import json

    req = httpx_mock.get_requests(method="POST", url=OPENAI_EMBEDDINGS_URL)[0]
    body = json.loads(req.content)
    assert body["dimensions"] == 1536
    assert body["model"] == "text-embedding-3-small"
    assert body["input"] == "x"


async def test_embed_text_openai_omits_dimensions_for_ada(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST", url=OPENAI_EMBEDDINGS_URL,
        json={"data": [{"embedding": [0.0]}]}, status_code=200,
    )
    await embed_text_openai("x", model="text-embedding-ada-002", api_key="k", dimension=1536)

    import json

    req = httpx_mock.get_requests(method="POST", url=OPENAI_EMBEDDINGS_URL)[0]
    body = json.loads(req.content)
    assert "dimensions" not in body


async def test_embed_text_openai_401_raises(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST", url=OPENAI_EMBEDDINGS_URL,
        status_code=401, text="bad key",
    )
    with pytest.raises(EmbeddingError, match="401"):
        await embed_text_openai("x", model="text-embedding-3-small", api_key="k")


async def test_embed_text_openai_malformed_response_raises(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST", url=OPENAI_EMBEDDINGS_URL,
        json={"nope": "nothing"}, status_code=200,
    )
    with pytest.raises(EmbeddingError, match="missing 'data'"):
        await embed_text_openai("x", model="text-embedding-3-small", api_key="k")


async def test_embed_text_openai_missing_key_raises() -> None:
    with pytest.raises(EmbeddingError, match="API key is required"):
        await embed_text_openai("x", model="text-embedding-3-small", api_key="")
```

- [ ] **Step 4: Append Pydantic tests to `tests/unit/engine/test_workflow_models.py`**

```python
def test_vector_db_node_data_parses_camelcase_aliases() -> None:
    from src.engine.workflow import VectorDbNodeData

    data = VectorDbNodeData.model_validate({
        "label": "VDB",
        "vectorDbProvider": "qdrant",
        "vectorDbEndpoint": "http://localhost:6333",
        "vectorDbApiKey": "{{env.QDRANT_API_KEY}}",
        "vectorDbCollection": "docs",
        "vectorDbDimension": 768,
        "vectorDbEmbeddingProvider": "openai",
        "vectorDbEmbeddingModel": "text-embedding-3-large",
        "vectorDbQueryPrompt": "find {{topic}}",
        "vectorDbTopK": 10,
        "vectorDbScoreThreshold": 0.7,
        "vectorDbNamespace": "ns1",
        "vectorDbIncludeMetadata": False,
        "vectorDbIncludeVector": True,
        "vectorDbTextField": "page_content",
        "vectorDbOutputVariable": "hits",
        "vectorDbMetadataFilter": "{\"category\":\"docs\"}",
        "vectorDbJoinResults": True,
        "vectorDbJoinSeparator": "\n---\n",
        "vectorDbJoinPrefix": "> ",
        "vectorDbJoinSuffix": " <",
    })
    assert data.provider == "qdrant"
    assert data.endpoint == "http://localhost:6333"
    assert data.api_key == "{{env.QDRANT_API_KEY}}"
    assert data.collection == "docs"
    assert data.dimension == 768
    assert data.embedding_provider == "openai"
    assert data.embedding_model == "text-embedding-3-large"
    assert data.query_prompt == "find {{topic}}"
    assert data.top_k == 10
    assert data.score_threshold == 0.7
    assert data.namespace == "ns1"
    assert data.include_metadata is False
    assert data.include_vector is True
    assert data.text_field == "page_content"
    assert data.output_variable == "hits"
    assert data.metadata_filter == '{"category":"docs"}'
    assert data.join_results is True
    assert data.join_separator == "\n---\n"
    assert data.join_prefix == "> "
    assert data.join_suffix == " <"


def test_vector_db_node_data_defaults() -> None:
    from src.engine.workflow import VectorDbNodeData

    data = VectorDbNodeData.model_validate({"label": "VDB"})
    assert data.provider == "pinecone"
    assert data.endpoint == ""
    assert data.api_key == ""
    assert data.collection == ""
    assert data.dimension == 1536
    assert data.embedding_provider == "openai"
    assert data.embedding_model == "text-embedding-3-small"
    assert data.query_prompt == ""
    assert data.top_k == 5
    assert data.score_threshold == 0.0
    assert data.namespace is None
    assert data.include_metadata is True
    assert data.include_vector is False
    assert data.text_field is None
    assert data.output_variable == "vectorDbResults"
    assert data.metadata_filter is None
    assert data.join_results is False


def test_vector_db_node_data_invalid_provider_raises() -> None:
    import pytest
    from pydantic import ValidationError

    from src.engine.workflow import VectorDbNodeData

    with pytest.raises(ValidationError):
        VectorDbNodeData.model_validate({"label": "VDB", "vectorDbProvider": "bogus"})


def test_vector_db_node_full_round_trip() -> None:
    from src.engine.workflow import VectorDbNode

    node = VectorDbNode.model_validate({
        "id": "vdb1",
        "type": "vector-db",
        "position": {"x": 0, "y": 0},
        "data": {"label": "VDB"},
    })
    assert node.id == "vdb1"
    assert node.type == "vector-db"
    assert node.data.provider == "pinecone"
```

- [ ] **Step 5: Run + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/vectordb/test_embedding.py tests/unit/engine/test_workflow_models.py -v -k "vector_db or embed"
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

Expected: ~10 new tests (6 embedding + 4 pydantic). Pyright 0 errors.

```bash
git add src/engine/workflow.py src/vectordb/ tests/unit/vectordb/ tests/unit/engine/test_workflow_models.py
git commit -m "feat(vectordb): Pydantic tightening + OpenAI embeddings utility (Phase 6e)

VectorDbNodeData tightened with 18+ explicit fields matching OAB
types.ts + executor reads (provider, endpoint, api_key, collection,
dimension, embedding config, query prompt, top_k, score_threshold,
namespace, filter, include flags, output variable, join-formatting).

Type aliases: VectorDbProvider (5 providers), EmbeddingProvider
(4 options; cohere/jina/pinecone-inference raise until a later phase).

New module src/vectordb/embedding.py — embed_text_openai() via the
OpenAI embeddings API.  text-embedding-3-* models accept dimensions
param; older models omit it.

See Phase 6e spec §4, §5.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Provider framework base + Pinecone

**Files:**
- Create: `src/vectordb/providers/__init__.py` (re-exports provider modules at task completion)
- Create: `src/vectordb/providers/base.py`
- Create: `src/vectordb/providers/pinecone.py`
- Create: `tests/unit/vectordb/providers/__init__.py` (empty)
- Create: `tests/unit/vectordb/providers/test_pinecone.py`

- [ ] **Step 1: `src/vectordb/providers/base.py`**

Copy from spec §6. Exports: `VectorDbResult`, `QueryConfig`, `VectorDbProviderError`.

- [ ] **Step 2: `src/vectordb/providers/pinecone.py`**

```python
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
            raise VectorDbProviderError(
                f"Pinecone query request failed: {exc}"
            ) from exc
    if resp.status_code >= 400:
        raise VectorDbProviderError(
            f"Pinecone query error {resp.status_code}: {resp.text}"
        )

    payload = resp.json()
    matches = payload.get("matches") or []
    text_key = config.text_field or "text"
    results: list[VectorDbResult] = []
    for match in matches:
        metadata_raw = match.get("metadata") or {}
        metadata = (
            dict(metadata_raw) if isinstance(metadata_raw, dict) else {}
        )
        text = metadata.get(text_key) or metadata.get("content") or ""
        results.append(
            VectorDbResult(
                id=str(match.get("id", "")),
                score=float(match.get("score") or 0.0),
                text=str(text),
                metadata=metadata if config.include_metadata else {},
                vector=(
                    match.get("values") if config.include_vector else None
                ),
            )
        )
    return results


__all__ = ["query"]
```

- [ ] **Step 3: `tests/unit/vectordb/providers/test_pinecone.py`**

```python
"""Tests for Pinecone vector-db provider (Phase 6e)."""

from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from src.vectordb.providers import pinecone
from src.vectordb.providers.base import QueryConfig, VectorDbProviderError


_BASE = "https://myindex.pinecone.io"


def _config(**overrides: Any) -> QueryConfig:
    base: dict[str, Any] = {
        "endpoint": _BASE,
        "api_key": "test-key",
        "collection": "does-not-matter-for-pinecone",
        "top_k": 5,
    }
    base.update(overrides)
    return QueryConfig(**base)


async def test_pinecone_happy_path(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST", url=f"{_BASE}/query",
        json={
            "matches": [
                {
                    "id": "doc1",
                    "score": 0.95,
                    "metadata": {"text": "hello", "source": "a.txt"},
                },
                {
                    "id": "doc2",
                    "score": 0.80,
                    "metadata": {"content": "world"},
                },
            ]
        },
        status_code=200,
    )
    results = await pinecone.query([0.1, 0.2], _config())
    assert len(results) == 2
    assert results[0].id == "doc1"
    assert results[0].score == 0.95
    assert results[0].text == "hello"
    assert results[0].metadata == {"text": "hello", "source": "a.txt"}
    # Fallback: uses 'content' when 'text' is absent
    assert results[1].text == "world"


async def test_pinecone_sends_namespace_and_filter(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST", url=f"{_BASE}/query",
        json={"matches": []}, status_code=200,
    )
    await pinecone.query(
        [0.0],
        _config(namespace="ns1", metadata_filter={"category": "docs"}),
    )

    import json

    req = httpx_mock.get_requests(method="POST", url=f"{_BASE}/query")[0]
    body = json.loads(req.content)
    assert body["namespace"] == "ns1"
    assert body["filter"] == {"category": "docs"}
    assert body["topK"] == 5
    assert body["includeMetadata"] is True
    assert body["includeValues"] is False


async def test_pinecone_include_vector(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST", url=f"{_BASE}/query",
        json={
            "matches": [
                {"id": "d", "score": 1.0, "metadata": {"text": "x"}, "values": [0.1, 0.2]}
            ]
        },
        status_code=200,
    )
    results = await pinecone.query(
        [0.0], _config(include_vector=True)
    )
    assert results[0].vector == [0.1, 0.2]


async def test_pinecone_http_error_raises(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST", url=f"{_BASE}/query",
        status_code=500, text="pinecone down",
    )
    with pytest.raises(VectorDbProviderError, match="500"):
        await pinecone.query([0.0], _config())
```

- [ ] **Step 4: Run + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/vectordb/providers/test_pinecone.py -v
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

```bash
git add src/vectordb/providers/ tests/unit/vectordb/providers/
git commit -m "feat(vectordb): provider framework base + Pinecone (Phase 6e)

src/vectordb/providers/base.py — VectorDbResult + QueryConfig dataclasses
(frozen) + VectorDbProviderError.  Providers expose top-level async
def query(embedding, config) -> list[VectorDbResult].

Pinecone provider: POST {endpoint}/query with topK/includeMetadata/
includeValues/namespace/filter.  Api-Key header.  Response parses
matches[i].metadata[text_field || 'text' || 'content'] for text.

4 unit tests via pytest-httpx.

See Phase 6e spec §6.1, ADR-0020.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Qdrant provider

**Files:**
- Create: `src/vectordb/providers/qdrant.py`
- Create: `tests/unit/vectordb/providers/test_qdrant.py`

- [ ] **Step 1: `src/vectordb/providers/qdrant.py`**

```python
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
            raise VectorDbProviderError(
                f"Qdrant query request failed: {exc}"
            ) from exc
    if resp.status_code >= 400:
        raise VectorDbProviderError(
            f"Qdrant query error {resp.status_code}: {resp.text}"
        )

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
```

- [ ] **Step 2: `tests/unit/vectordb/providers/test_qdrant.py`**

```python
"""Tests for Qdrant vector-db provider (Phase 6e)."""

from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from src.vectordb.providers import qdrant
from src.vectordb.providers.base import QueryConfig, VectorDbProviderError

_BASE = "http://localhost:6333"


def _config(**overrides: Any) -> QueryConfig:
    base: dict[str, Any] = {
        "endpoint": _BASE,
        "api_key": None,
        "collection": "docs",
        "top_k": 3,
    }
    base.update(overrides)
    return QueryConfig(**base)


async def test_qdrant_happy_path(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST", url=f"{_BASE}/collections/docs/points/search",
        json={
            "result": [
                {"id": "1", "score": 0.9, "payload": {"text": "hello"}},
                {"id": "2", "score": 0.7, "payload": {"content": "world"}},
            ]
        },
        status_code=200,
    )
    results = await qdrant.query([0.1], _config())
    assert len(results) == 2
    assert results[0].text == "hello"
    assert results[1].text == "world"


async def test_qdrant_filter_passed(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST", url=f"{_BASE}/collections/docs/points/search",
        json={"result": []}, status_code=200,
    )
    await qdrant.query(
        [0.0],
        _config(metadata_filter={"must": [{"key": "type", "match": {"value": "doc"}}]}),
    )
    import json

    req = httpx_mock.get_requests(
        method="POST", url=f"{_BASE}/collections/docs/points/search"
    )[0]
    body = json.loads(req.content)
    assert "filter" in body


async def test_qdrant_http_error_raises(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST", url=f"{_BASE}/collections/docs/points/search",
        status_code=404, text="collection not found",
    )
    with pytest.raises(VectorDbProviderError, match="404"):
        await qdrant.query([0.0], _config())
```

- [ ] **Step 3: Run + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/vectordb/providers/test_qdrant.py -v
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

```bash
git add src/vectordb/providers/qdrant.py tests/unit/vectordb/providers/test_qdrant.py
git commit -m "feat(vectordb): Qdrant provider (Phase 6e)

POST {endpoint}/collections/{collection}/points/search with
{vector, limit, with_payload, with_vector, filter?}.  api-key header
optional.  Response parses result[i].payload[text_field || 'text' ||
'content' || 'page_content'] for text.

3 unit tests.

See Phase 6e spec §6.2.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Chroma + Weaviate providers

**Files:**
- Create: `src/vectordb/providers/chroma.py`
- Create: `src/vectordb/providers/weaviate.py`
- Create: `tests/unit/vectordb/providers/test_chroma.py`
- Create: `tests/unit/vectordb/providers/test_weaviate.py`

### Step 1: `src/vectordb/providers/chroma.py`

Chroma's REST: POST `{endpoint}/api/v1/collections/{collection}/query` with `{query_embeddings: [[...]], n_results, where?}`.

Response shape: `{ids: [[...]], distances: [[...]], metadatas: [[...]], documents: [[...]], embeddings?: [[...]]}` — BATCH-indexed (outer array is per-query, inner per-result).

```python
"""Chroma provider for vector-db queries (Phase 6e).

Spec: POST {endpoint}/api/v1/collections/{collection}/query with
{query_embeddings, n_results, where?}.  Response is batch-indexed.
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
            raise VectorDbProviderError(
                f"Chroma query request failed: {exc}"
            ) from exc
    if resp.status_code >= 400:
        raise VectorDbProviderError(
            f"Chroma query error {resp.status_code}: {resp.text}"
        )

    payload = resp.json()
    ids_batch = payload.get("ids") or []
    if not ids_batch or not ids_batch[0]:
        return []
    ids = ids_batch[0]
    distances = (payload.get("distances") or [[]])[0]
    metadatas = (payload.get("metadatas") or [[]])[0]
    documents = (payload.get("documents") or [[]])[0]
    embeddings = (payload.get("embeddings") or [[]])[0] if config.include_vector else []

    results: list[VectorDbResult] = []
    for i, doc_id in enumerate(ids):
        distance = distances[i] if i < len(distances) else 0.0
        metadata = metadatas[i] if i < len(metadatas) else {}
        text = documents[i] if i < len(documents) else ""
        vector = embeddings[i] if i < len(embeddings) else None
        if config.text_field and isinstance(metadata, dict) and config.text_field in metadata:
            text = metadata[config.text_field]
        results.append(
            VectorDbResult(
                id=str(doc_id),
                score=float(1.0 - (distance or 0.0)),
                text=str(text or ""),
                metadata=dict(metadata) if (config.include_metadata and isinstance(metadata, dict)) else {},
                vector=list(vector) if vector is not None else None,
            )
        )
    return results


__all__ = ["query"]
```

### Step 2: `src/vectordb/providers/weaviate.py`

Weaviate uses GraphQL:

```python
"""Weaviate provider for vector-db queries (Phase 6e).

Spec: POST {endpoint}/v1/graphql with
{ Get { <ClassName>(nearVector: {vector}, limit, where?) {
    _additional { id, distance, vector }, <fields>
  }}}.

Score = 1 - distance.

See Phase 6e spec §6.4.
"""

from __future__ import annotations

from typing import Any

import httpx

from src.vectordb.providers.base import (
    QueryConfig,
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

    # Fields to request. Always include text_field; allow for alternates.
    additional = "id distance"
    if config.include_vector:
        additional += " vector"

    where_clause = ""
    if config.metadata_filter:
        # Users pass Weaviate-shaped where filter as dict → serialize as GraphQL value
        import json as _json

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

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(
                url, headers=headers, json={"query": graphql_query}
            )
        except httpx.HTTPError as exc:
            raise VectorDbProviderError(
                f"Weaviate query request failed: {exc}"
            ) from exc
    if resp.status_code >= 400:
        raise VectorDbProviderError(
            f"Weaviate query error {resp.status_code}: {resp.text}"
        )

    payload = resp.json()
    if payload.get("errors"):
        raise VectorDbProviderError(
            f"Weaviate GraphQL error: {payload['errors'][0].get('message', 'unknown')}"
        )

    items = (
        (payload.get("data") or {})
        .get("Get", {})
        .get(class_name, [])
    )
    results: list[VectorDbResult] = []
    for item in items:
        additional_obj = item.get("_additional", {}) or {}
        distance = float(additional_obj.get("distance") or 0.0)
        text = (
            item.get(text_field)
            or item.get("content")
            or item.get("page_content")
            or ""
        )
        # Metadata is everything except _additional
        metadata = {k: v for k, v in item.items() if k != "_additional"}
        results.append(
            VectorDbResult(
                id=str(additional_obj.get("id", "")),
                score=float(1.0 - distance),
                text=str(text),
                metadata=metadata if config.include_metadata else {},
                vector=(
                    additional_obj.get("vector") if config.include_vector else None
                ),
            )
        )
    return results


__all__ = ["query"]
```

### Step 3: `tests/unit/vectordb/providers/test_chroma.py` — 3 tests

```python
"""Tests for Chroma vector-db provider (Phase 6e)."""

from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from src.vectordb.providers import chroma
from src.vectordb.providers.base import QueryConfig, VectorDbProviderError

_BASE = "http://localhost:8000"


def _config(**overrides: Any) -> QueryConfig:
    base: dict[str, Any] = {
        "endpoint": _BASE,
        "api_key": None,
        "collection": "my-collection",
        "top_k": 3,
    }
    base.update(overrides)
    return QueryConfig(**base)


async def test_chroma_happy_path(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST", url=f"{_BASE}/api/v1/collections/my-collection/query",
        json={
            "ids": [["a", "b"]],
            "distances": [[0.1, 0.3]],
            "metadatas": [[{"source": "x"}, {"source": "y"}]],
            "documents": [["hello", "world"]],
        },
        status_code=200,
    )
    results = await chroma.query([0.1, 0.2], _config())
    assert len(results) == 2
    # Score = 1 - distance
    assert results[0].score == pytest.approx(0.9)
    assert results[1].score == pytest.approx(0.7)
    assert results[0].text == "hello"
    assert results[0].metadata == {"source": "x"}


async def test_chroma_empty_result(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST", url=f"{_BASE}/api/v1/collections/my-collection/query",
        json={"ids": [[]]}, status_code=200,
    )
    results = await chroma.query([0.0], _config())
    assert results == []


async def test_chroma_http_error_raises(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST", url=f"{_BASE}/api/v1/collections/my-collection/query",
        status_code=400, text="bad request",
    )
    with pytest.raises(VectorDbProviderError, match="400"):
        await chroma.query([0.0], _config())
```

### Step 4: `tests/unit/vectordb/providers/test_weaviate.py` — 3 tests

```python
"""Tests for Weaviate vector-db provider (Phase 6e)."""

from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from src.vectordb.providers import weaviate
from src.vectordb.providers.base import QueryConfig, VectorDbProviderError

_BASE = "http://localhost:8080"


def _config(**overrides: Any) -> QueryConfig:
    base: dict[str, Any] = {
        "endpoint": _BASE,
        "api_key": None,
        "collection": "docs",
        "top_k": 3,
    }
    base.update(overrides)
    return QueryConfig(**base)


async def test_weaviate_happy_path(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST", url=f"{_BASE}/v1/graphql",
        json={
            "data": {
                "Get": {
                    "Docs": [
                        {
                            "_additional": {"id": "abc", "distance": 0.1},
                            "text": "hello",
                            "content": None,
                            "page_content": None,
                        }
                    ]
                }
            }
        },
        status_code=200,
    )
    results = await weaviate.query([0.1], _config())
    assert len(results) == 1
    assert results[0].id == "abc"
    assert results[0].text == "hello"
    assert results[0].score == pytest.approx(0.9)


async def test_weaviate_graphql_error_raises(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST", url=f"{_BASE}/v1/graphql",
        json={"errors": [{"message": "class not found"}]},
        status_code=200,
    )
    with pytest.raises(VectorDbProviderError, match="class not found"):
        await weaviate.query([0.0], _config())


async def test_weaviate_http_error_raises(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST", url=f"{_BASE}/v1/graphql",
        status_code=401, text="auth required",
    )
    with pytest.raises(VectorDbProviderError, match="401"):
        await weaviate.query([0.0], _config())
```

### Step 5: Run + commit

```bash
.venv/Scripts/python -m pytest tests/unit/vectordb/providers/test_chroma.py tests/unit/vectordb/providers/test_weaviate.py -v
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

```bash
git add src/vectordb/providers/chroma.py src/vectordb/providers/weaviate.py tests/unit/vectordb/providers/test_chroma.py tests/unit/vectordb/providers/test_weaviate.py
git commit -m "feat(vectordb): Chroma + Weaviate providers (Phase 6e)

Chroma: POST {endpoint}/api/v1/collections/{collection}/query with
{query_embeddings: [[...]], n_results, where?}.  Batch-indexed response.
Score = 1 - distance.

Weaviate: POST {endpoint}/v1/graphql with GraphQL query including
_additional {id, distance, vector}.  Score = 1 - distance.  GraphQL
errors (data.errors) raise VectorDbProviderError.

6 unit tests (3 per provider).

See Phase 6e spec §6.3, §6.4.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Milvus provider + VectorDbExecutor orchestrator + sentinel migration

**Files:**
- Create: `src/vectordb/providers/milvus.py`
- Create: `src/executors/vector_db.py`
- Modify: `src/engine/graph_builder.py` — side-effect import
- Create: `tests/unit/vectordb/providers/test_milvus.py`
- Create: `tests/unit/executors/test_vector_db.py`
- **Sentinel migration:** check `tests/unit/executors/test_registry.py`, `tests/unit/engine/test_graph_builder.py`, `tests/unit/engine/test_langgraph_executor.py`. They currently use `"vector-db"` as the unshipped sentinel; after this task, `vector-db` ships. Either (a) migrate to a different unshipped type from `_PHASE_FOR_TYPE` in `src/executors/base.py` that's still mapped to a later phase, or (b) delete the sentinel tests entirely (they're vacuous after Phase 6 closes).
  - Task 5 implementer: `grep -rn '"vector-db"' tests/unit/` first. Check `src/executors/base.py` for any remaining `_PHASE_FOR_TYPE` entries for unshipped types. If none, delete the 3 sentinel tests and commit with a note in the same commit.

### Step 1: `src/vectordb/providers/milvus.py`

```python
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

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise VectorDbProviderError(
                f"Milvus query request failed: {exc}"
            ) from exc
    if resp.status_code >= 400:
        raise VectorDbProviderError(
            f"Milvus query error {resp.status_code}: {resp.text}"
        )

    payload = resp.json()
    items = payload.get("data") or []
    text_key = config.text_field or "text"
    results: list[VectorDbResult] = []
    for item in items:
        # item fields besides id/distance become metadata
        item_dict = dict(item) if isinstance(item, dict) else {}
        text = item_dict.get(text_key) or item_dict.get("content") or ""
        results.append(
            VectorDbResult(
                id=str(item_dict.get("id", "")),
                score=float(item_dict.get("distance") or 0.0),
                text=str(text),
                metadata=(
                    item_dict if config.include_metadata else {}
                ),
                vector=(
                    item_dict.get("vector") if config.include_vector else None
                ),
            )
        )
    return results


__all__ = ["query"]
```

### Step 2: `tests/unit/vectordb/providers/test_milvus.py` — 3 tests

```python
"""Tests for Milvus vector-db provider (Phase 6e)."""

import logging
from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from src.vectordb.providers import milvus
from src.vectordb.providers.base import QueryConfig, VectorDbProviderError

_BASE = "https://my-milvus.zillizcloud.com"


def _config(**overrides: Any) -> QueryConfig:
    base: dict[str, Any] = {
        "endpoint": _BASE,
        "api_key": "test-key",
        "collection": "coll",
        "top_k": 3,
    }
    base.update(overrides)
    return QueryConfig(**base)


async def test_milvus_happy_path(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST", url=f"{_BASE}/v1/vector/search",
        json={
            "data": [
                {"id": "1", "distance": 0.9, "text": "hello", "category": "doc"},
            ]
        },
        status_code=200,
    )
    results = await milvus.query([0.1], _config())
    assert len(results) == 1
    assert results[0].id == "1"
    assert results[0].score == pytest.approx(0.9)
    assert results[0].text == "hello"
    assert results[0].metadata == {"id": "1", "distance": 0.9, "text": "hello", "category": "doc"}


async def test_milvus_filter_dict_logs_warning_and_skips(
    httpx_mock: HTTPXMock, caplog: pytest.LogCaptureFixture,
) -> None:
    httpx_mock.add_response(
        method="POST", url=f"{_BASE}/v1/vector/search",
        json={"data": []}, status_code=200,
    )
    with caplog.at_level(logging.WARNING):
        await milvus.query([0.0], _config(metadata_filter={"category": "doc"}))
    assert any("DSL expression" in rec.message for rec in caplog.records)

    import json

    req = httpx_mock.get_requests(method="POST", url=f"{_BASE}/v1/vector/search")[0]
    body = json.loads(req.content)
    assert body["filter"] == ""


async def test_milvus_http_error_raises(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST", url=f"{_BASE}/v1/vector/search",
        status_code=500, text="boom",
    )
    with pytest.raises(VectorDbProviderError, match="500"):
        await milvus.query([0.0], _config())
```

### Step 3: `src/executors/vector_db.py`

Copy the executor implementation from spec §7 verbatim.

### Step 4: `tests/unit/executors/test_vector_db.py` — 12 tests

Follow the spec §9.1 executor tests. Cover:
1. Happy path Pinecone → output shape, lastOutput = output dict.
2. Substitution in endpoint / collection / prompt / namespace.
3. Score threshold filter.
4. Join results: separator + prefix + suffix + `{{index}}` placeholder substitution.
5. Custom output variable.
6. `lastOutput` = joined text when `joinResults=True`.
7. Empty endpoint → `VectorDbNodeError`.
8. Empty prompt → `VectorDbNodeError`.
9. Non-openai embedding_provider → `NotImplementedError`.
10. Metadata filter valid JSON → passed to provider.
11. Metadata filter malformed JSON → log warn + skip (asserts final provider call body).
12. Executor registered in `_REGISTRY`.
13. Dispatch covers all 5 providers (parametric).

Write tests with `monkeypatch` stubbing out `embed_text_openai` to return a fixed vector, and `pytest-httpx` or `monkeypatch` stubbing out each provider's `query()` function. This keeps the executor test independent of provider test suites.

Sample pattern for stubbing `embed_text_openai`:
```python
async def _fake_embed(*args: Any, **kwargs: Any) -> list[float]:
    return [0.1, 0.2, 0.3]

monkeypatch.setattr("src.executors.vector_db.embed_text_openai", _fake_embed)
```

Sample pattern for stubbing a provider:
```python
async def _fake_pinecone_query(embedding: list[float], config: Any) -> list[VectorDbResult]:
    return [VectorDbResult(id="1", score=0.9, text="hello")]

monkeypatch.setattr("src.executors.vector_db._PROVIDERS", {"pinecone": _fake_pinecone_query})
```

### Step 5: Register side-effect import in `src/engine/graph_builder.py`

Add alphabetically (near the end):
```python
from src.executors import (
    vector_db as _vector_db_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
```

### Step 6: Sentinel migration check

```bash
grep -rn '"vector-db"' tests/unit/executors/test_registry.py tests/unit/engine/test_graph_builder.py tests/unit/engine/test_langgraph_executor.py
cat src/executors/base.py | grep -A 20 "_PHASE_FOR_TYPE"
```

If `_PHASE_FOR_TYPE` has any remaining unshipped types (e.g., mapped to Phase 7+), use that type. Otherwise, delete the 3 sentinel tests (they're vacuous after Phase 6 closes the executor catalog). Include in the same commit with a rationale in the commit message.

### Step 7: Run + commit

```bash
.venv/Scripts/python -m pytest tests/unit/vectordb/providers/test_milvus.py tests/unit/executors/test_vector_db.py -v
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

```bash
git add src/vectordb/providers/milvus.py src/executors/vector_db.py src/engine/graph_builder.py tests/unit/vectordb/providers/test_milvus.py tests/unit/executors/test_vector_db.py
# Sentinel migration files if modified/deleted.
git commit -m "feat(executors): vector-db — Milvus provider + orchestrator (Phase 6e)

Milvus provider: POST {endpoint}/v1/vector/search with {collectionName,
vector, limit, filter, outputFields: ['*']}.  Bearer auth.  Milvus
expects DSL string filters, not JSON dicts — log warning + skip when
metadata_filter dict is non-empty (matches OAB).

VectorDbExecutor orchestrator:
  - substitute() in endpoint / collection / prompt / namespace / filter
  - embed via embed_text_openai (Phase 6e ships OpenAI only)
  - dispatch via _PROVIDERS dict on provider name
  - score-threshold filter post-query
  - optional join-results formatting with {{index}} substitution
  - dual output: variables[output_variable] + variables.lastOutput

Sentinel tests migrated/removed now that vector-db ships (Phase 6
executor catalog is complete).

See Phase 6e spec §6.5, §7, ADR-0020.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Sanity check

No code changes. Full suite green.

```bash
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m ruff format --check src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

No commit.

---

## Task 7: Phase-exit — CHANGELOG + CLAUDE.md + ADR-0020 backfill + push

**Authorized:** `CHANGELOG.md`, `CLAUDE.md`, `docs/design/decisions.md`.

- [ ] **Step 1: Final exit checklist**

```bash
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m ruff format --check src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

- [ ] **Step 2: Update `CHANGELOG.md`** — insert above Phase 6d:

```markdown
### Phase 6e — Vector-DB (2026-04-21)

#### Added
- [Phase 6e design spec](docs/superpowers/specs/2026-04-21-phase-6e-vector-db-design.md) + ADR-0020.
- `src/vectordb/` module — new top-level package.
  - `src/vectordb/embedding.py` — `embed_text_openai()` via the OpenAI embeddings API. `text-embedding-3-*` models accept `dimensions` param.
  - `src/vectordb/providers/` — one file per provider, each exporting `async def query(embedding, config) -> list[VectorDbResult]`. Shared `QueryConfig` + `VectorDbResult` dataclasses in `base.py`.
  - **5 providers:** Pinecone (REST `/query`), Qdrant (REST `/collections/{name}/points/search`), Chroma (REST batch-indexed), Weaviate (GraphQL with `_additional {id,distance,vector}`), Milvus (REST `/v1/vector/search`; DSL filter only — dict filters log warn + skip).
- `src/executors/vector_db.py` — orchestrator. Substitutes config fields, embeds, dispatches via `_PROVIDERS` dict map, applies score-threshold filter, optionally joins results with separator/prefix/suffix + `{{index}}` placeholder. Dual output: `variables[output_variable]` + `variables.lastOutput` (joined text if `joinResults=True`, otherwise the full output dict).
- `src/engine/workflow.py` — `VectorDbNodeData` tightened with 18+ explicit fields matching OAB `types.ts` + executor reads.
- Unit tests: ~35+ (6 embedding + 4 Pydantic + 3-4 per provider × 5 + 12 executor).
- `VectorDbProvider` and `EmbeddingProvider` Literal aliases exported from `src/engine/workflow.py`.

#### Changed
- Non-OpenAI embedding providers (cohere/jina/pinecone-inference) raise `NotImplementedError` until a later phase.
- Sentinel tests removed — Phase 6 closes the executor catalog; `_PHASE_FOR_TYPE` sentinels for unshipped types become vacuous (all node types now have registered executors).

#### Notes
- **Phase 6 is now complete.** All 18 node types in OAB's catalog have Composer executors:
  - Phase 1: start, end
  - Phase 2: agent
  - Phase 3a/3b: mcp
  - Phase 4a/4b: http, transform, data-transform, extract, set-state, if-else, while
  - Phase 5a: user-approval
  - Phase 6a-e: note, join-chunks, guardrails, gamma-ai, arcade, vector-db
- No automated integration tests for vector-db: each provider needs real credentials + a populated index. Unit tests via `pytest-httpx` pin the wire shapes.
- Per-node API keys (`vectorDbApiKey`) use `{{...}}` substitution — users parameterize via state/env.

#### Verified
- ~500+/500+ unit tests green (+30+ from Phase 6d 468).
- Pyright 0 errors, ruff + format clean.

### Phase 6d — Arcade (2026-04-21)
```

- [ ] **Step 3: Update `CLAUDE.md` phase table**

Change:
```markdown
| 6d — Arcade | ✅ Complete | HTTP integration with auth-interrupt flow; reuses Phase 5a `/resume`; retry counter MAX_RETRIES=3; 14 unit tests, smoke test manual |
| 6e — Vector-DB | ⏭ Next | provider framework (embed/upsert/query) |
```
To:
```markdown
| 6d — Arcade | ✅ Complete | HTTP integration with auth-interrupt flow; reuses Phase 5a `/resume`; retry counter MAX_RETRIES=3; 14 unit tests, smoke test manual |
| 6e — Vector-DB | ✅ Complete | 5 providers (Pinecone/Qdrant/Chroma/Weaviate/Milvus) + OpenAI embeddings; provider framework per ADR-0020; ~35 unit tests, smoke test manual |
| 7 — API parity + regression suite ported | ⏭ Next | port OAB's ~72 pytest tests (objective parity check) |
```

- [ ] **Step 4: Backfill ADR-0020 `Implemented by`**

```bash
git log --oneline 96ff8b5..HEAD
```

Replace `**Implemented by.** Phase 6e (commits TBD).` with the range.

- [ ] **Step 5: Commit + push**

```bash
git add CHANGELOG.md CLAUDE.md docs/design/decisions.md
git commit -m "docs(phase-6e): mark Phase 6e + Phase 6 complete

Vector-db shipped.  5 providers (Pinecone, Qdrant, Chroma, Weaviate,
Milvus) + OpenAI embeddings.  src/vectordb/ module; one file per
provider; uniform async def query() signature; executor dispatches
via dict map.  ADR-0020 documents the provider-framework pattern.

Phase 6 (all 6 sub-phases: 6a note+join-chunks, 6b guardrails, 6c
gamma-ai, 6d arcade, 6e vector-db) is now complete.  All 18 node
types in OAB's catalog have Composer executors.

ADR-0020 Implemented by backfilled.

Phase 7 (API parity + regression suite port) is next.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"

git push origin main
```

---

## Spec coverage self-review

| Spec section | Tasks |
|---|---|
| §4 Pydantic tightening | Task 1 |
| §5 embedding | Task 1 |
| §6 provider framework base | Task 2 |
| §6.1 Pinecone | Task 2 |
| §6.2 Qdrant | Task 3 |
| §6.3 Chroma | Task 4 |
| §6.4 Weaviate | Task 4 |
| §6.5 Milvus | Task 5 |
| §7 executor orchestrator | Task 5 |
| §8 error model | Tasks 1-5 (per-component tests) |
| §9 test plan | Tasks 1-5 |
| §10 phase-exit | Task 7 |
| §11 ADR-0020 | Pre-plan + Task 7 backfill |

No placeholders. Type consistency: `VectorDbResult` / `QueryConfig` shared across all 5 providers and the executor. Field names match across Pydantic (§4) and executor reads (§7).

---

## Execution handoff

Plan saved. Controller proceeds to `superpowers:subagent-driven-development` for Tasks 1–7.
