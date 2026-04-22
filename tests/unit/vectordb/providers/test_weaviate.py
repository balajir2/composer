"""Tests for Weaviate vector-db provider (Phase 6e)."""

from typing import Any

import pytest
from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]

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


async def test_weaviate_happy_path(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(
        method="POST",
        url=f"{_BASE}/v1/graphql",
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


async def test_weaviate_graphql_error_raises(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(
        method="POST",
        url=f"{_BASE}/v1/graphql",
        json={"errors": [{"message": "class not found"}]},
        status_code=200,
    )
    with pytest.raises(VectorDbProviderError, match="class not found"):
        await weaviate.query([0.0], _config())


async def test_weaviate_http_error_raises(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(
        method="POST",
        url=f"{_BASE}/v1/graphql",
        status_code=401,
        text="auth required",
    )
    with pytest.raises(VectorDbProviderError, match="401"):
        await weaviate.query([0.0], _config())
