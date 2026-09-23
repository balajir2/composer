"""Tests for Milvus vector-db provider (Phase 6e)."""

import json
import logging
from typing import Any

import pytest
from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]

from src.vectordb.providers import milvus
from src.vectordb.providers.base import QueryConfig, VectorDbProviderError

_BASE = "https://my-milvus.zillizcloud.com"


def _config(**overrides: Any) -> QueryConfig:
    base: dict[str, Any] = {
        "endpoint": _BASE,
        "api_key": "test-key",
        "collection": "coll",
        "top_k": 3,
    }
    base.update(overrides)
    return QueryConfig(**base)


async def test_milvus_happy_path(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(
        method="POST",
        url=f"{_BASE}/v1/vector/search",
        json={
            "data": [
                {"id": "1", "distance": 0.9, "text": "hello", "category": "doc"},
            ]
        },
        status_code=200,
    )
    results = await milvus.query([0.1], _config())
    assert len(results) == 1
    assert results[0].id == "1"
    assert results[0].score == pytest.approx(0.9)  # pyright: ignore[reportUnknownMemberType]
    assert results[0].text == "hello"
    assert results[0].metadata == {"id": "1", "distance": 0.9, "text": "hello", "category": "doc"}


async def test_milvus_filter_dict_logs_warning_and_skips(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
    caplog: pytest.LogCaptureFixture,
) -> None:
    httpx_mock.add_response(
        method="POST",
        url=f"{_BASE}/v1/vector/search",
        json={"data": []},
        status_code=200,
    )
    with caplog.at_level(logging.WARNING):
        await milvus.query([0.0], _config(metadata_filter={"category": "doc"}))
    assert any("DSL expression" in rec.message for rec in caplog.records)

    req = httpx_mock.get_requests(method="POST", url=f"{_BASE}/v1/vector/search")[0]
    body = json.loads(req.content)
    assert body["filter"] == ""


async def test_milvus_http_error_raises(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(
        method="POST",
        url=f"{_BASE}/v1/vector/search",
        status_code=500,
        text="boom",
    )
    with pytest.raises(VectorDbProviderError, match="500"):
        await milvus.query([0.0], _config())
