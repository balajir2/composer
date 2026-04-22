"""Tests for Qdrant vector-db provider (Phase 6e)."""

from typing import Any

import pytest
from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]

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


async def test_qdrant_happy_path(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(
        method="POST",
        url=f"{_BASE}/collections/docs/points/search",
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


async def test_qdrant_filter_passed(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(
        method="POST",
        url=f"{_BASE}/collections/docs/points/search",
        json={"result": []},
        status_code=200,
    )
    await qdrant.query(
        [0.0],
        _config(metadata_filter={"must": [{"key": "type", "match": {"value": "doc"}}]}),
    )

    import json

    req = httpx_mock.get_requests(method="POST", url=f"{_BASE}/collections/docs/points/search")[0]
    body = json.loads(req.content)
    assert "filter" in body


async def test_qdrant_http_error_raises(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(
        method="POST",
        url=f"{_BASE}/collections/docs/points/search",
        status_code=404,
        text="collection not found",
    )
    with pytest.raises(VectorDbProviderError, match="404"):
        await qdrant.query([0.0], _config())
