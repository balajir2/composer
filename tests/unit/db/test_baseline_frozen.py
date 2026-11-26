"""0001_baseline's DDL is frozen: it is the SQL every pre-Alembic database ran.

If the fingerprint test fails, you edited 0001's SQL. Revert it and write a new Alembic
revision instead.
"""

import hashlib
import importlib.util
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Callable

REPO_ROOT = Path(__file__).resolve().parents[3]
BASELINE = REPO_ROOT / "src" / "db" / "migrations" / "versions" / "0001_baseline.py"
FROZEN_FINGERPRINT = "4dfba090a9bb39db3fceac405ddc057d5328ac8e8ca2ee95a533f76e2deb72cd"


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("baseline_0001", BASELINE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migrations() -> tuple[tuple[str, str], ...]:
    return cast("tuple[tuple[str, str], ...]", _module().PRISMA_MIGRATIONS)


def _fingerprint(migrations: tuple[tuple[str, str], ...]) -> str:
    digest = hashlib.sha256()
    for name, sql in migrations:
        digest.update(name.encode() + b"\0" + sql.encode() + b"\0")
    return digest.hexdigest()


def test_baseline_sql_is_unchanged() -> None:
    assert _fingerprint(_migrations()) == FROZEN_FINGERPRINT, (
        "0001_baseline's SQL is frozen: revert your change and write a new Alembic revision"
    )


def test_twenty_prisma_migrations_in_order() -> None:
    names = [n for n, _ in _migrations()]
    assert len(names) == 20
    assert names == sorted(names)
    assert names[0] == "20260420081000_phase_1_execution_engine"
    assert names[-1] == "20261117000000_execution_attempt_timing"


def test_sql_is_safe_to_send_unparameterised_and_in_one_transaction() -> None:
    """exec_driver_sql sends the text as-is (no '%' placeholders, no dollar quoting), and
    env.py runs every revision in one transaction (no CONCURRENTLY, no ALTER TYPE ADD VALUE,
    no explicit transaction control)."""
    for name, sql in _migrations():
        upper = sql.upper()
        assert "%" not in sql and "$$" not in sql, name
        for keyword in ("CONCURRENTLY", "ADD VALUE", "BEGIN;", "COMMIT;"):
            assert keyword not in upper, (name, keyword)


def test_statement_split_skips_comment_only_chunks() -> None:
    split = cast("Callable[[str], list[str]]", _module()._statements)  # pyright: ignore[reportPrivateUsage]
    assert split("-- CreateTable\nCREATE TABLE a (x int);\n\n-- tail\n") == [
        "-- CreateTable\nCREATE TABLE a (x int)"
    ]
