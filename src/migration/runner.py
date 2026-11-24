"""Migration orchestrator.  Runs loader -> transformers -> writer.

Accepts --dry-run which prints planned inserts without writing.

Returns an int exit code: 0 on success (including partial skips), 1 on
fatal error (e.g., missing export dir, unrecoverable DB failure).
"""

from __future__ import annotations

import base64
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from src.config import get_settings
from src.db.runtime import one_shot_session
from src.migration import convex_loader, transformers, writer

logger = logging.getLogger(__name__)


@dataclass
class MigrationReport:
    inserted: dict[str, int] = field(default_factory=dict[str, int])
    skipped: dict[str, int] = field(default_factory=dict[str, int])
    errors: list[str] = field(default_factory=list[str])

    def bump_inserted(self, table: str) -> None:
        self.inserted[table] = self.inserted.get(table, 0) + 1

    def bump_skipped(self, table: str) -> None:
        self.skipped[table] = self.skipped.get(table, 0) + 1


def _make_token_reencryptor() -> Callable[[str], str]:
    """Return a function that re-encrypts OAB ciphertext -> Composer ciphertext.

    Requires OAB_MCP_OAUTH_ENCRYPTION_KEY env var (the key OAB used).
    Composer's ENCRYPTION_KEY is already configured via settings.
    """
    from src.security.encryption import encrypt  # lazy import

    oab_key_b64 = os.environ.get("OAB_MCP_OAUTH_ENCRYPTION_KEY")
    if not oab_key_b64:
        raise RuntimeError(
            "migration requires OAB_MCP_OAUTH_ENCRYPTION_KEY env var "
            "(the AES-256-GCM key OAB used to encrypt mcpOAuthTokens)"
        )
    oab_key = base64.b64decode(oab_key_b64)
    oab_aes = AESGCM(oab_key)

    def reencrypt(oab_ciphertext_b64: str) -> str:
        # OAB format: base64(nonce(12) + ciphertext + tag)
        raw = base64.b64decode(oab_ciphertext_b64)
        nonce, ct = raw[:12], raw[12:]
        plaintext = oab_aes.decrypt(nonce, ct, None)
        return encrypt(plaintext.decode("utf-8"))

    return reencrypt


async def run_migration(*, export_dir: str, dry_run: bool) -> int:
    path = Path(export_dir)
    try:
        tables = convex_loader.load_all(path)
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        return 1

    users = tables.get("users", [])
    report = MigrationReport()
    _settings = get_settings()

    if dry_run:
        logger.info("DRY RUN — no writes will be applied")
        logger.info("workflows: %d rows to consider", len(tables.get("workflows", [])))
        logger.info("executions: %d rows to consider", len(tables.get("executions", [])))
        logger.info("mcpServers: %d rows to consider", len(tables.get("mcpServers", [])))
        logger.info("mcpOAuthTokens: %d rows to consider", len(tables.get("mcpOAuthTokens", [])))
        logger.info("users skipped per Phase 9 spec §2 (no user migration)")
        if "userLLMKeys" in tables:
            logger.info(
                "userLLMKeys: %d source rows skipped (ADR-0022: LLM keys central)",
                len(tables["userLLMKeys"]),
            )
        return 0

    # Resolve the token re-encryptor BEFORE any write: discovering a missing
    # OAB key only after workflows/executions/servers were inserted would leave
    # a half-applied one-way migration.
    reencrypt: Callable[[str], str] | None = None
    if tables.get("mcpOAuthTokens"):
        try:
            reencrypt = _make_token_reencryptor()
        except RuntimeError as exc:
            logger.error("%s", exc)
            return 1

    # Workflows (SQLAlchemy; committed per row so one bad row can't undo the rest, and so
    # the rows exist before the executions that reference them)
    workflow_rows = tables.get("workflows", [])
    if workflow_rows:
        async with one_shot_session(get_settings()) as session:
            for row in workflow_rows:
                try:
                    data = transformers.workflow_row(row, users)
                    inserted = await writer.upsert_workflow(session, data)
                    await session.commit()
                    if inserted:
                        report.bump_inserted("workflows")
                    else:
                        report.bump_skipped("workflows")
                except Exception as exc:
                    await session.rollback()
                    msg = f"workflows row {row.get('_id')!r}: {type(exc).__name__}"
                    report.errors.append(msg)
                    logger.warning("%s", msg)

    # Executions (committed per row, like the other tables)
    execution_rows = tables.get("executions", [])
    if execution_rows:
        async with one_shot_session(get_settings()) as session:
            for row in execution_rows:
                try:
                    data = transformers.execution_row(row, users)
                    inserted = await writer.upsert_execution(session, data)
                    await session.commit()
                    if inserted:
                        report.bump_inserted("executions")
                    else:
                        report.bump_skipped("executions")
                except Exception as exc:
                    await session.rollback()
                    msg = f"executions row {row.get('_id')!r}: {type(exc).__name__}"
                    report.errors.append(msg)
                    logger.warning("%s", msg)

    # MCP servers (SQLAlchemy; one session, committed per row so one bad row can't undo the rest)
    mcp_rows = tables.get("mcpServers", [])
    if mcp_rows:
        async with one_shot_session(get_settings()) as session:
            for row in mcp_rows:
                try:
                    data = transformers.mcp_server_row(row, users)
                    inserted = await writer.upsert_mcp_server(session, data)
                    await session.commit()
                    if inserted:
                        report.bump_inserted("mcpServers")
                    else:
                        report.bump_skipped("mcpServers")
                except Exception as exc:
                    await session.rollback()
                    msg = f"mcpServers row {row.get('_id')!r}: {type(exc).__name__}"
                    report.errors.append(msg)
                    logger.warning("%s", msg)

    # MCP OAuth tokens (requires re-encrypt)
    if reencrypt is not None:
        async with one_shot_session(get_settings()) as session:
            for row in tables["mcpOAuthTokens"]:
                try:
                    data = transformers.mcp_oauth_token_row(row, reencrypt)
                    inserted = await writer.upsert_mcp_oauth_token(session, data)
                    await session.commit()
                    if inserted:
                        report.bump_inserted("mcpOAuthTokens")
                    else:
                        report.bump_skipped("mcpOAuthTokens")
                except Exception as exc:
                    await session.rollback()
                    msg = f"mcpOAuthTokens row {row.get('_id')!r}: {type(exc).__name__}"
                    report.errors.append(msg)
                    logger.warning("%s", msg)

    # Summary
    logger.info("migration complete")
    logger.info("inserted: %s", report.inserted)
    logger.info("skipped (already present): %s", report.skipped)
    if report.errors:
        logger.warning("errors (%d): skipped rows", len(report.errors))
    return 0


__all__ = ["MigrationReport", "run_migration"]
