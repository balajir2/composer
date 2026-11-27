"""Nothing in ``src``, ``tests``, ``scripts`` or ``frontend/scripts`` imports Prisma, ``src.storage``
or asyncpg, and neither package is installed (SQLAlchemy migration phase 6). Tests never write
``app.state.db`` (no code reads it since phase 5).

Limits: relative imports (``from . import x``) are skipped, and transitive imports (a module
importing a helper that imports Prisma) are not followed. Only direct absolute imports are
checked.
"""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCANNED_ROOTS = ["src", "tests", "scripts", "frontend/scripts"]
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


def test_nothing_imports_prisma() -> None:
    assert _offenders("prisma", "src.storage") == []


def test_nothing_imports_asyncpg() -> None:
    assert _offenders("asyncpg") == []


def test_packages_are_not_installed() -> None:
    import importlib.util

    assert importlib.util.find_spec("prisma") is None
    assert importlib.util.find_spec("asyncpg") is None


def _writes_app_state_db(source: str) -> bool:
    for node in ast.walk(ast.parse(source)):
        targets = node.targets if isinstance(node, ast.Assign) else []
        for t in targets:
            if (
                isinstance(t, ast.Attribute)
                and t.attr == "db"
                and isinstance(t.value, ast.Attribute)
                and t.value.attr == "state"
            ):
                return True
    return False


def test_no_legacy_prisma_shim_or_app_state_db_writes() -> None:
    offenders: list[str] = []
    for path in sorted((REPO_ROOT / "tests").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if path.name == "test_prisma_import_boundary.py":
            continue
        if "NEEDS_LEGACY_PRISMA" in text or _writes_app_state_db(text):
            offenders.append(path.relative_to(REPO_ROOT).as_posix())
    assert offenders == []


def test_app_state_db_detector() -> None:
    assert _writes_app_state_db("app.state.db = x")
    assert not _writes_app_state_db("app.state.db_sessionmaker = x")


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
