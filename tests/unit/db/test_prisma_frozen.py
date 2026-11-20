"""Prisma's schema and migrations are frozen; schema changes go through Alembic.

If this fails, you changed prisma/. Revert it and write an Alembic revision
instead (uv run alembic revision -m "..."). Only the migration's phase 6 PR
may update this fingerprint, when Prisma is removed.
"""

import hashlib
from pathlib import Path

PRISMA_DIR = Path(__file__).resolve().parents[3] / "prisma"
FROZEN_FINGERPRINT = "c8a3b0849e731c37b11ce406d390a274751b4195a727b4ff9b1d0ca2e0d61dce"


def _fingerprint() -> str:
    files = sorted(
        p
        for p in [PRISMA_DIR / "schema.prisma", *(PRISMA_DIR / "migrations").rglob("*")]
        if p.is_file()
    )
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.relative_to(PRISMA_DIR).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
        digest.update(b"\0")
    return digest.hexdigest()


def test_prisma_schema_and_migrations_are_unchanged() -> None:
    assert _fingerprint() == FROZEN_FINGERPRINT
