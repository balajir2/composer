# Phase 6e — Vector-DB: Design

**Status.** Approved 2026-04-21.
**Related.** Phase 2 LLM provider framework (ADR-0006 — analogous pattern), Phase 1 Pydantic workflow models (ADR-0002), ADR-0020 (this phase — VectorDB provider framework).

---

## 1. Goal

Ship the `vector-db` executor — OAB-compatible vector-search node supporting **5 providers** (Pinecone, Qdrant, Chroma, Weaviate, Milvus) with OpenAI embeddings. This is the last Phase 6 sub-phase; completes the executor catalog for regression-suite port in Phase 7.

## 2. Non-goals

- **No embedding providers beyond OpenAI.** OAB declares cohere / jina / pinecone-inference as stubs that raise. Composer raises `NotImplementedError("embedding_provider X lands in a later phase")` matching the Phase-N-sentinel pattern.
- **No write path.** OAB's `vector-db` is query-only (no upsert/delete/index-create). Composer matches.
- **No automated integration test.** Each provider requires real credentials + a populated index. Manual smoke-test pattern documented (like Arcade / Gamma).
- **No global provider-API-key settings.** Per OAB, `vectorDbApiKey` is a per-node field with `{{...}}` substitution. Users parameterize via state/env as needed. Only `OPENAI_API_KEY` (already in settings) is a global.
- **No local-embed fallback.** OAB uses OpenAI's embeddings API; no on-device embedding.

## 3. Architecture

```
src/
├── vectordb/                           (NEW)
│   ├── __init__.py
│   ├── embedding.py                    # embed_text(text, model, api_key, dimension) → list[float]
│   └── providers/
│       ├── __init__.py
│       ├── base.py                     # VectorDbResult + QueryConfig dataclasses
│       ├── pinecone.py                 # async def query(embedding, config) → list[VectorDbResult]
│       ├── qdrant.py
│       ├── chroma.py
│       ├── weaviate.py
│       └── milvus.py
├── executors/
│   └── vector_db.py                    (NEW)  # orchestrator
```

**Executor flow:**
```
read + substitute all config fields from node.data
embed query_prompt via OpenAI → list[float]
dispatch provider.query(embedding, config) via dict map
apply score_threshold filter
build output dict {query, results, total, provider, collection, dimension, top_k, joined?}
if join_results: render joined text
return delta with:
  variables[output_variable] = output dict
  variables.lastOutput = joined_text if join_results else output_dict
```

## 4. `VectorDbNodeData` — Pydantic tightening

Current placeholder:
```python
class VectorDbNodeData(BaseNodeData):
    config: dict[str, Any] = Field(default_factory=dict)
```

Replace with ~18 explicit fields matching OAB `types.ts` + executor reads:

```python
VectorDbProvider = Literal["pinecone", "qdrant", "chroma", "weaviate", "milvus"]
EmbeddingProvider = Literal["openai", "cohere", "jina", "pinecone-inference"]


class VectorDbNodeData(BaseNodeData):
    model_config = ConfigDict(populate_by_name=True)

    provider: VectorDbProvider = Field(default="pinecone", alias="vectorDbProvider")
    endpoint: str = Field(default="", alias="vectorDbEndpoint")
    api_key: str = Field(default="", alias="vectorDbApiKey")
    collection: str = Field(default="", alias="vectorDbCollection")
    dimension: int = Field(default=1536, alias="vectorDbDimension")
    embedding_provider: EmbeddingProvider = Field(
        default="openai", alias="vectorDbEmbeddingProvider"
    )
    embedding_model: str = Field(
        default="text-embedding-3-small", alias="vectorDbEmbeddingModel"
    )
    query_prompt: str = Field(default="", alias="vectorDbQueryPrompt")
    top_k: int = Field(default=5, alias="vectorDbTopK")
    score_threshold: float = Field(default=0.0, alias="vectorDbScoreThreshold")
    namespace: str | None = Field(default=None, alias="vectorDbNamespace")
    include_metadata: bool = Field(default=True, alias="vectorDbIncludeMetadata")
    include_vector: bool = Field(default=False, alias="vectorDbIncludeVector")
    text_field: str | None = Field(default=None, alias="vectorDbTextField")
    output_variable: str = Field(default="vectorDbResults", alias="vectorDbOutputVariable")
    metadata_filter: str | None = Field(default=None, alias="vectorDbMetadataFilter")

    # Join-results formatting (per-node, independent of the `join-chunks` executor)
    join_results: bool = Field(default=False, alias="vectorDbJoinResults")
    join_separator: str = Field(default="----", alias="vectorDbJoinSeparator")
    join_prefix: str = Field(default="", alias="vectorDbJoinPrefix")
    join_suffix: str = Field(default="", alias="vectorDbJoinSuffix")
```

`metadata_filter` is a JSON **string** (matches OAB — users enter JSON in the UI with `{{...}}` substitution). Executor parses it at runtime; parse failure logs warning and continues with empty filter.

## 5. Embedding — `src/vectordb/embedding.py`

```python
"""OpenAI embeddings for vector-db queries (Phase 6e).

Only OpenAI is shipped; cohere / jina / pinecone-inference raise
NotImplementedError until a future phase needs them.
"""

from __future__ import annotations

from typing import Any

import httpx

OPENAI_EMBEDDINGS_URL = "https://api.openai.com/v1/embeddings"


class EmbeddingError(RuntimeError):
    """Raised when an embedding call fails."""


async def embed_text_openai(
    text: str,
    *,
    model: str,
    api_key: str,
    dimension: int | None = None,
) -> list[float]:
    """Embed `text` via OpenAI's embeddings API.

    For `text-embedding-3-*` models, `dimension` may be passed to request a
    truncated vector. For ada-002 or other models, dimension is ignored
    (OpenAI returns the model's native size).
    """
    if not api_key:
        raise EmbeddingError("OpenAI API key is required for embeddings")

    payload: dict[str, Any] = {"input": text, "model": model}
    if dimension and model.startswith("text-embedding-3-"):
        payload["dimensions"] = dimension

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(
                OPENAI_EMBEDDINGS_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        except httpx.HTTPError as exc:
            raise EmbeddingError(f"OpenAI embeddings request failed: {exc}") from exc

    if resp.status_code >= 400:
        raise EmbeddingError(
            f"OpenAI embeddings error {resp.status_code}: {resp.text}"
        )
    body = resp.json()
    data = body.get("data")
    if not data or not isinstance(data, list):
        raise EmbeddingError(
            f"OpenAI embeddings response missing 'data' array: {body}"
        )
    vector = data[0].get("embedding")
    if not isinstance(vector, list):
        raise EmbeddingError(
            f"OpenAI embeddings response missing 'embedding': {body}"
        )
    return vector


__all__ = ["EmbeddingError", "OPENAI_EMBEDDINGS_URL", "embed_text_openai"]
```

Other embedding providers raise `NotImplementedError` inside the executor's dispatch.

## 6. Provider framework — `src/vectordb/providers/base.py`

```python
"""Provider framework for vector-db queries (Phase 6e)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class VectorDbResult:
    id: str
    score: float
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    vector: list[float] | None = None


@dataclass(frozen=True)
class QueryConfig:
    endpoint: str
    api_key: str | None
    collection: str
    top_k: int
    namespace: str | None = None
    metadata_filter: dict[str, Any] | None = None
    include_metadata: bool = True
    include_vector: bool = False
    text_field: str | None = None


class VectorDbProviderError(RuntimeError):
    """Raised when a provider's HTTP call fails."""


__all__ = ["QueryConfig", "VectorDbProviderError", "VectorDbResult"]
```

Each provider file exports a single top-level async function:

```python
async def query(embedding: list[float], config: QueryConfig) -> list[VectorDbResult]: ...
```

The executor holds a dispatch map:

```python
_PROVIDERS = {
    "pinecone": pinecone.query,
    "qdrant": qdrant.query,
    "chroma": chroma.query,
    "weaviate": weaviate.query,
    "milvus": milvus.query,
}
```

### 6.1 Pinecone — `src/vectordb/providers/pinecone.py`

POST `{endpoint}/query` with `{vector, topK, includeMetadata, includeValues, namespace?, filter?}`. `Api-Key` header.

Response: `{matches: [{id, score, metadata, values?}, ...]}`.

Extract text from `matches[i].metadata[text_field || "text"]`; default score is already cosine similarity.

### 6.2 Qdrant — `src/vectordb/providers/qdrant.py`

POST `{endpoint}/collections/{collection}/points/search` with `{vector, limit, with_payload, with_vector, filter?}`. `api-key` header (optional).

Response: `{result: [{id, score, payload, vector?}, ...]}`.

Extract text from `result[i].payload[text_field || "text"]`.

### 6.3 Chroma — `src/vectordb/providers/chroma.py`

POST `{endpoint}/api/v1/collections/{collection}/query` with `{query_embeddings: [[...]], n_results, where?}`. Bearer auth optional.

Response: `{ids: [[...]], distances: [[...]], metadatas: [[...]], documents: [[...]], embeddings?: [[...]]}` — batch-indexed.

Score = `1 - distance` (Chroma returns distances).

### 6.4 Weaviate — `src/vectordb/providers/weaviate.py`

POST `{endpoint}/v1/graphql` with GraphQL `Get { <ClassName>(nearVector: {vector, distance}, limit, where?) { _additional { id, distance, vector }, <fields> } }`. Bearer auth optional.

Response: parsed from GraphQL. Score = `1 - distance`.

### 6.5 Milvus — `src/vectordb/providers/milvus.py`

POST `{endpoint}/v1/vector/search` with `{collectionName, vector, limit, filter, outputFields: ["*"]}`. Bearer auth.

Response: `{data: [{id, distance, ...fields}, ...]}`.

**Milvus filter quirk** (OAB documents): Milvus expects DSL string (`'category == "docs"'`), not JSON. If `metadata_filter` dict is non-empty, log warning and skip the filter (per OAB's behavior).

## 7. `VectorDbExecutor` — orchestrator

**File:** `src/executors/vector_db.py` (new).

```python
"""vector-db node executor (Phase 6e).

Dispatches to one of 5 providers (Pinecone, Qdrant, Chroma, Weaviate,
Milvus) after embedding the query prompt via OpenAI.

See Phase 6e spec §7, ADR-0020.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from src.engine.state import WorkflowStateDict
from src.engine.workflow import VectorDbNode
from src.executors.base import register_executor
from src.variable_substitution import substitute
from src.vectordb.embedding import embed_text_openai
from src.vectordb.providers import chroma, milvus, pinecone, qdrant, weaviate
from src.vectordb.providers.base import QueryConfig, VectorDbResult

logger = logging.getLogger(__name__)


class VectorDbNodeError(RuntimeError):
    """Raised for config / embedding / provider failures."""


_ProviderFn = Callable[[list[float], QueryConfig], Awaitable[list[VectorDbResult]]]

_PROVIDERS: dict[str, _ProviderFn] = {
    "pinecone": pinecone.query,
    "qdrant": qdrant.query,
    "chroma": chroma.query,
    "weaviate": weaviate.query,
    "milvus": milvus.query,
}


@register_executor("vector-db")
class VectorDbExecutor:
    def __init__(self, node: VectorDbNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        data = self.node.data
        provider_name = data.provider

        # Resolve substituted string fields
        endpoint = substitute(data.endpoint, state)
        api_key = substitute(data.api_key, state) if data.api_key else ""
        collection = substitute(data.collection, state)
        prompt = substitute(data.query_prompt, state) if data.query_prompt else ""
        namespace = substitute(data.namespace, state) if data.namespace else None

        if not endpoint:
            raise VectorDbNodeError(
                f"vector-db node {self.node.id!r}: endpoint is required"
            )
        if not prompt:
            raise VectorDbNodeError(
                f"vector-db node {self.node.id!r}: query_prompt is required"
            )

        # Parse metadata_filter JSON (optional)
        metadata_filter: dict[str, Any] | None = None
        if data.metadata_filter:
            try:
                parsed = json.loads(substitute(data.metadata_filter, state))
                if isinstance(parsed, dict):
                    metadata_filter = parsed
            except (json.JSONDecodeError, TypeError):
                logger.warning(
                    "vector-db node %r: could not parse metadata_filter JSON; "
                    "proceeding without filter",
                    self.node.id,
                )

        # Embed via OpenAI (Phase 6e: only OpenAI is shipped)
        embedding = await self._embed(prompt)

        # Dispatch to provider
        provider_fn = _PROVIDERS.get(provider_name)
        if provider_fn is None:
            # Should be caught by Pydantic Literal; belt + suspenders
            raise VectorDbNodeError(
                f"vector-db node {self.node.id!r}: unknown provider {provider_name!r}"
            )

        config = QueryConfig(
            endpoint=endpoint,
            api_key=api_key or None,
            collection=collection,
            top_k=data.top_k,
            namespace=namespace,
            metadata_filter=metadata_filter,
            include_metadata=data.include_metadata,
            include_vector=data.include_vector,
            text_field=data.text_field,
        )
        raw_results = await provider_fn(embedding, config)

        # Score-threshold filter
        results = (
            [r for r in raw_results if r.score >= data.score_threshold]
            if data.score_threshold > 0
            else list(raw_results)
        )

        # Build output dict
        output: dict[str, Any] = {
            "query": prompt,
            "results": [self._result_to_dict(r) for r in results],
            "total": len(results),
            "provider": provider_name,
            "collection": collection,
            "dimension": len(embedding),
            "top_k": data.top_k,
        }

        # Optional join-results formatting
        joined: str | None = None
        if data.join_results:
            joined = self._render_joined(results)
            output["joined"] = joined

        output_var = data.output_variable

        return {
            "variables": {
                output_var: output,
                "lastOutput": joined if data.join_results else output,
            },
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "input": {
                        "provider": provider_name,
                        "collection": collection,
                        "top_k": data.top_k,
                        "query": prompt,
                    },
                    "output": output,
                }
            },
        }

    async def _embed(self, prompt: str) -> list[float]:
        data = self.node.data
        if data.embedding_provider != "openai":
            raise NotImplementedError(
                f"vector-db embedding_provider {data.embedding_provider!r} lands "
                f"in a later phase; Phase 6e ships OpenAI only"
            )
        # OpenAI key comes from settings (global)
        from src.config import get_settings

        settings = get_settings()
        if not settings.openai_api_key:
            raise VectorDbNodeError(
                f"vector-db node {self.node.id!r}: OPENAI_API_KEY is required "
                f"for embeddings"
            )
        return await embed_text_openai(
            prompt,
            model=data.embedding_model,
            api_key=settings.openai_api_key,
            dimension=data.dimension,
        )

    @staticmethod
    def _result_to_dict(r: VectorDbResult) -> dict[str, Any]:
        d: dict[str, Any] = {"id": r.id, "score": r.score, "text": r.text}
        if r.metadata:
            d["metadata"] = dict(r.metadata)
        if r.vector is not None:
            d["vector"] = list(r.vector)
        return d

    def _render_joined(self, results: list[VectorDbResult]) -> str:
        data = self.node.data
        separator = data.join_separator.replace("\\n", "\n")
        prefix_template = data.join_prefix.replace("\\n", "\n")
        suffix_template = data.join_suffix.replace("\\n", "\n")

        parts: list[str] = []
        for i, result in enumerate(results, start=1):
            prefix = prefix_template.replace("{{index}}", str(i))
            suffix = suffix_template.replace("{{index}}", str(i))
            parts.append(f"{prefix}{result.text}{suffix}")
        return separator.join(parts)


__all__ = ["VectorDbExecutor", "VectorDbNodeError"]
```

## 8. Error model

| Condition                                           | Exception                     | Execution |
|-----------------------------------------------------|-------------------------------|-----------|
| `endpoint` empty after substitution                 | `VectorDbNodeError`           | `failed`  |
| `query_prompt` empty after substitution             | `VectorDbNodeError`           | `failed`  |
| `OPENAI_API_KEY` unset (embedding)                  | `VectorDbNodeError`           | `failed`  |
| OpenAI embeddings HTTP error                        | `EmbeddingError`              | `failed`  |
| `embedding_provider` != "openai"                    | `NotImplementedError`         | `failed`  |
| Provider HTTP error (4xx/5xx/network)               | `VectorDbProviderError`       | `failed`  |
| Unknown provider (shouldn't reach)                  | `VectorDbNodeError`           | `failed`  |
| `metadata_filter` malformed JSON                    | (none — log warn + skip)      | `completed` |
| Empty results                                       | (none — pass)                 | `completed` |

## 9. Test plan

### 9.1 Unit tests

**`tests/unit/vectordb/test_embedding.py`** — 4-5 tests
- Successful embedding via `pytest-httpx`.
- Non-OpenAI model doesn't send `dimensions`.
- `text-embedding-3-small` + dimension sends `dimensions`.
- OpenAI 401 → `EmbeddingError`.
- Missing `data` array → `EmbeddingError`.
- Missing `api_key` → `EmbeddingError`.

**`tests/unit/vectordb/providers/test_pinecone.py`** — 4 tests
- Happy path: POST body shape + Api-Key header; result parsing.
- Namespace included when set.
- Filter included when non-empty.
- HTTP 500 → `VectorDbProviderError`.

**`tests/unit/vectordb/providers/test_qdrant.py`** — 3 tests (happy path, filter, error).
**`tests/unit/vectordb/providers/test_chroma.py`** — 3 tests (batch-shape parsing, `1 - distance` score).
**`tests/unit/vectordb/providers/test_weaviate.py`** — 3 tests (GraphQL shape, `1 - distance` score, GraphQL errors).
**`tests/unit/vectordb/providers/test_milvus.py`** — 3 tests (happy path, filter-ignore warning, error).

**`tests/unit/executors/test_vector_db.py`** — 10+ tests
- Happy path Pinecone: embed + query + output shape.
- Substitution in endpoint / collection / prompt / namespace.
- Score threshold filter (e.g., threshold=0.5 removes low-score results).
- Join results with separator/prefix/suffix + `{{index}}` substitution.
- Output variable routing (default `vectorDbResults` + custom).
- `lastOutput` = joined text when `joinResults=True`.
- `lastOutput` = output dict when `joinResults=False`.
- Empty endpoint / prompt → `VectorDbNodeError`.
- Non-openai embedding_provider → `NotImplementedError`.
- Metadata filter valid JSON → passed to provider.
- Metadata filter malformed JSON → logged warning + skipped.
- Executor registered in `_REGISTRY`.
- Provider dispatch covers all 5 types.

### 9.2 Integration — manual only

Each provider requires real credentials + a populated index. Not automated.
User can set up one provider locally (e.g., Qdrant via docker) and run an ad-hoc smoke script. CHANGELOG documents.

## 10. Phase-exit checklist

- [ ] All unit tests green (~40 new tests).
- [ ] Ruff + format + pyright strict clean.
- [ ] `CHANGELOG.md` — Phase 6e section.
- [ ] `CLAUDE.md` — phase table: 6e → ✅, Phase 6 fully complete, next is Phase 7.
- [ ] ADR-0020 appended + `Implemented by` backfilled.

## 11. ADR-0020 (summary — full entry appended to `docs/design/decisions.md`)

**Title.** VectorDB provider framework — one file per provider, uniform `async def query(...)` signature.

**Decision.** Each of 5 providers lives in `src/vectordb/providers/<name>.py` with a top-level `async def query(embedding, config) -> list[VectorDbResult]`. Executor dispatches via a dict map on `provider_name`. No abstract base class — Python's duck typing + structural matching via the Callable type alias is sufficient.

**Alternatives considered.**
- ABC / Protocol class — overkill; no polymorphic state.
- Single mega-file with if-elif dispatch — 600+ LOC; hard to test providers in isolation.
- External plugin registry — YAGNI; providers are known + finite.

**Consequences.**
- Each provider is independently testable and replaceable.
- Adding a new provider = one new file + one new dict entry.
- No shared state across providers — `QueryConfig` + `VectorDbResult` dataclasses are the contract.
- Phase 7+ can promote non-OpenAI embedding providers to real implementations by adding `src/vectordb/embedding.py` helpers.

**Implemented by.** Phase 6e (commits TBD).

**Related.** ADR-0006 (LLM provider framework — analogous pattern), [Phase 6e spec](../superpowers/specs/2026-04-21-phase-6e-vector-db-design.md).

## 12. Self-review

- **Placeholders:** None. All 5 providers, 18 Pydantic fields, OpenAI embedding function all fully specified.
- **Internal consistency:** `provider` literal (§4) matches `_PROVIDERS` dict keys (§7) matches test plan (§9). `VectorDbResult` / `QueryConfig` dataclasses (§6) match executor imports (§7).
- **Scope:** Biggest Phase 6 sub-phase. 7 tasks as planned.
- **Ambiguity:** `metadata_filter` parse failure = log+skip (not raise). `score_threshold=0` = no filter. `include_metadata=True` default per OAB. Milvus filter-ignore behavior pinned. `\n` escape in join strings explicitly replaced.

## 13. Risks + future

- **Vendor API drift.** Each provider's REST shape is pinned by unit tests. Drift caught at test time.
- **Milvus DSL filter.** Phase 6e logs-and-ignores non-Milvus filters. A future phase could add DSL translation if users demand it.
- **Per-node API keys.** Users must manage keys in state/env. Phase 7+ can add encrypted per-user keys (ADR-0005 pattern extension).
- **No write operations.** If users need to upsert vectors, that's a Phase 7+ extension (new node type, probably `vector-db-write`).
