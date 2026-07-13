"""vector-db node executor (Phase 6e + insert mode).

Dispatches to one of 5 providers (Pinecone, Qdrant, Chroma, Weaviate,
Milvus).  Two modes:

  - `query` (default): embed the query_prompt, retrieve top_k matches.
  - `upsert`: embed each chunk and insert into the configured collection.
    Documents come from a simpleeval expression — either a list of
    `{id?, text, metadata?}` dicts (pre-chunked) or a single string
    (auto-chunked using chunk_size + chunk_overlap).

See Phase 6e spec §7, ADR-0020.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from src.config import get_settings
from src.executors._eval import EvalError, evaluate
from src.executors.base import register_executor
from src.security.encryption import decrypt_marked
from src.variable_substitution import substitute
from src.vectordb.embedding import EmbeddingConfig, embed_text
from src.vectordb.providers import chroma, milvus, pinecone, qdrant, weaviate
from src.vectordb.providers.base import (
    QueryConfig,
    UpsertConfig,
    UpsertDocument,
    UpsertResult,
    VectorDbResult,
)

if TYPE_CHECKING:
    from src.engine.state import WorkflowStateDict
    from src.engine.workflow import VectorDbNode

logger = logging.getLogger(__name__)


class VectorDbNodeError(RuntimeError):
    """Raised for config / embedding / provider failures."""


_QueryFn = Callable[[list[float], QueryConfig], Awaitable[list[VectorDbResult]]]
_UpsertFn = Callable[[list[UpsertDocument], UpsertConfig], Awaitable[UpsertResult]]

_QUERY_PROVIDERS: dict[str, _QueryFn] = {
    "pinecone": pinecone.query,
    "qdrant": qdrant.query,
    "chroma": chroma.query,
    "weaviate": weaviate.query,
    "milvus": milvus.query,
}

_UPSERT_PROVIDERS: dict[str, _UpsertFn] = {
    "pinecone": pinecone.upsert,
    "qdrant": qdrant.upsert,
    "chroma": chroma.upsert,
    "weaviate": weaviate.upsert,
    "milvus": milvus.upsert,
}


@register_executor("vector-db")
class VectorDbExecutor:
    def __init__(self, node: VectorDbNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        operation = (self.node.data.operation or "query").lower()
        if operation == "upsert":
            return await self._run_upsert(state)
        if operation == "query":
            return await self._run_query(state)
        raise VectorDbNodeError(
            f"vector-db node {self.node.id!r}: operation {operation!r} not "
            "supported (need 'query' or 'upsert')."
        )

    async def _run_query(self, state: WorkflowStateDict) -> dict[str, Any]:
        data = self.node.data
        provider_name = data.provider

        endpoint = substitute(data.endpoint, state)
        # P0-5: apiKey is encrypted at rest by the workflow API; decrypt
        # before substitution (a no-op on plaintext/pass-through values, so
        # this is safe even for workflows saved before encryption existed).
        api_key = substitute(decrypt_marked(data.api_key), state) if data.api_key else ""
        collection = substitute(data.collection, state)
        prompt = substitute(data.query_prompt, state) if data.query_prompt else ""
        namespace = substitute(data.namespace, state) if data.namespace else None

        if not endpoint:
            raise VectorDbNodeError(f"vector-db node {self.node.id!r}: endpoint is required")
        if not prompt:
            raise VectorDbNodeError(f"vector-db node {self.node.id!r}: query_prompt is required")

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

        embedding = await self._embed(prompt)

        provider_fn = _QUERY_PROVIDERS.get(provider_name)
        if provider_fn is None:
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

        results = (
            [r for r in raw_results if r.score >= data.score_threshold]
            if data.score_threshold > 0
            else list(raw_results)
        )

        output: dict[str, Any] = {
            "query": prompt,
            "results": [self._result_to_dict(r) for r in results],
            "total": len(results),
            "provider": provider_name,
            "collection": collection,
            "dimension": len(embedding),
            "top_k": data.top_k,
        }

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
        provider = data.embedding_provider
        api_key = (
            decrypt_marked(data.embedding_api_key)
            if data.embedding_api_key
            else self._embedding_api_key_from_settings(provider)
        )
        if not api_key:
            raise VectorDbNodeError(
                f"vector-db node {self.node.id!r}: API key is required for "
                f"{provider!r} embeddings. Set vectorDbEmbeddingApiKey on the node "
                "or configure the matching provider API key in the backend environment."
            )
        return await embed_text(
            prompt,
            config=EmbeddingConfig(
                provider=provider,
                model=data.embedding_model,
                api_key=api_key,
                dimension=data.dimension,
                base_url=data.embedding_base_url or None,
            ),
        )

    @staticmethod
    def _embedding_api_key_from_settings(provider: str) -> str:
        settings = get_settings()
        return {
            "openai": settings.openai_api_key,
            "dashscope": settings.dashscope_api_key,
            "siliconflow": settings.siliconflow_api_key,
            "zhipu": settings.zhipu_api_key,
            "cohere": settings.cohere_api_key,
            "jina": settings.jina_api_key,
            "voyage": settings.voyage_api_key,
            "pinecone-inference": settings.pinecone_inference_api_key,
            "custom-openai-compatible": "",
        }.get(provider, "")

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

    # ─── Upsert path ──────────────────────────────────────────────

    async def _run_upsert(self, state: WorkflowStateDict) -> dict[str, Any]:
        data = self.node.data
        provider_name = data.provider

        endpoint = substitute(data.endpoint, state)
        api_key = substitute(decrypt_marked(data.api_key), state) if data.api_key else ""
        collection = substitute(data.collection, state)
        namespace = substitute(data.namespace, state) if data.namespace else None

        if not endpoint:
            raise VectorDbNodeError(
                f"vector-db node {self.node.id!r}: endpoint is required for upsert"
            )
        if not data.documents:
            raise VectorDbNodeError(
                f"vector-db node {self.node.id!r}: documents expression is required "
                "for upsert (a simpleeval expression that yields a list of "
                "{id?, text, metadata?} dicts, or a single string for auto-chunking)"
            )

        # Resolve the documents expression against state.  Designers
        # typically reference an upstream node's output here — e.g.
        # `lastOutput` for a scrape that produced raw text, or
        # `chunks` for a transform that already split it.
        try:
            raw_docs = evaluate(data.documents, state)
        except EvalError as exc:
            raise VectorDbNodeError(
                f"vector-db node {self.node.id!r}: documents expression failed: {exc}"
            ) from exc

        chunks = self._coerce_to_chunks(raw_docs, data.chunk_size, data.chunk_overlap)
        if not chunks:
            raise VectorDbNodeError(
                f"vector-db node {self.node.id!r}: documents expression produced no "
                "chunks — check the upstream output isn't empty."
            )

        # Embed each chunk.  Sequential rather than concurrent because
        # the embedding API rate-limits aggressively and chunks tend
        # to be small batches (10s, not 1000s).  If this becomes a
        # bottleneck, swap for asyncio.gather with a Semaphore.
        documents: list[UpsertDocument] = []
        for chunk in chunks:
            embedding = await self._embed(chunk["text"])
            documents.append(
                UpsertDocument(
                    id=chunk.get("id"),
                    text=chunk["text"],
                    embedding=embedding,
                    metadata=chunk.get("metadata") or {},
                )
            )

        upsert_fn = _UPSERT_PROVIDERS.get(provider_name)
        if upsert_fn is None:
            raise VectorDbNodeError(
                f"vector-db node {self.node.id!r}: unknown provider {provider_name!r}"
            )
        config = UpsertConfig(
            endpoint=endpoint,
            api_key=api_key or None,
            collection=collection,
            namespace=namespace,
            text_field=data.text_field or "text",
        )
        result = await upsert_fn(documents, config)

        output: dict[str, Any] = {
            "operation": "upsert",
            "provider": provider_name,
            "collection": collection,
            "inserted_count": result.inserted_count,
            "ids": list(result.ids),
            "dimension": len(documents[0].embedding) if documents else 0,
        }
        output_var = data.output_variable
        return {
            "variables": {
                output_var: output,
                "lastOutput": output,
            },
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "input": {
                        "operation": "upsert",
                        "provider": provider_name,
                        "collection": collection,
                        "chunk_count": len(documents),
                    },
                    "output": output,
                }
            },
        }

    def _coerce_to_chunks(
        self,
        raw: Any,
        chunk_size: int,
        chunk_overlap: int,
    ) -> list[dict[str, Any]]:
        """Normalise the documents expression's value into a list of
        chunk dicts: `[{id?, text, metadata?}, ...]`.

        Three accepted shapes:

        * **list of dicts** with at least a `text` field — used as-is
          after type-checking.
        * **list of strings** — each string becomes a chunk dict.
        * **single string** — split into overlapping windows of
          `chunk_size` characters, stepping by `chunk_size - overlap`.
          Naive char-window chunking; designers wanting token-aware or
          semantic chunking pre-process upstream and pass a list.
        """
        if raw is None:
            return []
        if isinstance(raw, str):
            return self._chunk_string(raw, chunk_size, chunk_overlap)
        if isinstance(raw, list):
            chunks: list[dict[str, Any]] = []
            for item in raw:
                if isinstance(item, str):
                    if item.strip():
                        chunks.append({"text": item})
                elif isinstance(item, dict):
                    text = item.get("text") or item.get("content") or ""
                    if not isinstance(text, str) or not text.strip():
                        continue
                    chunk: dict[str, Any] = {"text": text}
                    if "id" in item and isinstance(item["id"], str):
                        chunk["id"] = item["id"]
                    if "metadata" in item and isinstance(item["metadata"], dict):
                        chunk["metadata"] = dict(item["metadata"])
                    chunks.append(chunk)
                else:
                    raise VectorDbNodeError(
                        f"vector-db node {self.node.id!r}: documents list contained "
                        f"unexpected item type {type(item).__name__}; expected str or "
                        "dict with `text` field."
                    )
            return chunks
        raise VectorDbNodeError(
            f"vector-db node {self.node.id!r}: documents expression yielded "
            f"{type(raw).__name__}; expected str, list[str], or list[dict]."
        )

    @staticmethod
    def _chunk_string(text: str, chunk_size: int, overlap: int) -> list[dict[str, Any]]:
        """Char-window chunking with overlap.  Cheap and deterministic.

        Why char-window not token-window: tokenisers vary by model
        (cl100k vs o200k vs Sentencepiece vs whatever Gemini uses), so
        char count is the only universal unit.  For most prose 1
        char ≈ 0.25 tokens, so default chunk_size=1000 is ~250 tokens
        — well below any embedding model's 8K context.
        """
        if chunk_size <= 0:
            raise VectorDbNodeError(f"chunk_size must be > 0; got {chunk_size}")
        if overlap < 0 or overlap >= chunk_size:
            raise VectorDbNodeError(
                f"chunk_overlap must satisfy 0 <= overlap < chunk_size; "
                f"got overlap={overlap}, chunk_size={chunk_size}"
            )
        if not text.strip():
            return []
        step = chunk_size - overlap
        chunks: list[dict[str, Any]] = []
        position = 0
        while position < len(text):
            window = text[position : position + chunk_size]
            if window.strip():
                chunks.append({"text": window})
            position += step
        return chunks


__all__ = ["VectorDbExecutor", "VectorDbNodeError"]
