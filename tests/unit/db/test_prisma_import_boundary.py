"""Code in migrated areas must not import Prisma.

Each migration phase adds the areas it ported to MIGRATED_PATHS. The list
only grows, until phase 6 removes Prisma entirely.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATED_PATHS = ["src/db"]
PRISMA_IMPORT = re.compile(r"^\s*(from|import)\s+prisma\b", re.MULTILINE)


def test_migrated_areas_do_not_import_prisma() -> None:
    offenders: list[str] = []
    for relative in MIGRATED_PATHS:
        root = REPO_ROOT / relative
        files = [root] if root.is_file() else sorted(root.rglob("*.py"))
        for path in files:
            if PRISMA_IMPORT.search(path.read_text(encoding="utf-8")):
                offenders.append(path.relative_to(REPO_ROOT).as_posix())
    assert offenders == [], f"Prisma imported in migrated areas: {offenders}"
