"""Tests for the vector-db executor orchestrator (Phase 6e)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from src.engine.state import initial_state
from src.engine.workflow import VectorDbNode
from src.executors.vector_db import VectorDbExecutor, VectorDbNodeError
from src.vectordb.providers.base import QueryConfig, VectorDbResult

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


@pytest.fixture(autouse=True)
def _stub_openai_key(monkeypatch: pytest.MonkeyPatch) -> None:  # pyright: ignore[reportUnusedFunction]
    from src.config import get_settings

    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    get_settings.cache_clear()


def _node(**data_overrides: Any) -> VectorDbNode:
    data: dict[str, Any] = {
        "label": "VDB",
        "vectorDbProvider": "pinecone",
        "vectorDbEndpoint": "https://idx.pinecone.io",
        "vectorDbCollection": "mine",
        "vectorDbQueryPrompt": "find stuff",
        "vectorDbTopK": 5,
    }
    data.update(data_overrides)
    return VectorDbNode.model_validate(
        {
            "id": "vdb",
            "type": "vector-db",
            "position": {"x": 0, "y": 0},
            "data": data,
        }
    )


async def _fake_embed(*_args: Any, **_kwargs: Any) -> list[float]:
    return [0.1, 0.2, 0.3]


def _stub_provider(
    monkeypatch: pytest.MonkeyPatch,
    results: list[VectorDbResult],
    *,
    captured: dict[str, Any] | None = None,
) -> None:
    """Replace _QUERY_PROVIDERS with a dict mapping to a stub for the active provider."""
    import src.executors.vector_db as vdb_mod

    async def _stub(embedding: list[float], config: QueryConfig) -> list[VectorDbResult]:
        if captured is not None:
            captured["embedding"] = embedding
            captured["config"] = config
        return results

    # Build a new dispatch map with EVERY provider pointing to the stub so
    # dispatch always hits the stub regardless of provider name.
    stub_map: dict[str, Callable[[list[float], QueryConfig], Awaitable[list[VectorDbResult]]]] = {
        "pinecone": _stub,
        "qdrant": _stub,
        "chroma": _stub,
        "weaviate": _stub,
        "milvus": _stub,
    }
    monkeypatch.setattr(vdb_mod, "_QUERY_PROVIDERS", stub_map)
    monkeypatch.setattr(vdb_mod, "embed_text", _fake_embed)


async def test_happy_path_pinecone(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_provider(
        monkeypatch,
        [
            VectorDbResult(id="1", score=0.9, text="hello", metadata={"k": "v"}),
        ],
    )
    delta = await VectorDbExecutor(_node()).arun(initial_state())
    output = delta["variables"]["vectorDbResults"]
    assert output["query"] == "find stuff"
    assert output["provider"] == "pinecone"
    assert output["total"] == 1
    assert output["results"][0]["id"] == "1"
    assert output["results"][0]["text"] == "hello"
    assert delta["variables"]["lastOutput"] == output  # no join_results


async def test_substitution_in_endpoint_collection_prompt_namespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    _stub_provider(monkeypatch, [], captured=captured)
    state = initial_state()
    state["variables"]["host"] = "myhost"
    state["variables"]["col"] = "mycol"
    state["variables"]["ns"] = "myns"
    state["variables"]["q"] = "find it"

    node = _node(
        vectorDbEndpoint="https://{{host}}.example.com",
        vectorDbCollection="{{col}}",
        vectorDbQueryPrompt="{{q}}",
        vectorDbNamespace="{{ns}}",
    )
    await VectorDbExecutor(node).arun(state)

    cfg: QueryConfig = captured["config"]
    assert cfg.endpoint == "https://myhost.example.com"
    assert cfg.collection == "mycol"
    assert cfg.namespace == "myns"


async def test_score_threshold_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_provider(
        monkeypatch,
        [
            VectorDbResult(id="1", score=0.9, text="a"),
            VectorDbResult(id="2", score=0.3, text="b"),
            VectorDbResult(id="3", score=0.6, text="c"),
        ],
    )
    delta = await VectorDbExecutor(_node(vectorDbScoreThreshold=0.5)).arun(initial_state())
    output = delta["variables"]["vectorDbResults"]
    ids = [r["id"] for r in output["results"]]
    assert ids == ["1", "3"]
    assert output["total"] == 2


async def test_join_results_with_separator_prefix_suffix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_provider(
        monkeypatch,
        [
            VectorDbResult(id="1", score=0.9, text="alpha"),
            VectorDbResult(id="2", score=0.8, text="beta"),
        ],
    )
    node = _node(
        vectorDbJoinResults=True,
        vectorDbJoinSeparator="\\n---\\n",
        vectorDbJoinPrefix="[{{index}}] ",
        vectorDbJoinSuffix=" <",
    )
    delta = await VectorDbExecutor(node).arun(initial_state())
    assert delta["variables"]["lastOutput"] == "[1] alpha <\n---\n[2] beta <"
    assert delta["variables"]["vectorDbResults"]["joined"] == "[1] alpha <\n---\n[2] beta <"


async def test_custom_output_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_provider(
        monkeypatch,
        [
            VectorDbResult(id="1", score=0.9, text="x"),
        ],
    )
    node = _node(vectorDbOutputVariable="myHits")
    delta = await VectorDbExecutor(node).arun(initial_state())
    assert "myHits" in delta["variables"]
    assert delta["variables"]["myHits"]["results"][0]["id"] == "1"


async def test_empty_endpoint_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_provider(monkeypatch, [])
    with pytest.raises(VectorDbNodeError, match="endpoint is required"):
        await VectorDbExecutor(_node(vectorDbEndpoint="")).arun(initial_state())


async def test_empty_prompt_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_provider(monkeypatch, [])
    with pytest.raises(VectorDbNodeError, match="query_prompt is required"):
        await VectorDbExecutor(_node(vectorDbQueryPrompt="")).arun(initial_state())


async def test_non_openai_embedding_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COHERE_API_KEY", "test-cohere-key")
    from src.config import get_settings

    get_settings.cache_clear()
    _stub_provider(monkeypatch, [])
    node = _node(vectorDbEmbeddingProvider="cohere")
    delta = await VectorDbExecutor(node).arun(initial_state())
    assert delta["variables"]["vectorDbResults"]["provider"] == "pinecone"


async def test_embedding_key_can_come_from_node(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    _stub_provider(monkeypatch, [], captured=captured)
    node = _node(
        vectorDbEmbeddingProvider="dashscope",
        vectorDbEmbeddingApiKey="node-key",
        vectorDbEmbeddingModel="text-embedding-v4",
    )
    await VectorDbExecutor(node).arun(initial_state())
    assert captured["embedding"] == [0.1, 0.2, 0.3]


async def test_metadata_filter_valid_json_passed_to_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    _stub_provider(monkeypatch, [], captured=captured)
    node = _node(vectorDbMetadataFilter='{"category":"docs"}')
    await VectorDbExecutor(node).arun(initial_state())
    cfg: QueryConfig = captured["config"]
    assert cfg.metadata_filter == {"category": "docs"}


async def test_metadata_filter_malformed_json_logged_and_skipped(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    captured: dict[str, Any] = {}
    _stub_provider(monkeypatch, [], captured=captured)
    node = _node(vectorDbMetadataFilter="{not-json")
    with caplog.at_level(logging.WARNING):
        await VectorDbExecutor(node).arun(initial_state())
    assert any("metadata_filter" in rec.message for rec in caplog.records)
    cfg: QueryConfig = captured["config"]
    assert cfg.metadata_filter is None


async def test_encrypted_api_key_is_decrypted_before_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P0-5: vectorDbApiKey is now encrypted at rest by the workflow API —
    the executor must decrypt it before handing it to the provider, or
    every workflow saved after that change sends ciphertext as the API key."""
    from src.security.encryption import encrypt_marked

    captured: dict[str, Any] = {}
    _stub_provider(monkeypatch, [], captured=captured)
    node = _node(vectorDbApiKey=encrypt_marked("real-pinecone-key"))
    await VectorDbExecutor(node).arun(initial_state())
    cfg: QueryConfig = captured["config"]
    assert cfg.api_key == "real-pinecone-key"


async def test_encrypted_embedding_api_key_is_decrypted_before_embed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.executors.vector_db as vdb_mod
    from src.security.encryption import encrypt_marked

    captured: dict[str, Any] = {}

    async def _capturing_embed(prompt: str, *, config: Any) -> list[float]:
        captured["embedding_config"] = config
        return [0.1, 0.2, 0.3]

    _stub_provider(monkeypatch, [])
    monkeypatch.setattr(vdb_mod, "embed_text", _capturing_embed)
    node = _node(
        vectorDbEmbeddingProvider="dashscope",
        vectorDbEmbeddingApiKey=encrypt_marked("real-dashscope-key"),
        vectorDbEmbeddingModel="text-embedding-v4",
    )
    await VectorDbExecutor(node).arun(initial_state())
    assert captured["embedding_config"].api_key == "real-dashscope-key"


async def test_missing_openai_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.config import get_settings

    monkeypatch.setenv("OPENAI_API_KEY", "")
    get_settings.cache_clear()
    with pytest.raises(VectorDbNodeError, match="API key is required"):
        await VectorDbExecutor(_node()).arun(initial_state())


async def test_executor_is_registered() -> None:
    from src.executors.base import build_executor

    executor = build_executor(_node())
    assert isinstance(executor, VectorDbExecutor)


@pytest.mark.parametrize(
    "provider",
    ["pinecone", "qdrant", "chroma", "weaviate", "milvus"],
)
async def test_dispatch_covers_all_providers(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    captured: dict[str, Any] = {}
    _stub_provider(
        monkeypatch,
        [
            VectorDbResult(id="1", score=0.9, text="x"),
        ],
        captured=captured,
    )
    node = _node(vectorDbProvider=provider)
    delta = await VectorDbExecutor(node).arun(initial_state())
    assert delta["variables"]["vectorDbResults"]["provider"] == provider


# ─── Upsert mode (Phase 6e+ insert) ──────────────────────────────────


def _stub_upsert_provider(
    monkeypatch: pytest.MonkeyPatch,
    *,
    captured: dict[str, Any] | None = None,
    inserted_count: int = 0,
) -> None:
    """Replace _UPSERT_PROVIDERS so dispatch hits a recording stub."""
    import src.executors.vector_db as vdb_mod
    from src.vectordb.providers.base import (
        UpsertConfig,
        UpsertDocument,
        UpsertResult,
    )

    async def _stub(
        documents: list[UpsertDocument],
        config: UpsertConfig,
    ) -> UpsertResult:
        if captured is not None:
            captured["documents"] = list(documents)
            captured["config"] = config
        ids = [d.id or f"auto-{i}" for i, d in enumerate(documents)]
        count = inserted_count if inserted_count else len(documents)
        return UpsertResult(inserted_count=count, ids=ids)

    stub_map = dict.fromkeys(("pinecone", "qdrant", "chroma", "weaviate", "milvus"), _stub)
    monkeypatch.setattr(vdb_mod, "_UPSERT_PROVIDERS", stub_map)
    monkeypatch.setattr(vdb_mod, "embed_text", _fake_embed)


async def test_upsert_with_pre_chunked_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """When `documents` evaluates to a list of dicts, each becomes a
    chunk verbatim — the executor doesn't auto-chunk."""
    captured: dict[str, Any] = {}
    _stub_upsert_provider(monkeypatch, captured=captured)

    node = _node(
        vectorDbOperation="upsert",
        vectorDbDocuments="chunks",
    )
    state = initial_state()
    state["variables"]["chunks"] = [
        {"text": "alpha"},
        {"text": "beta", "metadata": {"source": "test"}},
        {"id": "user-supplied", "text": "gamma"},
    ]
    delta = await VectorDbExecutor(node).arun(state)

    docs: list[Any] = captured["documents"]
    assert [d.text for d in docs] == ["alpha", "beta", "gamma"]
    assert docs[1].metadata == {"source": "test"}
    assert docs[2].id == "user-supplied"
    assert delta["variables"]["lastOutput"]["operation"] == "upsert"
    assert delta["variables"]["lastOutput"]["inserted_count"] == 3


async def test_upsert_auto_chunks_raw_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """A single string flows through char-window chunking with overlap."""
    captured: dict[str, Any] = {}
    _stub_upsert_provider(monkeypatch, captured=captured)

    raw = "x" * 2500  # forces multiple chunks at default 1000/100
    node = _node(
        vectorDbOperation="upsert",
        vectorDbDocuments="lastOutput",
        vectorDbChunkSize=1000,
        vectorDbChunkOverlap=100,
    )
    state = initial_state()
    state["variables"]["lastOutput"] = raw
    delta = await VectorDbExecutor(node).arun(state)

    docs: list[Any] = captured["documents"]
    # 2500 chars with step 900 (size - overlap) → windows at 0, 900, 1800
    assert len(docs) == 3
    assert docs[0].text.startswith("x") and len(docs[0].text) == 1000
    assert delta["variables"]["lastOutput"]["inserted_count"] == 3


async def test_upsert_missing_documents_expression_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Misconfigured node — clear error rather than running with no
    documents."""
    _stub_upsert_provider(monkeypatch)
    node = _node(vectorDbOperation="upsert")  # no vectorDbDocuments
    with pytest.raises(VectorDbNodeError, match="documents expression is required"):
        await VectorDbExecutor(node).arun(initial_state())


async def test_upsert_empty_collection_yields_clear_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the documents expression resolves to an empty list (no
    chunks to insert) the executor refuses with a clear message —
    silently succeeding with 0 chunks would mask upstream bugs."""
    _stub_upsert_provider(monkeypatch)
    node = _node(
        vectorDbOperation="upsert",
        vectorDbDocuments="chunks",
    )
    state = initial_state()
    state["variables"]["chunks"] = []
    with pytest.raises(VectorDbNodeError, match="produced no"):
        await VectorDbExecutor(node).arun(state)


async def test_upsert_unknown_operation_rejected() -> None:
    node = _node(vectorDbOperation="delete")  # not implemented
    with pytest.raises(VectorDbNodeError, match="not supported"):
        await VectorDbExecutor(node).arun(initial_state())


async def test_upsert_dispatches_to_correct_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify each of the 5 providers gets dispatched to its upsert fn."""
    for provider in ("pinecone", "qdrant", "chroma", "weaviate", "milvus"):
        captured: dict[str, Any] = {}
        _stub_upsert_provider(monkeypatch, captured=captured)
        node = _node(
            vectorDbProvider=provider,
            vectorDbOperation="upsert",
            vectorDbDocuments="chunks",
        )
        state = initial_state()
        state["variables"]["chunks"] = [{"text": "hi"}]
        delta = await VectorDbExecutor(node).arun(state)
        assert delta["variables"]["lastOutput"]["provider"] == provider
        assert delta["variables"]["lastOutput"]["operation"] == "upsert"
