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
    "src/builder/model_policy.py",
    "src/cli/keys.py",
    "src/security/api_key_auth.py",
    "src/security/auth.py",
    "src/security/key_sync.py",
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
