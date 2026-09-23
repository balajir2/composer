"""Tests for Chroma vector-db provider (Phase 6e)."""

from typing import Any

import pytest
from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]

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


async def test_chroma_happy_path(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(
        method="POST",
        url=f"{_BASE}/api/v1/collections/my-collection/query",
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
    assert results[0].score == pytest.approx(0.9)  # pyright: ignore[reportUnknownMemberType]
    assert results[1].score == pytest.approx(0.7)  # pyright: ignore[reportUnknownMemberType]
    assert results[0].text == "hello"
    assert results[0].metadata == {"source": "x"}


async def test_chroma_empty_result(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(
        method="POST",
        url=f"{_BASE}/api/v1/collections/my-collection/query",
        json={"ids": [[]]},
        status_code=200,
    )
    results = await chroma.query([0.0], _config())
    assert results == []


async def test_chroma_http_error_raises(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(
        method="POST",
        url=f"{_BASE}/api/v1/collections/my-collection/query",
        status_code=400,
        text="bad request",
    )
    with pytest.raises(VectorDbProviderError, match="400"):
        await chroma.query([0.0], _config())
