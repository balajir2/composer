"""Per-model verify probe.

Every provider's `/models` listing returns models the account *can see* —
not necessarily models the account *can invoke*.  Google ships
`gemini-2.0-flash` in the catalogue but returns 404 NOT_FOUND when a
non-grandfathered key tries to call it; OpenAI keeps deprecated aliases
in the listing for weeks after they stop accepting traffic.

Verify hits the model with the smallest possible call (free where the
provider offers it, otherwise a 1-token completion) and stamps the row
with `ok` / `unavailable` / error message.

Auth-vs-model distinction: 401/403 is a *key* problem (don't penalise
the model); 404 / model-not-found is a *model* problem.  We only mark
`unavailable` on the latter.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Literal

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT_SEC = 10.0


VerifyStatus = Literal["ok", "unavailable", "auth_error", "error"]


@dataclass(frozen=True)
class ModelVerifyResult:
    status: VerifyStatus
    http_status: int | None
    message: str


async def _post(url: str, headers: dict[str, str], json: dict[str, object]) -> httpx.Response:
    async with httpx.AsyncClient(timeout=httpx.Timeout(_TIMEOUT_SEC, connect=5.0)) as client:
        return await client.post(url, headers=headers, json=json)


def _classify(http_status: int, body: str) -> VerifyStatus:
    """Bucket an HTTP response into our four status buckets.

    The body is lower-cased for keyword matching; providers vary in
    structure but consistently use words like "model" / "not found" /
    "deprecated" in 4xx error messages when the *model* (not key) is
    the problem.
    """
    body_l = body.lower()
    if http_status == 200:
        return "ok"
    if http_status in (401, 403):
        return "auth_error"
    if http_status == 404:
        return "unavailable"
    if 400 <= http_status < 500:
        # 400/422 with a model-shaped error → unavailable; otherwise generic error.
        if any(
            kw in body_l
            for kw in (
                "model",
                "not found",
                "does not exist",
                "deprecated",
                "decommissioned",
                "unsupported",
                "retired",
            )
        ):
            return "unavailable"
        return "error"
    return "error"


async def _verify_anthropic(model_id: str, key: str) -> ModelVerifyResult:
    resp = await _post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": model_id,
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    status = _classify(resp.status_code, resp.text)
    msg = (
        f"Anthropic {model_id!r} OK."
        if status == "ok"
        else f"Anthropic HTTP {resp.status_code}: {resp.text[:240]}"
    )
    return ModelVerifyResult(status=status, http_status=resp.status_code, message=msg)


def _is_openai_reasoning_model(model_id: str) -> bool:
    """OpenAI reasoning models (o1*, o3*, o4*) reject `max_tokens` and
    require `max_completion_tokens` — chat models accept either but
    `max_tokens` is deprecated.  Pattern-match the prefix instead of
    maintaining a hard-coded list so newer reasoning families pick up
    the right shape automatically."""
    if not model_id:
        return False
    # Prefix shape: a single 'o' followed by a digit (o1, o3-mini, etc).
    return len(model_id) >= 2 and model_id[0] == "o" and model_id[1].isdigit()


def openai_chat_probe_body(model_id: str) -> dict[str, object]:
    body: dict[str, object] = {
        "model": model_id,
        "messages": [{"role": "user", "content": "hi"}],
    }
    if _is_openai_reasoning_model(model_id):
        body["max_completion_tokens"] = 1
    else:
        body["max_tokens"] = 1
    return body


async def _verify_openai(model_id: str, key: str) -> ModelVerifyResult:
    # Use chat/completions with the smallest possible call — every
    # chat-capable model accepts this shape, and retired ones return
    # 404 / model_not_found before we burn tokens.  Reasoning models
    # need `max_completion_tokens` instead of the deprecated `max_tokens`,
    # so dispatch by model name.
    resp = await _post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "content-type": "application/json"},
        json=openai_chat_probe_body(model_id),
    )
    status = _classify(resp.status_code, resp.text)
    msg = (
        f"OpenAI {model_id!r} OK."
        if status == "ok"
        else f"OpenAI HTTP {resp.status_code}: {resp.text[:240]}"
    )
    return ModelVerifyResult(status=status, http_status=resp.status_code, message=msg)


async def _verify_google(model_id: str, key: str) -> ModelVerifyResult:
    # IMPORTANT: must use generateContent, not countTokens.  Google
    # gates them separately: countTokens is metadata-only and succeeds
    # on models like gemini-2.0-flash that have been retired for new
    # keys, while generateContent returns the real "no longer available
    # to new users" 404.  Verify must mirror the call the workflow
    # actually makes — which is generateContent — or it lies.
    #
    # Strip a "models/" prefix if present (REST API expects bare id in URL).
    bare = model_id.removeprefix("models/")
    resp = await _post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{bare}:generateContent?key={key}",
        headers={"content-type": "application/json"},
        json={
            "contents": [{"parts": [{"text": "hi"}]}],
            "generationConfig": {"maxOutputTokens": 1},
        },
    )
    status = _classify(resp.status_code, resp.text)
    msg = (
        f"Google {model_id!r} OK."
        if status == "ok"
        else f"Google HTTP {resp.status_code}: {resp.text[:240]}"
    )
    return ModelVerifyResult(status=status, http_status=resp.status_code, message=msg)


async def _verify_groq(model_id: str, key: str) -> ModelVerifyResult:
    resp = await _post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "content-type": "application/json"},
        json={
            "model": model_id,
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    status = _classify(resp.status_code, resp.text)
    msg = (
        f"Groq {model_id!r} OK."
        if status == "ok"
        else f"Groq HTTP {resp.status_code}: {resp.text[:240]}"
    )
    return ModelVerifyResult(status=status, http_status=resp.status_code, message=msg)


_VERIFIERS = {
    "anthropic": _verify_anthropic,
    "openai": _verify_openai,
    "google": _verify_google,
    "groq": _verify_groq,
}


async def run_model_verify(provider: str, model_id: str, key: str) -> ModelVerifyResult:
    verifier = _VERIFIERS.get(provider)
    if verifier is None:
        return ModelVerifyResult(
            status="error",
            http_status=None,
            message=f"no verifier for provider {provider!r}",
        )
    try:
        return await asyncio.wait_for(verifier(model_id, key), timeout=_TIMEOUT_SEC + 2.0)
    except TimeoutError:
        return ModelVerifyResult(
            status="error",
            http_status=None,
            message=f"timed out after {_TIMEOUT_SEC:.0f}s",
        )
    except httpx.HTTPError as exc:
        return ModelVerifyResult(
            status="error",
            http_status=None,
            message=f"network error: {exc}",
        )
    except Exception as exc:
        logger.exception("admin-llm-models verify failed for %s/%s", provider, model_id)
        return ModelVerifyResult(
            status="error",
            http_status=None,
            message=f"{type(exc).__name__}: {exc}",
        )


__all__ = ["ModelVerifyResult", "VerifyStatus", "run_model_verify"]
