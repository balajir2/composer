"""vector-db node executor (Phase 6e).

Dispatches to one of 5 providers (Pinecone, Qdrant, Chroma, Weaviate,
Milvus) after embedding the query prompt via OpenAI.

See Phase 6e spec §7, ADR-0020.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from src.config import get_settings
from src.executors.base import register_executor
from src.variable_substitution import substitute
from src.vectordb.embedding import embed_text_openai
from src.vectordb.providers import chroma, milvus, pinecone, qdrant, weaviate
from src.vectordb.providers.base import QueryConfig, VectorDbResult

if TYPE_CHECKING:
    from src.engine.state import WorkflowStateDict
    from src.engine.workflow import VectorDbNode

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

        endpoint = substitute(data.endpoint, state)
        api_key = substitute(data.api_key, state) if data.api_key else ""
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

        provider_fn = _PROVIDERS.get(provider_name)
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
        if data.embedding_provider != "openai":
            raise NotImplementedError(
                f"vector-db embedding_provider {data.embedding_provider!r} lands "
                f"in a later phase; Phase 6e ships OpenAI only"
            )
        settings = get_settings()
        if not settings.openai_api_key:
            raise VectorDbNodeError(
                f"vector-db node {self.node.id!r}: OPENAI_API_KEY is required for embeddings"
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
