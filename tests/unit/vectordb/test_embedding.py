"""Tests for OpenAI embeddings utility (vector-db, Phase 6e)."""

import pytest
from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]

from src.vectordb.embedding import (
    OPENAI_EMBEDDINGS_URL,
    EmbeddingError,
    embed_text_openai,
)


async def test_embed_text_openai_happy_path(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(
        method="POST",
        url=OPENAI_EMBEDDINGS_URL,
        json={"data": [{"embedding": [0.1, 0.2, 0.3]}]},
        status_code=200,
    )
    vector = await embed_text_openai(
        "hello", model="text-embedding-3-small", api_key="key", dimension=3
    )
    assert vector == [0.1, 0.2, 0.3]


async def test_embed_text_openai_sends_dimensions_for_v3_models(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    httpx_mock.add_response(
        method="POST",
        url=OPENAI_EMBEDDINGS_URL,
        json={"data": [{"embedding": [0.0]}]},
        status_code=200,
    )
    await embed_text_openai("x", model="text-embedding-3-small", api_key="k", dimension=1536)

    import json

    req = httpx_mock.get_requests(method="POST", url=OPENAI_EMBEDDINGS_URL)[0]
    body = json.loads(req.content)
    assert body["dimensions"] == 1536
    assert body["model"] == "text-embedding-3-small"
    assert body["input"] == "x"


async def test_embed_text_openai_omits_dimensions_for_ada(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(
        method="POST",
        url=OPENAI_EMBEDDINGS_URL,
        json={"data": [{"embedding": [0.0]}]},
        status_code=200,
    )
    await embed_text_openai("x", model="text-embedding-ada-002", api_key="k", dimension=1536)

    import json

    req = httpx_mock.get_requests(method="POST", url=OPENAI_EMBEDDINGS_URL)[0]
    body = json.loads(req.content)
    assert "dimensions" not in body


async def test_embed_text_openai_401_raises(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(
        method="POST",
        url=OPENAI_EMBEDDINGS_URL,
        status_code=401,
        text="bad key",
    )
    with pytest.raises(EmbeddingError, match="401"):
        await embed_text_openai("x", model="text-embedding-3-small", api_key="k")


async def test_embed_text_openai_malformed_response_raises(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(
        method="POST",
        url=OPENAI_EMBEDDINGS_URL,
        json={"nope": "nothing"},
        status_code=200,
    )
    with pytest.raises(EmbeddingError, match="missing 'data'"):
        await embed_text_openai("x", model="text-embedding-3-small", api_key="k")


async def test_embed_text_openai_missing_key_raises() -> None:
    with pytest.raises(EmbeddingError, match="API key is required"):
        await embed_text_openai("x", model="text-embedding-3-small", api_key="")
