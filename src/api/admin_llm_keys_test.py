"""Provider-specific ping to verify an LLM / tool API key is live.

Decrypts the DB-stored key and runs a minimal authenticated call
against the upstream service, returning `{ok, status, message}`.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT_SEC = 8.0


@dataclass(frozen=True)
class KeyTestResult:
    ok: bool
    status: int | None
    message: str


async def _get(url: str, headers: dict[str, str]) -> httpx.Response:
    async with httpx.AsyncClient(timeout=httpx.Timeout(_TIMEOUT_SEC, connect=5.0)) as client:
        return await client.get(url, headers=headers)


async def _post(url: str, headers: dict[str, str], json: dict[str, object]) -> httpx.Response:
    async with httpx.AsyncClient(timeout=httpx.Timeout(_TIMEOUT_SEC, connect=5.0)) as client:
        return await client.post(url, headers=headers, json=json)


async def _test_anthropic(key: str) -> KeyTestResult:
    # Cheapest auth check: 1-token completion.  Bad keys → 401.
    resp = await _post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    if resp.status_code == 200:
        return KeyTestResult(ok=True, status=200, message="Anthropic key valid.")
    return KeyTestResult(
        ok=False,
        status=resp.status_code,
        message=f"Anthropic HTTP {resp.status_code}: {resp.text[:200]}",
    )


async def _test_openai(key: str) -> KeyTestResult:
    resp = await _get(
        "https://api.openai.com/v1/models",
        headers={"Authorization": f"Bearer {key}"},
    )
    if resp.status_code == 200:
        return KeyTestResult(ok=True, status=200, message="OpenAI key valid.")
    return KeyTestResult(
        ok=False,
        status=resp.status_code,
        message=f"OpenAI HTTP {resp.status_code}: {resp.text[:200]}",
    )


async def _test_google(key: str) -> KeyTestResult:
    # Gemini: list models with API key as query param.
    resp = await _get(
        f"https://generativelanguage.googleapis.com/v1beta/models?key={key}",
        headers={},
    )
    if resp.status_code == 200:
        return KeyTestResult(ok=True, status=200, message="Google (Gemini) key valid.")
    return KeyTestResult(
        ok=False,
        status=resp.status_code,
        message=f"Google HTTP {resp.status_code}: {resp.text[:200]}",
    )


async def _test_groq(key: str) -> KeyTestResult:
    resp = await _get(
        "https://api.groq.com/openai/v1/models",
        headers={"Authorization": f"Bearer {key}"},
    )
    if resp.status_code == 200:
        return KeyTestResult(ok=True, status=200, message="Groq key valid.")
    return KeyTestResult(
        ok=False,
        status=resp.status_code,
        message=f"Groq HTTP {resp.status_code}: {resp.text[:200]}",
    )


async def _test_langsmith(key: str) -> KeyTestResult:
    resp = await _get(
        "https://api.smith.langchain.com/api/v1/runs?limit=1",
        headers={"x-api-key": key},
    )
    if resp.status_code in (200, 204):
        return KeyTestResult(ok=True, status=resp.status_code, message="LangSmith key valid.")
    return KeyTestResult(
        ok=False,
        status=resp.status_code,
        message=f"LangSmith HTTP {resp.status_code}: {resp.text[:200]}",
    )


async def _test_tavily(key: str) -> KeyTestResult:
    resp = await _post(
        "https://api.tavily.com/search",
        headers={},
        json={"api_key": key, "query": "ping", "max_results": 1, "search_depth": "basic"},
    )
    if resp.status_code == 200:
        return KeyTestResult(ok=True, status=200, message="Tavily key valid.")
    return KeyTestResult(
        ok=False,
        status=resp.status_code,
        message=f"Tavily HTTP {resp.status_code}: {resp.text[:200]}",
    )


async def _test_firecrawl(key: str) -> KeyTestResult:
    resp = await _post(
        "https://api.firecrawl.dev/v1/scrape",
        headers={"Authorization": f"Bearer {key}", "content-type": "application/json"},
        json={"url": "https://example.com"},
    )
    if resp.status_code == 200:
        return KeyTestResult(ok=True, status=200, message="Firecrawl key valid.")
    return KeyTestResult(
        ok=False,
        status=resp.status_code,
        message=f"Firecrawl HTTP {resp.status_code}: {resp.text[:200]}",
    )


async def _test_serper(key: str) -> KeyTestResult:
    resp = await _post(
        "https://google.serper.dev/search",
        headers={"X-API-KEY": key, "content-type": "application/json"},
        json={"q": "ping"},
    )
    if resp.status_code == 200:
        return KeyTestResult(ok=True, status=200, message="Serper key valid.")
    return KeyTestResult(
        ok=False,
        status=resp.status_code,
        message=f"Serper HTTP {resp.status_code}: {resp.text[:200]}",
    )


async def _test_browserless(key: str) -> KeyTestResult:
    # Browserless /content returns the fetched HTML on 200, auth error on 401.
    resp = await _post(
        f"https://chrome.browserless.io/content?token={key}",
        headers={"content-type": "application/json"},
        json={"url": "https://example.com"},
    )
    if resp.status_code == 200:
        return KeyTestResult(ok=True, status=200, message="Browserless key valid.")
    return KeyTestResult(
        ok=False,
        status=resp.status_code,
        message=f"Browserless HTTP {resp.status_code}: {resp.text[:200]}",
    )


async def _test_gamma(key: str) -> KeyTestResult:
    # Gamma has no cheap auth-only endpoint; this hits the generations list
    # which returns 200 for valid keys and 401 for invalid.
    resp = await _get(
        "https://api.gamma.app/public/v1/generations?limit=1",
        headers={"X-API-KEY": key},
    )
    if resp.status_code in (200, 204):
        return KeyTestResult(ok=True, status=resp.status_code, message="Gamma key valid.")
    return KeyTestResult(
        ok=False,
        status=resp.status_code,
        message=f"Gamma HTTP {resp.status_code}: {resp.text[:200]}",
    )


_TESTERS = {
    "anthropic": _test_anthropic,
    "openai": _test_openai,
    "google": _test_google,
    "groq": _test_groq,
    "langsmith": _test_langsmith,
    "tavily": _test_tavily,
    "firecrawl": _test_firecrawl,
    "serper": _test_serper,
    "browserless": _test_browserless,
    "gamma": _test_gamma,
}


async def run_key_test(provider: str, key: str) -> KeyTestResult:
    tester = _TESTERS.get(provider)
    if tester is None:
        return KeyTestResult(ok=False, status=None, message=f"no tester for provider {provider!r}")
    try:
        return await asyncio.wait_for(tester(key), timeout=_TIMEOUT_SEC + 2.0)
    except TimeoutError:
        return KeyTestResult(ok=False, status=None, message=f"timed out after {_TIMEOUT_SEC:.0f}s")
    except httpx.HTTPError as exc:
        return KeyTestResult(ok=False, status=None, message=f"network error: {exc}")
    except Exception as exc:
        logger.exception("admin-llm-keys test failed for %s", provider)
        return KeyTestResult(ok=False, status=None, message=f"{type(exc).__name__}: {exc}")


__all__ = ["KeyTestResult", "run_key_test"]
