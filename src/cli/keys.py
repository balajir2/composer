"""`composer keys` subcommand handlers."""

from __future__ import annotations

import logging
import os
import sys

from src.config import get_settings
from src.db.repos import llm_api_keys as keys_repo
from src.db.runtime import one_shot_session
from src.integrations.vercel import VercelClient, VercelEnvVar
from src.security.encryption import decrypt, encrypt

logger = logging.getLogger(__name__)

_PROVIDER_TO_ENV: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GOOGLE_API_KEY",
    "groq": "GROQ_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "qwen": "QWEN_API_KEY",
    "langsmith": "LANGCHAIN_API_KEY",
    "tavily": "TAVILY_API_KEY",
    "firecrawl": "FIRECRAWL_API_KEY",
    "serper": "SERPER_API_KEY",
    "browserless": "BROWSERLESS_API_KEY",
    "gamma": "GAMMA_API_KEY",
    "resend": "RESEND_API_KEY",
    "typesafe": "TYPESAFE_API_KEY",
}


async def keys_list() -> int:
    async with one_shot_session(get_settings()) as session:
        rows = await keys_repo.list_keys(session)
    if not rows:
        print("(no keys set)")
        return 0
    for r in rows:
        print(f"  {r.provider:15s}  {r.key_prefix}...  (updated {r.updated_at})")
    return 0


async def keys_set(provider: str, value: str) -> int:
    if provider not in _PROVIDER_TO_ENV:
        logger.error("unsupported provider %r", provider)
        return 1
    # Read from stdin if value is '-'
    raw_value = sys.stdin.read().strip() if value == "-" else value
    if not raw_value:
        logger.error("empty key value")
        return 1
    encrypted = encrypt(raw_value)
    prefix = raw_value[:6]
    async with one_shot_session(get_settings()) as session:
        result = await keys_repo.upsert(
            session, provider, encrypted_key=encrypted, key_prefix=prefix
        )
        await session.commit()
    verb = "created" if result.created else "updated"
    print(f"{verb} key for {provider} (prefix {prefix})")
    return 0


async def keys_delete(provider: str) -> int:
    async with one_shot_session(get_settings()) as session:
        if not await keys_repo.delete(session, provider):
            print(f"no key set for {provider}", file=sys.stderr)
            return 1
        await session.commit()
    print(f"deleted key for {provider}")
    return 0


async def keys_sync(target: str, prune: bool) -> int:
    if target != "vercel":
        logger.error("only --target vercel supported")
        return 1
    api_token = os.environ.get("VERCEL_API_TOKEN")
    project_id = os.environ.get("VERCEL_PROJECT_ID")
    if not api_token or not project_id:
        logger.error("set VERCEL_API_TOKEN and VERCEL_PROJECT_ID env vars")
        return 1

    client = VercelClient(api_token=api_token, project_id=project_id)
    # Only the read needs the database; close it before the Vercel calls.
    async with one_shot_session(get_settings()) as session:
        rows = await keys_repo.list_keys(session)
    posted: list[str] = []
    for r in rows:
        env_key = _PROVIDER_TO_ENV.get(r.provider)
        if env_key is None:
            logger.warning("no env-var mapping for provider %r; skipping", r.provider)
            continue
        plaintext = decrypt(r.encrypted_key)
        await client.upsert_env(
            VercelEnvVar(key=env_key, value=plaintext, target=["production", "preview"])
        )
        posted.append(env_key)

    pruned: list[str] = []
    if prune:
        # Any Vercel env var tracked by us but not in Postgres → delete
        vercel_envs = await client.list_env()
        tracked_keys = set(_PROVIDER_TO_ENV.values())
        current_posted = set(posted)
        for env in vercel_envs:
            if env["key"] in tracked_keys and env["key"] not in current_posted:
                deleted = await client.delete_env(env["key"])
                if deleted:
                    pruned.append(env["key"])
    print(f"sync summary: posted={posted}, pruned={pruned}")
    return 0


# Expose for test imports
_PROVIDER_TO_ENV_MAP = _PROVIDER_TO_ENV

__all__ = ["_PROVIDER_TO_ENV", "keys_delete", "keys_list", "keys_set", "keys_sync"]
