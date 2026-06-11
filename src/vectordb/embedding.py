"""Embedding providers for vector-db queries and upserts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

OPENAI_EMBEDDINGS_URL = "https://api.openai.com/v1/embeddings"
OPENAI_COMPATIBLE_BASE_URLS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "dashscope": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    "siliconflow": "https://api.siliconflow.cn/v1",
    "zhipu": "https://open.bigmodel.cn/api/paas/v4",
    "jina": "https://api.jina.ai/v1",
    "voyage": "https://api.voyageai.com/v1",
}
COHERE_EMBEDDINGS_URL = "https://api.cohere.com/v2/embed"
PINECONE_INFERENCE_EMBEDDINGS_URL = "https://api.pinecone.io/embed"


class EmbeddingError(RuntimeError):
    """Raised when an embedding call fails."""


@dataclass(frozen=True)
class EmbeddingConfig:
    provider: str
    model: str
    api_key: str
    dimension: int | None = None
    base_url: str | None = None


def _format_openai_error(status_code: int, response_text: str) -> str:
    """Return a concise error message for OpenAI-compatible failures."""
    try:
        body = json.loads(response_text)
    except json.JSONDecodeError:
        return f"embeddings error {status_code}: {response_text}"

    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return f"embeddings error {status_code}: {response_text}"

    code = error.get("code")
    message = error.get("message")
    if code == "insufficient_quota":
        return (
            "embeddings error 429: insufficient quota. The API key is valid, "
            "but the associated provider project/account has no available billing "
            "quota or credits for embeddings. Check provider billing, project limits, "
            "and the project attached to the embedding API key."
        )
    if isinstance(message, str) and message:
        return f"embeddings error {status_code}: {message}"
    return f"embeddings error {status_code}: {response_text}"


def _normalise_base_url(base_url: str) -> str:
    return base_url.rstrip("/")


def _embedding_from_openai_compatible_body(body: Any) -> list[float]:
    if not isinstance(body, dict):
        raise EmbeddingError(f"embeddings response was not an object: {body}")
    data = body.get("data")
    if not data or not isinstance(data, list):
        raise EmbeddingError(f"embeddings response missing 'data' array: {body}")
    first = data[0]
    if not isinstance(first, dict):
        raise EmbeddingError(f"embeddings response item was not an object: {body}")
    vector = first.get("embedding")
    if not isinstance(vector, list):
        raise EmbeddingError(f"embeddings response missing 'embedding': {body}")
    return vector


async def _post_json(
    *,
    url: str,
    api_key: str,
    payload: dict[str, Any],
    provider_label: str,
) -> Any:
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
        try:
            resp = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        except httpx.HTTPError as exc:
            raise EmbeddingError(f"{provider_label} embeddings request failed: {exc}") from exc

    if resp.status_code >= 400:
        raise EmbeddingError(f"{provider_label} {_format_openai_error(resp.status_code, resp.text)}")
    return resp.json()


async def embed_text_openai_compatible(
    text: str,
    *,
    config: EmbeddingConfig,
) -> list[float]:
    if not config.api_key:
        raise EmbeddingError(f"{config.provider} API key is required for embeddings")

    base_url = config.base_url or OPENAI_COMPATIBLE_BASE_URLS.get(config.provider)
    if not base_url:
        raise EmbeddingError(
            f"{config.provider} embeddings require vectorDbEmbeddingBaseUrl"
        )
    payload: dict[str, Any] = {"input": text, "model": config.model}
    if config.dimension:
        payload["dimensions"] = config.dimension

    body = await _post_json(
        url=f"{_normalise_base_url(base_url)}/embeddings",
        api_key=config.api_key,
        payload=payload,
        provider_label=config.provider,
    )
    return _embedding_from_openai_compatible_body(body)


async def embed_text_cohere(text: str, *, config: EmbeddingConfig) -> list[float]:
    if not config.api_key:
        raise EmbeddingError("cohere API key is required for embeddings")

    payload: dict[str, Any] = {
        "texts": [text],
        "model": config.model,
        "input_type": "search_query",
        "embedding_types": ["float"],
    }
    body = await _post_json(
        url=config.base_url or COHERE_EMBEDDINGS_URL,
        api_key=config.api_key,
        payload=payload,
        provider_label="cohere",
    )
    embeddings = body.get("embeddings") if isinstance(body, dict) else None
    if isinstance(embeddings, dict):
        vectors = embeddings.get("float")
        if isinstance(vectors, list) and vectors and isinstance(vectors[0], list):
            return vectors[0]
    if isinstance(embeddings, list) and embeddings and isinstance(embeddings[0], list):
        return embeddings[0]
    raise EmbeddingError(f"cohere embeddings response missing vector: {body}")


async def embed_text_pinecone_inference(text: str, *, config: EmbeddingConfig) -> list[float]:
    if not config.api_key:
        raise EmbeddingError("pinecone-inference API key is required for embeddings")

    payload: dict[str, Any] = {
        "model": config.model,
        "inputs": [{"text": text}],
        "parameters": {"input_type": "query"},
    }
    if config.dimension:
        payload["parameters"]["dimension"] = config.dimension
    body = await _post_json(
        url=config.base_url or PINECONE_INFERENCE_EMBEDDINGS_URL,
        api_key=config.api_key,
        payload=payload,
        provider_label="pinecone-inference",
    )
    data = body.get("data") if isinstance(body, dict) else None
    if isinstance(data, list) and data and isinstance(data[0], dict):
        values = data[0].get("values") or data[0].get("embedding")
        if isinstance(values, list):
            return values
    raise EmbeddingError(f"pinecone-inference embeddings response missing vector: {body}")


async def embed_text(
    text: str,
    *,
    config: EmbeddingConfig,
) -> list[float]:
    provider = config.provider
    if provider in OPENAI_COMPATIBLE_BASE_URLS or provider == "custom-openai-compatible":
        return await embed_text_openai_compatible(text, config=config)
    if provider == "cohere":
        return await embed_text_cohere(text, config=config)
    if provider == "pinecone-inference":
        return await embed_text_pinecone_inference(text, config=config)
    raise EmbeddingError(f"unknown embedding provider {provider!r}")


async def embed_text_openai(
    text: str,
    *,
    model: str,
    api_key: str,
    dimension: int | None = None,
) -> list[float]:
    """Embed `text` via OpenAI's embeddings API.

    For `text-embedding-3-*` models, `dimension` may be passed to request a
    truncated vector.  For ada-002 or other models, dimension is omitted
    (OpenAI returns the model's native size).
    """
    if not api_key:
        raise EmbeddingError("OpenAI API key is required for embeddings")
    return await embed_text(
        text,
        config=EmbeddingConfig(
            provider="openai",
            model=model,
            api_key=api_key,
            dimension=dimension if model.startswith("text-embedding-3-") else None,
        ),
    )


__all__ = [
    "COHERE_EMBEDDINGS_URL",
    "OPENAI_COMPATIBLE_BASE_URLS",
    "OPENAI_EMBEDDINGS_URL",
    "PINECONE_INFERENCE_EMBEDDINGS_URL",
    "EmbeddingConfig",
    "EmbeddingError",
    "embed_text",
    "embed_text_cohere",
    "embed_text_openai",
    "embed_text_openai_compatible",
    "embed_text_pinecone_inference",
]
