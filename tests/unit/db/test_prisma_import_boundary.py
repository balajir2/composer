"""Nothing in ``src`` may import Prisma or the (deleted) Prisma-based storage layer.

Phase 5 finished the move: the last Prisma client plumbing (``src/storage``, ``get_db``,
``prisma_lifespan``) is gone, so the whole of ``src`` is scanned instead of a growing list of
migrated areas. ``asyncpg`` is forbidden too since LISTEN/NOTIFY moved to psycopg (phase 5
Task 8); phase 6 removes Prisma and the asyncpg package from the dependencies and tests.

Limits: relative imports (``from . import x``) are skipped, and transitive imports (a module
importing a helper that imports Prisma) are not followed. Only direct absolute imports are
checked.
"""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCANNED_ROOTS = ["src"]
FORBIDDEN_ROOTS = ("prisma", "src.storage", "asyncpg")


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


def _offenders(*roots: str) -> list[str]:
    offenders: list[str] = []
    for relative in SCANNED_ROOTS:
        root = REPO_ROOT / relative
        assert root.exists(), f"SCANNED_ROOTS entry missing: {relative}"
        files = [root] if root.is_file() else sorted(root.rglob("*.py"))
        for path in files:
            found = _forbidden_imports(path.read_text(encoding="utf-8"))
            if any(name == r or name.startswith(f"{r}.") for name in found for r in roots):
                offenders.append(path.relative_to(REPO_ROOT).as_posix())
    return offenders


def test_src_does_not_import_prisma() -> None:
    assert _offenders("prisma", "src.storage") == []


def test_src_does_not_import_asyncpg() -> None:
    assert _offenders("asyncpg") == []


def test_the_prisma_storage_package_is_deleted() -> None:
    assert not (REPO_ROOT / "src" / "storage").exists()


def test_detector_catches_forbidden_imports() -> None:
    assert _forbidden_imports("import prisma") == ["prisma"]
    assert _forbidden_imports("from prisma.errors import X") == ["prisma.errors"]
    assert _forbidden_imports("import os, prisma") == ["prisma"]
    assert _forbidden_imports("from src.storage.db import get_db") == ["src.storage.db"]
    assert _forbidden_imports("import src.storage") == ["src.storage"]
    assert _forbidden_imports("import asyncpg") == ["asyncpg"]
    assert _forbidden_imports("from asyncpg import Connection") == ["asyncpg"]
    assert _forbidden_imports("from asyncpg.exceptions import X") == ["asyncpg.exceptions"]


def test_detector_ignores_lookalikes() -> None:
    assert _forbidden_imports("import prisma_tools") == []
    assert _forbidden_imports('"""import prisma"""\nx = 1') == []
    assert _forbidden_imports("from src.storage_x import y") == []


def test_detector_catches_submodule_imported_from_a_package() -> None:
    assert _forbidden_imports("from src import storage") == ["src.storage"]
    assert _forbidden_imports("from src import db") == []
