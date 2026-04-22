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
    """Replace _PROVIDERS with a dict mapping to a stub for the active provider."""
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
    monkeypatch.setattr(vdb_mod, "_PROVIDERS", stub_map)
    monkeypatch.setattr(vdb_mod, "embed_text_openai", _fake_embed)


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


async def test_non_openai_embedding_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_provider(monkeypatch, [])
    node = _node(vectorDbEmbeddingProvider="cohere")
    with pytest.raises(NotImplementedError, match="OpenAI only"):
        await VectorDbExecutor(node).arun(initial_state())


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


async def test_missing_openai_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.config import get_settings

    monkeypatch.setenv("OPENAI_API_KEY", "")
    get_settings.cache_clear()
    with pytest.raises(VectorDbNodeError, match="OPENAI_API_KEY"):
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
