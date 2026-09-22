"""`composer keys` subcommand handlers."""

from __future__ import annotations

import logging
import os
import sys

from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]
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
    db = Prisma()
    await db.connect()
    try:
        rows = await db.llmapikey.find_many(order={"provider": "asc"})  # pyright: ignore[reportAttributeAccessIssue]
        if not rows:
            print("(no keys set)")
            return 0
        for r in rows:
            print(f"  {r.provider:15s}  {r.keyPrefix}...  (updated {r.updatedAt})")
        return 0
    finally:
        await db.disconnect()


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
    db = Prisma()
    await db.connect()
    try:
        existing = await db.llmapikey.find_unique(where={"provider": provider})  # pyright: ignore[reportAttributeAccessIssue]
        if existing is None:
            await db.llmapikey.create(  # pyright: ignore[reportAttributeAccessIssue]
                data={"provider": provider, "encryptedKey": encrypted, "keyPrefix": prefix}
            )
            print(f"created key for {provider} (prefix {prefix})")
        else:
            await db.llmapikey.update(  # pyright: ignore[reportAttributeAccessIssue]
                where={"provider": provider},
                data={"encryptedKey": encrypted, "keyPrefix": prefix},
            )
            print(f"updated key for {provider} (prefix {prefix})")
        return 0
    finally:
        await db.disconnect()


async def keys_delete(provider: str) -> int:
    db = Prisma()
    await db.connect()
    try:
        existing = await db.llmapikey.find_unique(where={"provider": provider})  # pyright: ignore[reportAttributeAccessIssue]
        if existing is None:
            print(f"no key set for {provider}", file=sys.stderr)
            return 1
        await db.llmapikey.delete(where={"provider": provider})  # pyright: ignore[reportAttributeAccessIssue]
        print(f"deleted key for {provider}")
        return 0
    finally:
        await db.disconnect()


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
    db = Prisma()
    await db.connect()
    try:
        rows = await db.llmapikey.find_many()  # pyright: ignore[reportAttributeAccessIssue]
        posted: list[str] = []
        for r in rows:
            env_key = _PROVIDER_TO_ENV.get(r.provider)
            if env_key is None:
                logger.warning("no env-var mapping for provider %r; skipping", r.provider)
                continue
            plaintext = decrypt(r.encryptedKey)
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
    finally:
        await db.disconnect()


# Expose for test imports
_PROVIDER_TO_ENV_MAP = _PROVIDER_TO_ENV

__all__ = ["_PROVIDER_TO_ENV", "keys_delete", "keys_list", "keys_set", "keys_sync"]
