"""Provider framework for vector-db queries + upserts (Phase 6e + insert).

See ADR-0020.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class VectorDbResult:
    """One result row from a vector-db query.

    id: document identifier from the vector store
    score: similarity score (higher = more similar for most providers;
           some providers return distance and the provider module converts)
    text: extracted text content (from text_field / content / page_content / etc.)
    metadata: full metadata dict from the store (excluded if include_metadata=False)
    vector: raw embedding (included only when include_vector=True)
    """

    id: str
    score: float
    text: str
    metadata: dict[str, Any] = field(default_factory=dict[str, Any])
    vector: list[float] | None = None


@dataclass(frozen=True)
class QueryConfig:
    """Runtime config shared across all providers.

    endpoint: base URL (no trailing slash needed)
    api_key: provider-specific API key; None if not required
    collection: collection / index / class name
    top_k: number of results to return
    namespace: Pinecone-specific (optional)
    metadata_filter: JSON-compatible dict filter; providers interpret per their
                     native filter language
    include_metadata: if False, returned results have empty metadata dict
    include_vector: if True, returned results include the raw embedding vector
    text_field: override for the metadata field that holds result text
    """

    endpoint: str
    api_key: str | None
    collection: str
    top_k: int
    namespace: str | None = None
    metadata_filter: dict[str, Any] | None = None
    include_metadata: bool = True
    include_vector: bool = False
    text_field: str | None = None


@dataclass(frozen=True)
class UpsertDocument:
    """One chunk to be inserted into the vector store.

    id: optional stable identifier; if None the provider generates one
        (typically a hash of the text + metadata).  Pass an id when you
        want re-runs to overwrite the previous version of the same chunk
        instead of duplicating.
    text: the chunk text the embedding was computed from.  Stored under
        the configured `text_field` so the query path can return it.
    embedding: pre-computed embedding vector (the executor handles
        embedding upstream — the provider just stores).
    metadata: arbitrary JSON metadata; queryable via filters on most
        providers.  The text field is merged in before storage.
    """

    text: str
    embedding: list[float]
    id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict[str, Any])


@dataclass(frozen=True)
class UpsertConfig:
    """Runtime config for vector-db upsert operations.

    Mirror of QueryConfig but trimmed to what insert needs — top_k,
    metadata_filter, include_* flags don't apply.
    """

    endpoint: str
    api_key: str | None
    collection: str
    namespace: str | None = None
    text_field: str = "text"


@dataclass(frozen=True)
class UpsertResult:
    """Outcome of an upsert call — what landed in the store and how
    callers should report success in the workflow output."""

    inserted_count: int
    ids: list[str]


class VectorDbProviderError(RuntimeError):
    """Raised when a provider's HTTP call fails."""


__all__ = [
    "QueryConfig",
    "UpsertConfig",
    "UpsertDocument",
    "UpsertResult",
    "VectorDbProviderError",
    "VectorDbResult",
]
