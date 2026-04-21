"""Tests for Pinecone vector-db provider (Phase 6e)."""

from typing import Any

import pytest
from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]

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


async def test_pinecone_happy_path(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(
        method="POST",
        url=f"{_BASE}/query",
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


async def test_pinecone_sends_namespace_and_filter(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(
        method="POST",
        url=f"{_BASE}/query",
        json={"matches": []},
        status_code=200,
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


async def test_pinecone_include_vector(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(
        method="POST",
        url=f"{_BASE}/query",
        json={
            "matches": [{"id": "d", "score": 1.0, "metadata": {"text": "x"}, "values": [0.1, 0.2]}]
        },
        status_code=200,
    )
    results = await pinecone.query([0.0], _config(include_vector=True))
    assert results[0].vector == [0.1, 0.2]


async def test_pinecone_http_error_raises(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(
        method="POST",
        url=f"{_BASE}/query",
        status_code=500,
        text="pinecone down",
    )
    with pytest.raises(VectorDbProviderError, match="500"):
        await pinecone.query([0.0], _config())
