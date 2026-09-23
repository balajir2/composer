"""Tests for Weaviate vector-db provider (Phase 6e)."""

from typing import Any

import pytest
from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]

from src.vectordb.providers import weaviate
from src.vectordb.providers.base import (
    QueryConfig,
    UpsertConfig,
    UpsertDocument,
    VectorDbProviderError,
)

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


def _upsert_config(**overrides: Any) -> UpsertConfig:
    base: dict[str, Any] = {
        "endpoint": _BASE,
        "api_key": None,
        "collection": "docs",
    }
    base.update(overrides)
    return UpsertConfig(**base)


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
    assert results[0].score == pytest.approx(0.9)  # pyright: ignore[reportUnknownMemberType]


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


async def test_weaviate_upsert_all_success(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    docs = [
        UpsertDocument(text="a", embedding=[0.1], id="doc-a"),
        UpsertDocument(text="b", embedding=[0.2], id="doc-b"),
        UpsertDocument(text="c", embedding=[0.3], id="doc-c"),
    ]
    httpx_mock.add_response(
        method="POST",
        url=f"{_BASE}/v1/batch/objects",
        json=[
            {"id": "doc-a", "result": {"status": "SUCCESS"}},
            {"id": "doc-b", "result": {"status": "SUCCESS"}},
            {"id": "doc-c", "result": {"status": "SUCCESS"}},
        ],
        status_code=200,
    )
    result = await weaviate.upsert(docs, _upsert_config())
    assert result.inserted_count == 3
    assert result.ids == ["doc-a", "doc-b", "doc-c"]


async def test_weaviate_upsert_partial_failure_matches_ids_by_position(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    """Regression test: a non-tail partial failure must not corrupt which
    ids are reported as successful.

    Weaviate's batch endpoint returns one result per submitted object, in
    the same order as the request, each carrying its own success/failure
    status. The bug: the old code counted successes but then returned
    `out_ids[:successful]` — the *first* N ids by position — rather than
    the ids of the objects that actually succeeded. With the middle
    object (doc-b) failing here, the old code would have returned
    ["doc-a", "doc-c"[:2]] i.e. ["doc-a", "doc-b"] (wrong: doc-b failed,
    doc-c actually succeeded but is silently dropped from the reported
    ids). The fix must report exactly ["doc-a", "doc-c"].
    """
    docs = [
        UpsertDocument(text="a", embedding=[0.1], id="doc-a"),
        UpsertDocument(text="b", embedding=[0.2], id="doc-b"),
        UpsertDocument(text="c", embedding=[0.3], id="doc-c"),
    ]
    httpx_mock.add_response(
        method="POST",
        url=f"{_BASE}/v1/batch/objects",
        json=[
            {"id": "doc-a", "result": {"status": "SUCCESS"}},
            {"id": "doc-b", "result": {"errors": {"error": [{"message": "boom"}]}}},
            {"id": "doc-c", "result": {"status": "SUCCESS"}},
        ],
        status_code=200,
    )
    result = await weaviate.upsert(docs, _upsert_config())
    assert result.inserted_count == 2
    assert result.ids == ["doc-a", "doc-c"]
