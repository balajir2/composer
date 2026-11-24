"""Code in migrated areas must not import Prisma or the Prisma-based storage layer.

Each migration phase adds the areas it ported to MIGRATED_PATHS. The list
only grows, until phase 6 removes Prisma entirely.

Limits: relative imports (``from . import x``) are skipped, and transitive
imports (a migrated module importing a helper that imports Prisma) are not
followed. Only direct absolute imports are checked.
"""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
# Phase 2 (integrations) areas: src/mcp, src/integrations/google_drive, the MCP and
# cloud-storage routes, src/tools/registry.py and the mcp / download-pdf executors.
# Deliberately NOT listed, because they still use Prisma for workflows/executions or
# other not-yet-migrated tables: src/api/internal.py (workflow reads, executions),
# src/api/test_cleanup.py (deletes workflows/executions via Prisma), src/migration/*
# (reconcile, runner and writer work on Prisma-backed rows). They move in later phases.
# Phase 3 (workflows) areas: the workflows routes and test cleanup. Still on Prisma for
# executions, the checkpointer or the migration source, so NOT listed: src/api/builder.py
# (agent/execution tooling via ``db``), src/api/run.py and src/api/executions.py
# (execution rows), src/api/internal.py (executions, approvals),
# src/engine/langgraph_executor.py (execution rows), src/migration/* (Convex-sourced
# rows on the Prisma client). The workflow tables themselves are reached only through
# ``src.db.repos`` from all of them.
# (The exclusions above for src/api/builder.py, src/engine and src/migration were lifted in 4a.)
# Phase 4a (executions and events) areas: the execution engine, events and rate limits,
# the sweeper, the builder and the migration CLI. Still on Prisma, so NOT listed:
# src/api/executions.py and src/api/approval_email.py (approval resume paths, phase 4b),
# src/api/run.py and src/api/internal.py (``get_db``/``get_checkpointer`` for the enqueue
# fallback and the checkpointer, phase 5), src/execution/cloud_tasks.py
# (``PrismaCheckpointSaver``, phase 5), src/storage (checkpointer and Prisma client,
# phases 5 and 6).
MIGRATED_PATHS = [
    "src/db",
    "src/api/admin_deployment_settings.py",
    "src/api/admin_llm_keys.py",
    "src/api/admin_llm_models.py",
    "src/api/admin_tools.py",
    "src/api/admin_users.py",
    "src/api/api_keys.py",
    "src/api/auth_common.py",
    "src/api/auth_standalone.py",
    "src/api/llm_models.py",
    "src/api/llm_models_live.py",
    "src/api/users.py",
    "src/api/workflows.py",
    "src/api/_idempotency.py",
    "src/api/events_ws.py",
    "src/api/builder.py",
    "src/api/test_cleanup.py",
    "src/api/cloud_storage_oauth.py",
    "src/api/mcp_servers.py",
    "src/builder",
    "src/cli/keys.py",
    "src/executors/download_pdf.py",
    "src/executors/mcp.py",
    "src/engine",
    "src/integrations/google_drive",
    "src/maintenance",
    "src/mcp",
    "src/migration",
    "src/security/api_key_auth.py",
    "src/security/auth.py",
    "src/security/key_sync.py",
    "src/security/rate_limit_pg.py",
    "src/tools/registry.py",
]
FORBIDDEN_ROOTS = ("prisma", "src.storage")


def _is_forbidden(name: str) -> bool:
    return any(name == root or name.startswith(f"{root}.") for root in FORBIDDEN_ROOTS)


def _forbidden_imports(source: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names if _is_forbidden(alias.name))
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if _is_forbidden(node.module):
                found.append(node.module)
                continue
            found.extend(
                qualified
                for alias in node.names
                if _is_forbidden(qualified := f"{node.module}.{alias.name}")
            )
    return found


def test_migrated_areas_do_not_import_prisma() -> None:
    offenders: list[str] = []
    for relative in MIGRATED_PATHS:
        root = REPO_ROOT / relative
        assert root.exists(), f"MIGRATED_PATHS entry missing: {relative}"
        files = [root] if root.is_file() else sorted(root.rglob("*.py"))
        for path in files:
            if _forbidden_imports(path.read_text(encoding="utf-8")):
                offenders.append(path.relative_to(REPO_ROOT).as_posix())
    assert offenders == [], f"Prisma imported in migrated areas: {offenders}"


def test_detector_catches_forbidden_imports() -> None:
    assert _forbidden_imports("import prisma") == ["prisma"]
    assert _forbidden_imports("from prisma.errors import X") == ["prisma.errors"]
    assert _forbidden_imports("import os, prisma") == ["prisma"]
    assert _forbidden_imports("from src.storage.db import get_db") == ["src.storage.db"]
    assert _forbidden_imports("import src.storage") == ["src.storage"]


def test_detector_ignores_lookalikes() -> None:
    assert _forbidden_imports("import prisma_tools") == []
    assert _forbidden_imports('"""import prisma"""\nx = 1') == []
    assert _forbidden_imports("from src.storage_x import y") == []


def test_detector_catches_submodule_imported_from_a_package() -> None:
    assert _forbidden_imports("from src import storage") == ["src.storage"]
    assert _forbidden_imports("from src import db") == []
