from pathlib import Path

import pytest

from src.db.health import alembic_head_revision


def test_head_revision_does_not_depend_on_alembic_ini_or_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    alembic_head_revision.cache_clear()
    versions = Path(__file__).resolve().parents[3] / "src" / "db" / "migrations" / "versions"
    ids = {p.stem for p in versions.glob("*.py")}
    assert alembic_head_revision() in ids
