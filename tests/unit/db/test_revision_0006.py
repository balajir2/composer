"""Revision 0006 without a database: its downgrade DDL is 0001's, verbatim, and it is the head."""

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[3]
VERSIONS = ROOT / "src" / "db" / "migrations" / "versions"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, VERSIONS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_downgrade_ddl_is_0001s_verbatim() -> None:
    first_migration_sql = _load("0001_baseline").PRISMA_MIGRATIONS[0][1]
    statements = _load("0006_drop_legacy_tables").CREATE_OLD_TABLES
    assert len(statements) == 4  # two tables, two indexes
    for statement in statements:
        assert statement + ";" in first_migration_sql, statement


def test_0006_is_the_single_head_on_top_of_0005() -> None:
    script = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    assert script.get_heads() == ["0006_drop_legacy_tables"]
    assert script.get_revision("0006_drop_legacy_tables").down_revision == "0005_execution_attempts"


def test_guard_covers_every_active_status() -> None:
    module = _load("0006_drop_legacy_tables")
    assert module.ACTIVE_STATUSES == ("queued", "running", "waiting_approval")
    for status in module.ACTIVE_STATUSES:
        assert f"'{status}'" in module.LEGACY_ONLY_ACTIVE_RUNS
