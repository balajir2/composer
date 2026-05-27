"""Boot-time sync of provider API keys from Postgres into runtime Settings.

In production, admins manage provider keys via the admin UI; the values are
stored AES-256-GCM-encrypted in the ``llm_api_keys`` table.  At workflow
execution time ``src/llm/providers.py`` reads from ``settings.<provider>_api_key``
fields (i.e. env vars) — there is no DB read on the hot path.

This module bridges the two halves on app startup: every row in
``llm_api_keys`` is decrypted and the plaintext is copied into the cached
Settings instance.  Env vars (.env / Cloud Run / Vercel) WIN when set —
that preserves the documented "DEV-ONLY override" semantics in
.env.example.  When the env-side value is empty (typical for Cloud Run
deployments that lean on the admin UI), the DB value fills it in.

A revision restart (Cloud Run rolls a new revision; k8s rolling-restart;
local ``uvicorn --reload``) picks up edits made via the admin UI.  The
existing ``composer keys sync --target vercel`` deploy-time path still
exists for setups that prefer pushing into the deployment env directly.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.config import get_settings
from src.security.encryption import EncryptionError, decrypt

if TYPE_CHECKING:
    from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]

logger = logging.getLogger(__name__)

# provider → Settings field name.  Mirrors src/cli/keys.py:_PROVIDER_TO_ENV
# but maps to the lowercased Settings attribute names instead of env var
# names — pydantic_settings handles the env-var ↔ field mapping.
_PROVIDER_TO_SETTINGS_FIELD: dict[str, str] = {
    "anthropic": "anthropic_api_key",
    "openai": "openai_api_key",
    "google": "google_api_key",
    "groq": "groq_api_key",
    "langsmith": "langchain_api_key",
    "tavily": "tavily_api_key",
    "firecrawl": "firecrawl_api_key",
    "serper": "serper_api_key",
    "browserless": "browserless_api_key",
    "gamma": "gamma_api_key",
}


async def sync_llm_keys_from_db(db: Prisma) -> dict[str, str]:  # pyright: ignore[reportUnknownParameterType]
    """Populate Settings from llm_api_keys.  Returns {provider: settings_field}
    for the rows that actually populated a previously-empty field.

    Decrypt failures and unknown providers are logged and skipped — startup
    must NOT crash because of a bad row.  An ENCRYPTION_KEY mismatch (e.g.
    after a key rotation without re-encryption) shows up here as a stream of
    warnings, which is the right signal to operators.
    """
    settings = get_settings()
    populated: dict[str, str] = {}
    try:
        rows = await db.llmapikey.find_many()  # pyright: ignore[reportAttributeAccessIssue]
    except Exception as exc:  # any DB error must be non-fatal at startup
        logger.warning("llm-key sync: failed to query llm_api_keys: %s", exc)
        return populated

    for row in rows:
        field = _PROVIDER_TO_SETTINGS_FIELD.get(row.provider)
        if field is None:
            logger.warning(
                "llm-key sync: no Settings field mapping for provider %r; skipping",
                row.provider,
            )
            continue
        existing = getattr(settings, field, "")
        if existing:
            # Env-side value wins: preserves "DEV-ONLY override" doc in .env.example.
            logger.info(
                "llm-key sync: %s already set via env (or earlier sync); leaving DB row untouched",
                field,
            )
            continue
        try:
            plaintext = decrypt(row.encryptedKey)
        except EncryptionError as exc:
            logger.warning(
                "llm-key sync: failed to decrypt %s key (prefix=%s): %s",
                row.provider,
                row.keyPrefix,
                exc,
            )
            continue
        setattr(settings, field, plaintext)
        populated[row.provider] = field

    if populated:
        logger.info("llm-key sync: populated %s", sorted(populated.keys()))
    else:
        logger.info(
            "llm-key sync: nothing to sync (all fields already set via env, or no rows decrypted)"
        )
    return populated


__all__ = ["sync_llm_keys_from_db"]
