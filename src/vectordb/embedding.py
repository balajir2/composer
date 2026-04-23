"""OpenAI embeddings for vector-db queries (Phase 6e).

Only OpenAI is shipped; cohere / jina / pinecone-inference raise
NotImplementedError until a future phase needs them.
"""

from __future__ import annotations

from typing import Any

import httpx

OPENAI_EMBEDDINGS_URL = "https://api.openai.com/v1/embeddings"


class EmbeddingError(RuntimeError):
    """Raised when an embedding call fails."""


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

    payload: dict[str, Any] = {"input": text, "model": model}
    if dimension and model.startswith("text-embedding-3-"):
        payload["dimensions"] = dimension

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
        try:
            resp = await client.post(
                OPENAI_EMBEDDINGS_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        except httpx.HTTPError as exc:
            raise EmbeddingError(f"OpenAI embeddings request failed: {exc}") from exc

    if resp.status_code >= 400:
        raise EmbeddingError(f"OpenAI embeddings error {resp.status_code}: {resp.text}")
    body = resp.json()
    data = body.get("data")
    if not data or not isinstance(data, list):
        raise EmbeddingError(f"OpenAI embeddings response missing 'data' array: {body}")
    vector = data[0].get("embedding")
    if not isinstance(vector, list):
        raise EmbeddingError(f"OpenAI embeddings response missing 'embedding': {body}")
    return vector


__all__ = ["OPENAI_EMBEDDINGS_URL", "EmbeddingError", "embed_text_openai"]
