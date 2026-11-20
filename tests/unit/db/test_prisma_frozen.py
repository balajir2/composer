"""Prisma's schema and migrations are frozen; schema changes go through Alembic.

If this fails, you changed prisma/. Revert it and write an Alembic revision
instead (uv run alembic revision -m "..."). Only the migration's phase 6 PR
may update this fingerprint, when Prisma is removed.
"""

import hashlib
from pathlib import Path

PRISMA_DIR = Path(__file__).resolve().parents[3] / "prisma"
FROZEN_FINGERPRINT = "c8a3b0849e731c37b11ce406d390a274751b4195a727b4ff9b1d0ca2e0d61dce"


def _fingerprint(prisma_dir: Path = PRISMA_DIR) -> str:
    """Hash only real Prisma files, so stray files (.DS_Store, backups) are ignored."""
    migrations = prisma_dir / "migrations"
    candidates = [
        prisma_dir / "schema.prisma",
        migrations / "migration_lock.toml",
        *migrations.glob("*/migration.sql"),
    ]
    files = sorted(p for p in candidates if p.is_file())
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.relative_to(prisma_dir).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
        digest.update(b"\0")
    return digest.hexdigest()


def test_prisma_schema_and_migrations_are_unchanged() -> None:
    assert _fingerprint() == FROZEN_FINGERPRINT, (
        "prisma/ is frozen: revert your change there and write an Alembic revision "
        'instead (uv run alembic revision -m "...")'
    )


def test_stray_files_do_not_change_the_fingerprint(tmp_path: Path) -> None:
    (tmp_path / "migrations" / "20260101000000_init").mkdir(parents=True)
    (tmp_path / "schema.prisma").write_text("model A {}\n")
    (tmp_path / "migrations" / "migration_lock.toml").write_text('provider = "postgresql"\n')
    (tmp_path / "migrations" / "20260101000000_init" / "migration.sql").write_text("SELECT 1;\n")
    before = _fingerprint(tmp_path)

    (tmp_path / ".DS_Store").write_bytes(b"junk")
    (tmp_path / "migrations" / "x").mkdir()
    (tmp_path / "migrations" / "x" / "notes.txt").write_text("notes")

    assert _fingerprint(tmp_path) == before
