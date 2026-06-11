"""Tests for OpenAI embeddings utility (vector-db, Phase 6e)."""

import pytest
from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]

from src.vectordb.embedding import (
    COHERE_EMBEDDINGS_URL,
    OPENAI_COMPATIBLE_BASE_URLS,
    OPENAI_EMBEDDINGS_URL,
    PINECONE_INFERENCE_EMBEDDINGS_URL,
    EmbeddingConfig,
    EmbeddingError,
    embed_text,
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


async def test_embed_text_openai_insufficient_quota_has_actionable_message(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    httpx_mock.add_response(
        method="POST",
        url=OPENAI_EMBEDDINGS_URL,
        status_code=429,
        json={
            "error": {
                "message": "You exceeded your current quota",
                "type": "insufficient_quota",
                "param": None,
                "code": "insufficient_quota",
            }
        },
    )
    with pytest.raises(EmbeddingError) as exc_info:
        await embed_text_openai("x", model="text-embedding-3-small", api_key="k")

    msg = str(exc_info.value)
    assert "insufficient quota" in msg
    assert "API key is valid" in msg
    assert "embedding API key" in msg


async def test_embed_text_openai_json_error_uses_message(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    httpx_mock.add_response(
        method="POST",
        url=OPENAI_EMBEDDINGS_URL,
        status_code=400,
        json={"error": {"message": "bad embedding model"}},
    )
    with pytest.raises(EmbeddingError, match="bad embedding model"):
        await embed_text_openai("x", model="not-real", api_key="k")


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


async def test_embed_text_dashscope_uses_openai_compatible_endpoint(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    url = f"{OPENAI_COMPATIBLE_BASE_URLS['dashscope']}/embeddings"
    httpx_mock.add_response(
        method="POST",
        url=url,
        json={"data": [{"embedding": [0.4, 0.5]}]},
        status_code=200,
    )
    vector = await embed_text(
        "hello",
        config=EmbeddingConfig(
            provider="dashscope",
            model="text-embedding-v4",
            api_key="dashscope-key",
            dimension=1536,
        ),
    )

    assert vector == [0.4, 0.5]

    import json

    req = httpx_mock.get_requests(method="POST", url=url)[0]
    body = json.loads(req.content)
    assert body["model"] == "text-embedding-v4"
    assert body["dimensions"] == 1536


async def test_embed_text_custom_openai_compatible_requires_base_url() -> None:
    with pytest.raises(EmbeddingError, match="vectorDbEmbeddingBaseUrl"):
        await embed_text(
            "hello",
            config=EmbeddingConfig(
                provider="custom-openai-compatible",
                model="embed",
                api_key="key",
            ),
        )


async def test_embed_text_cohere_parses_float_embedding(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    httpx_mock.add_response(
        method="POST",
        url=COHERE_EMBEDDINGS_URL,
        json={"embeddings": {"float": [[0.1, 0.2, 0.3]]}},
        status_code=200,
    )
    vector = await embed_text(
        "hello",
        config=EmbeddingConfig(
            provider="cohere",
            model="embed-v4.0",
            api_key="cohere-key",
        ),
    )
    assert vector == [0.1, 0.2, 0.3]


async def test_embed_text_pinecone_inference_parses_values(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    httpx_mock.add_response(
        method="POST",
        url=PINECONE_INFERENCE_EMBEDDINGS_URL,
        json={"data": [{"values": [0.7, 0.8]}]},
        status_code=200,
    )
    vector = await embed_text(
        "hello",
        config=EmbeddingConfig(
            provider="pinecone-inference",
            model="multilingual-e5-large",
            api_key="pinecone-key",
            dimension=1024,
        ),
    )
    assert vector == [0.7, 0.8]
