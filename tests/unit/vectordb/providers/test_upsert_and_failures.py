"""Behavioral tests for vector-db provider upserts and upstream failure modes
(timeouts, non-2xx, secret hygiene, id stability, auth headers) across all five
providers: chroma, milvus, pinecone, qdrant, weaviate."""

from __future__ import annotations

import contextlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from src.vectordb.providers import chroma, milvus, pinecone, qdrant, weaviate
from src.vectordb.providers.base import (
    QueryConfig,
    UpsertConfig,
    UpsertDocument,
    UpsertResult,
    VectorDbProviderError,
    VectorDbResult,
)

if TYPE_CHECKING:
    from pytest_httpx import HTTPXMock

_BASE = "https://vdb.example.com"
_SECRET = "vdb-super-secret-key"

UpsertFn = Callable[[list[UpsertDocument], UpsertConfig], Awaitable[UpsertResult]]
QueryFn = Callable[[list[float], QueryConfig], Awaitable[list[VectorDbResult]]]


@dataclass(frozen=True)
class _Provider:
    name: str
    upsert: UpsertFn
    query: QueryFn
    upsert_method: str
    upsert_path: str
    query_path: str
    auth_header: str
    auth_value: str


_PROVIDERS = [
    _Provider(
        "chroma",
        chroma.upsert,
        chroma.query,
        "POST",
        "/api/v1/collections/col/upsert",
        "/api/v1/collections/col/query",
        "authorization",
        f"Bearer {_SECRET}",
    ),
    _Provider(
        "milvus",
        milvus.upsert,
        milvus.query,
        "POST",
        "/v1/vector/insert",
        "/v1/vector/search",
        "authorization",
        f"Bearer {_SECRET}",
    ),
    _Provider(
        "pinecone",
        pinecone.upsert,
        pinecone.query,
        "POST",
        "/vectors/upsert",
        "/query",
        "api-key",
        _SECRET,
    ),
    _Provider(
        "qdrant",
        qdrant.upsert,
        qdrant.query,
        "PUT",
        "/collections/col/points",
        "/collections/col/points/search",
        "api-key",
        _SECRET,
    ),
    _Provider(
        "weaviate",
        weaviate.upsert,
        weaviate.query,
        "POST",
        "/v1/batch/objects",
        "/v1/graphql",
        "authorization",
        f"Bearer {_SECRET}",
    ),
]
_IDS = [p.name for p in _PROVIDERS]


def _approx(value: float) -> Any:
    return pytest.approx(value)  # pyright: ignore[reportUnknownMemberType]


def _uc(api_key: str | None = _SECRET, **kw: Any) -> UpsertConfig:
    return UpsertConfig(endpoint=_BASE + "/", api_key=api_key, collection="col", **kw)


def _qc(**kw: Any) -> QueryConfig:
    return QueryConfig(endpoint=_BASE, api_key=_SECRET, collection="col", top_k=3, **kw)


def _docs() -> list[UpsertDocument]:
    return [
        UpsertDocument(text="alpha", embedding=[0.1, 0.2], metadata={"src": "a"}),
        UpsertDocument(text="beta", embedding=[0.3, 0.4]),
    ]


def _ok_response(p: _Provider, n: int) -> Any:
    """A minimal success body each provider accepts for an upsert of n docs."""
    if p.name == "weaviate":
        return [{"result": {"status": "SUCCESS"}} for _ in range(n)]
    if p.name == "pinecone":
        return {"upsertedCount": n}
    if p.name == "milvus":
        return {"code": 0, "data": {"insertCount": n}}
    return {}


# ─── shared upsert contract ─────────────────────────────────────────────────


@pytest.mark.parametrize("p", _PROVIDERS, ids=_IDS)
async def test_upsert_empty_batch_makes_no_request(p: _Provider, httpx_mock: HTTPXMock) -> None:
    result = await p.upsert([], _uc())
    assert result == UpsertResult(inserted_count=0, ids=[])
    assert httpx_mock.get_requests() == []


@pytest.mark.parametrize("p", _PROVIDERS, ids=_IDS)
async def test_upsert_sends_expected_request_with_auth_header(
    p: _Provider, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(json=_ok_response(p, 2))
    result = await p.upsert(_docs(), _uc())
    req = httpx_mock.get_requests()[0]
    assert req.method == p.upsert_method
    assert req.url.path == p.upsert_path
    assert req.headers[p.auth_header] == p.auth_value
    assert result.inserted_count == 2
    assert len(result.ids) == 2


@pytest.mark.parametrize("p", _PROVIDERS, ids=_IDS)
async def test_upsert_omits_auth_header_when_no_api_key(
    p: _Provider, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(json=_ok_response(p, 2))
    await p.upsert(_docs(), _uc(api_key=None))
    headers = httpx_mock.get_requests()[0].headers
    assert p.auth_header not in headers


@pytest.mark.parametrize("p", _PROVIDERS, ids=_IDS)
async def test_upsert_ids_are_deterministic_for_identical_text(
    p: _Provider, httpx_mock: HTTPXMock
) -> None:
    """Re-ingesting the same chunk must produce the same id (idempotent overwrite)."""
    httpx_mock.add_response(json=_ok_response(p, 1))
    httpx_mock.add_response(json=_ok_response(p, 1))
    doc = [UpsertDocument(text="stable text", embedding=[1.0])]
    first = await p.upsert(doc, _uc())
    second = await p.upsert(doc, _uc())
    assert first.ids == second.ids
    assert first.ids[0] != ""


@pytest.mark.parametrize("p", _PROVIDERS, ids=_IDS)
async def test_upsert_caller_supplied_id_is_preserved(p: _Provider, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json=_ok_response(p, 1))
    doc = [UpsertDocument(text="t", embedding=[1.0], id="fixed-id")]
    result = await p.upsert(doc, _uc())
    assert result.ids == ["fixed-id"]


@pytest.mark.parametrize("p", _PROVIDERS, ids=_IDS)
async def test_upsert_non_2xx_raises_provider_error_without_leaking_api_key(
    p: _Provider, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(status_code=500, text="internal boom")
    with pytest.raises(VectorDbProviderError, match="500") as info:
        await p.upsert(_docs(), _uc())
    assert _SECRET not in str(info.value)


@pytest.mark.parametrize("p", _PROVIDERS, ids=_IDS)
async def test_upsert_network_timeout_is_wrapped_as_provider_error(
    p: _Provider, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_exception(httpx.ReadTimeout("slow upstream"))
    with pytest.raises(VectorDbProviderError, match="upsert request failed"):
        await p.upsert(_docs(), _uc())


@pytest.mark.parametrize("p", _PROVIDERS, ids=_IDS)
async def test_query_network_timeout_is_wrapped_as_provider_error(
    p: _Provider, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_exception(httpx.ConnectTimeout("no route"))
    with pytest.raises(VectorDbProviderError, match="query request failed"):
        await p.query([0.1], _qc())


@pytest.mark.parametrize("p", _PROVIDERS, ids=_IDS)
async def test_query_server_error_raises_provider_error(
    p: _Provider, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(status_code=503, text="unavailable")
    with pytest.raises(VectorDbProviderError, match="503"):
        await p.query([0.1], _qc())


@pytest.mark.parametrize("p", _PROVIDERS, ids=_IDS)
async def test_query_hits_expected_path_with_trimmed_endpoint(
    p: _Provider, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(json={})
    # an empty body may be reported as an error; only routing is asserted
    with contextlib.suppress(VectorDbProviderError):
        await p.query([0.1], _qc())
    assert httpx_mock.get_requests()[0].url.path == p.query_path


# ─── provider-specific upsert bodies ────────────────────────────────────────


def _body(httpx_mock: HTTPXMock) -> dict[str, Any]:
    return json.loads(httpx_mock.get_requests()[0].content)


async def test_pinecone_upsert_merges_text_into_metadata_and_sets_namespace(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(json={"upsertedCount": 2})
    await pinecone.upsert(_docs(), _uc(namespace="ns1", text_field="body"))
    body = _body(httpx_mock)
    assert body["namespace"] == "ns1"
    assert body["vectors"][0]["metadata"] == {"src": "a", "body": "alpha"}
    assert body["vectors"][0]["values"] == [0.1, 0.2]


async def test_pinecone_upsert_empty_text_without_id_gets_unique_ids(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(json={})
    docs = [UpsertDocument(text="", embedding=[1.0]), UpsertDocument(text="", embedding=[2.0])]
    result = await pinecone.upsert(docs, _uc())
    assert len(set(result.ids)) == 2
    assert result.inserted_count == 2  # falls back to our own count


async def test_chroma_upsert_uses_column_major_body_and_sentinel_metadata(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(json={})
    await chroma.upsert(_docs(), _uc())
    body = _body(httpx_mock)
    assert body["documents"] == ["alpha", "beta"]
    assert body["embeddings"] == [[0.1, 0.2], [0.3, 0.4]]
    assert body["metadatas"][0] == {"src": "a"}
    assert body["metadatas"][1] == {"_source": "composer"}  # Chroma rejects empty metadata
    assert len(body["ids"]) == 2


async def test_qdrant_upsert_waits_for_commit_and_generates_uuid_ids(
    httpx_mock: HTTPXMock,
) -> None:
    import uuid

    httpx_mock.add_response(json={})
    result = await qdrant.upsert(_docs(), _uc())
    req = httpx_mock.get_requests()[0]
    assert req.url.params["wait"] == "true"
    for point_id in result.ids:
        uuid.UUID(point_id)  # Qdrant requires UUID or uint ids
    point = json.loads(req.content)["points"][0]
    assert point["payload"] == {"src": "a", "text": "alpha"}


async def test_weaviate_upsert_pascal_cases_class_and_reports_only_successful_ids(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(
        json=[
            {"result": {"errors": {"error": [{"message": "bad"}]}}},
            {"result": {"status": "SUCCESS"}},
        ]
    )
    docs = [
        UpsertDocument(text="a", embedding=[1.0], id="id-a"),
        UpsertDocument(text="b", embedding=[2.0], id="id-b"),
    ]
    result = await weaviate.upsert(docs, _uc())
    assert result.ids == ["id-b"]
    assert result.inserted_count == 1
    assert _body(httpx_mock)["objects"][0]["class"] == "Col"


async def test_weaviate_upsert_non_list_response_trusts_submitted_count(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(json={"status": "ok"})
    result = await weaviate.upsert(_docs(), _uc())
    assert result.inserted_count == 2


async def test_milvus_upsert_hashes_text_to_stable_positive_int63_ids(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(json={"code": 0, "data": {"insertCount": 2}})
    result = await milvus.upsert(_docs(), _uc(text_field="chunk"))
    row = _body(httpx_mock)["data"][0]
    assert row["chunk"] == "alpha"
    assert row["src"] == "a"
    assert 0 < row["id"] < (1 << 63)
    assert result.ids[0] == str(row["id"])


async def test_milvus_upsert_prefers_server_generated_ids(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json={"code": 0, "data": {"insertCount": 2, "insertIds": [101, 102]}})
    result = await milvus.upsert(_docs(), _uc())
    assert result.ids == ["101", "102"]


async def test_milvus_metadata_cannot_overwrite_vector_or_text_fields(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(json={"code": 0, "data": {"insertCount": 1}})
    doc = UpsertDocument(text="real", embedding=[1.0], metadata={"vector": "evil", "text": "evil"})
    await milvus.upsert([doc], _uc())
    row = _body(httpx_mock)["data"][0]
    assert row["vector"] == [1.0]
    assert row["text"] == "real"


async def test_milvus_application_level_error_code_on_http_200_is_not_reported_as_success(
    httpx_mock: HTTPXMock,
) -> None:
    """Milvus/Zilliz REST returns HTTP 200 with a non-zero `code` on failure; an
    insert that was rejected must not be reported as inserted."""
    httpx_mock.add_response(status_code=200, json={"code": 1800, "message": "collection not found"})
    with pytest.raises(VectorDbProviderError, match="collection not found"):
        await milvus.upsert(_docs(), _uc())


async def test_milvus_query_error_code_on_http_200_raises(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=200, json={"code": 1100, "message": "bad filter"})
    with pytest.raises(VectorDbProviderError, match="bad filter"):
        await milvus.query([0.1], _qc())


# ─── query parsing edge cases ───────────────────────────────────────────────


async def test_chroma_query_prefers_configured_text_field_and_converts_distance(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(
        json={
            "ids": [["d1"]],
            "distances": [[0.25]],
            "metadatas": [[{"body": "from-meta"}]],
            "documents": [["from-doc"]],
            "embeddings": [[[0.5, 0.5]]],
        }
    )
    out = await chroma.query([0.1], _qc(text_field="body", include_vector=True))
    assert out[0].text == "from-meta"
    assert out[0].score == _approx(0.75)
    assert out[0].vector == [0.5, 0.5]
    assert "embeddings" in _body(httpx_mock)["include"]


async def test_chroma_query_include_metadata_false_strips_metadata(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        json={
            "ids": [["d1"]],
            "distances": [[0.0]],
            "metadatas": [[{"k": "v"}]],
            "documents": [["t"]],
        }
    )
    out = await chroma.query([0.1], _qc(include_metadata=False))
    assert out[0].metadata == {}
    assert out[0].text == "t"


async def test_chroma_query_sends_where_filter(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json={"ids": [[]]})
    assert await chroma.query([0.1], _qc(metadata_filter={"k": "v"})) == []
    assert _body(httpx_mock)["where"] == {"k": "v"}


async def test_qdrant_query_falls_back_to_content_fields_and_includes_vector(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(
        json={
            "result": [
                {"id": 1, "score": 0.9, "payload": {"page_content": "pc"}, "vector": [1.0]},
                {"id": 2, "score": 0.1, "payload": None},
            ]
        }
    )
    out = await qdrant.query([0.1], _qc(include_vector=True))
    assert [r.text for r in out] == ["pc", ""]
    assert out[0].vector == [1.0]
    assert _body(httpx_mock)["with_vector"] is True


async def test_pinecone_query_tolerates_non_dict_metadata(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json={"matches": [{"id": "x", "score": 1, "metadata": "oops"}]})
    out = await pinecone.query([0.1], _qc())
    assert out[0].metadata == {}
    assert out[0].text == ""


async def test_weaviate_query_builds_where_clause_and_vector_selection(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(
        json={
            "data": {
                "Get": {
                    "Col": [
                        {
                            "_additional": {"id": "u1", "distance": 0.2, "vector": [1.0]},
                            "text": "hello",
                        }
                    ]
                }
            }
        }
    )
    out = await weaviate.query(
        [0.1], _qc(metadata_filter={"path": ["a"], "operator": "Equal"}, include_vector=True)
    )
    gql = _body(httpx_mock)["query"]
    assert "where:" in gql
    assert "id distance vector" in gql
    assert out[0].score == _approx(0.8)
    assert out[0].vector == [1.0]
