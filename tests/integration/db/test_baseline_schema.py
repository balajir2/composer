"""Alembic alone builds the same schema the old way built (Prisma's 20 migrations, then
Alembic). Each test runs in its own scratch database (CREATE DATABASE needs a role allowed to
create databases: CI's Postgres service and the local container are superusers)."""

import importlib.util
import json
import os
import subprocess
import sys
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from typing import cast

import psycopg
import pytest
from sqlalchemy.engine import make_url

from src.db.url import to_libpq_url
from tests.integration.db.schema_catalog import snapshot

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[3]
GOLDEN = REPO_ROOT / "tests" / "fixtures" / "schema"
BASELINE = REPO_ROOT / "src" / "db" / "migrations" / "versions" / "0001_baseline.py"

PRISMA_MIGRATIONS_TABLE = """
CREATE TABLE "_prisma_migrations" (
    "id" VARCHAR(36) PRIMARY KEY NOT NULL,
    "checksum" VARCHAR(64) NOT NULL,
    "finished_at" TIMESTAMPTZ,
    "migration_name" VARCHAR(255) NOT NULL,
    "logs" TEXT,
    "rolled_back_at" TIMESTAMPTZ,
    "started_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "applied_steps_count" INTEGER NOT NULL DEFAULT 0
)
"""


def _baseline_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("baseline_0001", BASELINE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migrations() -> tuple[tuple[str, str], ...]:
    return cast("tuple[tuple[str, str], ...]", _baseline_module().PRISMA_MIGRATIONS)


def _names() -> tuple[str, ...]:
    return cast("tuple[str, ...]", _baseline_module().PRISMA_MIGRATION_NAMES)


def _admin_url() -> str:
    return to_libpq_url(os.environ["TEST_DATABASE_URL"])


@pytest.fixture
def scratch_db() -> Iterator[str]:
    name = f"composer_scratch_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(_admin_url(), autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')  # pyright: ignore[reportCallIssue, reportArgumentType]
    url = make_url(_admin_url()).set(database=name).render_as_string(hide_password=False)
    try:
        yield url
    finally:
        with psycopg.connect(_admin_url(), autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')  # pyright: ignore[reportCallIssue, reportArgumentType]


def _alembic(url: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
        timeout=300,
    )


def _ok(url: str, *args: str) -> None:
    done = _alembic(url, *args)
    assert done.returncode == 0, done.stdout + done.stderr


def _refused(url: str, *args: str) -> str:
    done = _alembic(url, *args)
    assert done.returncode != 0
    return done.stderr + done.stdout


def _golden(name: str) -> object:
    return json.loads((GOLDEN / f"golden_{name}.json").read_text(encoding="utf-8"))


def _tables(url: str) -> set[str]:
    with psycopg.connect(url, autocommit=True) as conn:
        return {
            r[0]
            for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        }


def _versions(url: str) -> list[tuple[str]]:
    if "alembic_version" not in _tables(url):
        return []
    with psycopg.connect(url, autocommit=True) as conn:
        return [(r[0],) for r in conn.execute("SELECT version_num FROM alembic_version")]


def _prisma_built(
    url: str,
    names: tuple[str, ...] | None = None,
    *,
    unfinished: str | None = None,
    rolled_back: str | None = None,
    extra: tuple[str, ...] = (),
) -> None:
    """What prisma migrate deploy left behind: the DDL plus its history table."""
    split = cast("Callable[[str], list[str]]", _baseline_module()._statements)  # pyright: ignore[reportPrivateUsage]
    recorded = _names() if names is None else names
    with psycopg.connect(url, autocommit=True) as conn:
        for _, sql in _migrations():
            for stmt in split(sql):
                conn.execute(stmt)  # pyright: ignore[reportCallIssue, reportArgumentType]
        conn.execute(PRISMA_MIGRATIONS_TABLE)  # pyright: ignore[reportCallIssue, reportArgumentType]
        for n in (*recorded, *extra):
            conn.execute(
                "INSERT INTO _prisma_migrations (id, checksum, finished_at, rolled_back_at,"
                " migration_name, applied_steps_count) VALUES (%s, %s,"
                " CASE WHEN %s THEN NULL ELSE now() END,"
                " CASE WHEN %s THEN now() ELSE NULL END, %s, 1)",
                (str(uuid.uuid4()), "0" * 64, n == unfinished, n == rolled_back, n),
            )


def test_fresh_database_at_0001_matches_what_prisma_built(scratch_db: str) -> None:
    _ok(scratch_db, "upgrade", "0001_baseline")
    assert snapshot(scratch_db) == _golden("0001_baseline")


def test_fresh_database_at_head_matches_the_old_way(scratch_db: str) -> None:
    _ok(scratch_db, "upgrade", "0004_langgraph_saver_tables")
    assert snapshot(scratch_db) == _golden("0004_langgraph_saver_tables")


def test_fresh_database_at_head_matches_the_models(scratch_db: str) -> None:
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from sqlalchemy import create_engine

    from src.db.models import Base
    from src.db.schema_filters import include_name

    _ok(scratch_db, "upgrade", "head")
    engine = create_engine(make_url(scratch_db).set(drivername="postgresql+psycopg"))
    try:
        with engine.connect() as conn:
            ctx = MigrationContext.configure(
                conn,
                opts={
                    "include_name": include_name,
                    "compare_type": True,
                    "compare_server_default": True,
                },
            )
            assert compare_metadata(ctx, Base.metadata) == []
    finally:
        engine.dispose()


def test_a_database_prisma_built_is_adopted_without_ddl(scratch_db: str) -> None:
    _prisma_built(scratch_db)
    _ok(scratch_db, "upgrade", "head")
    assert snapshot(scratch_db) == _golden("0004_langgraph_saver_tables")


def test_adoption_records_0001_and_runs_no_ddl(scratch_db: str) -> None:
    _prisma_built(scratch_db)
    with psycopg.connect(scratch_db, autocommit=True) as conn:
        conn.execute("CREATE TABLE adoption_marker (id int)")
    _ok(scratch_db, "upgrade", "0001_baseline")  # re-running DDL would fail: tables exist
    assert "adoption_marker" in _tables(scratch_db)
    assert _versions(scratch_db) == [("0001_baseline",)]


def test_a_partial_prisma_history_is_refused(scratch_db: str) -> None:
    names = _names()
    _prisma_built(scratch_db, names[:-1])
    out = _refused(scratch_db, "upgrade", "head")
    assert "missing Prisma migrations" in out
    assert names[-1] in out
    assert _versions(scratch_db) == []


def test_an_unfinished_prisma_migration_is_refused(scratch_db: str) -> None:
    names = _names()
    _prisma_built(scratch_db, unfinished=names[5])
    out = _refused(scratch_db, "upgrade", "head")
    assert "unresolved Prisma migrations" in out
    assert names[5] in out
    assert _versions(scratch_db) == []


def test_a_rolled_back_prisma_migration_is_refused(scratch_db: str) -> None:
    names = _names()
    _prisma_built(scratch_db, rolled_back=names[-1])
    out = _refused(scratch_db, "upgrade", "head")
    assert "missing Prisma migrations" in out
    assert names[-1] in out
    assert _versions(scratch_db) == []


def test_unknown_prisma_migrations_are_refused(scratch_db: str) -> None:
    _prisma_built(scratch_db, extra=("20270101000000_from_the_future",))
    out = _refused(scratch_db, "upgrade", "head")
    assert "20270101000000_from_the_future" in out
    assert "unknown" in out
    assert _versions(scratch_db) == []


def test_tables_without_prisma_history_are_refused(scratch_db: str) -> None:
    with psycopg.connect(scratch_db, autocommit=True) as conn:
        conn.execute("CREATE TABLE workflows (id text PRIMARY KEY)")
    out = _refused(scratch_db, "upgrade", "head")
    assert "no Prisma history" in out
    assert _tables(scratch_db) == {"workflows"}


def test_tables_in_other_schemas_do_not_count_as_non_empty(scratch_db: str) -> None:
    with psycopg.connect(scratch_db, autocommit=True) as conn:
        conn.execute("CREATE SCHEMA other")
        conn.execute("CREATE TABLE other.stuff (id int)")
    _ok(scratch_db, "upgrade", "head")
    assert _versions(scratch_db) == [("0004_langgraph_saver_tables",)]


def test_an_existing_database_past_the_baseline_never_reruns_it(scratch_db: str) -> None:
    _prisma_built(scratch_db)
    _ok(scratch_db, "upgrade", "0002_idempotency_per_user")
    with psycopg.connect(scratch_db, autocommit=True) as conn:
        conn.execute("DROP TABLE _prisma_migrations")  # 0001 must not look at it again
    _ok(scratch_db, "upgrade", "head")
    assert snapshot(scratch_db) == _golden("0004_langgraph_saver_tables")


def test_a_database_at_0003_upgrades_to_head(scratch_db: str) -> None:
    _ok(scratch_db, "upgrade", "0003_assignment_user_fks")
    _ok(scratch_db, "upgrade", "head")
    assert snapshot(scratch_db) == _golden("0004_langgraph_saver_tables")


def test_downgrade_below_the_baseline_is_refused(scratch_db: str) -> None:
    _ok(scratch_db, "upgrade", "0001_baseline")
    out = _refused(scratch_db, "downgrade", "base")
    assert "0001 is the base schema" in out
    with psycopg.connect(scratch_db, autocommit=True) as conn:
        assert conn.execute("SELECT to_regclass('public.workflows')").fetchone() == ("workflows",)


def _history_row(url: str, name: str, *, finished: bool = True, rolled_back: bool = False) -> None:
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO _prisma_migrations (id, checksum, finished_at, rolled_back_at,"
            " migration_name, applied_steps_count) VALUES (%s, %s,"
            " CASE WHEN %s THEN now() END, CASE WHEN %s THEN now() END, %s, 1)",
            (str(uuid.uuid4()), "0" * 64, finished, rolled_back, name),
        )


def test_an_unfinished_unknown_row_beside_all_twenty_is_refused(scratch_db: str) -> None:
    _prisma_built(scratch_db)
    _history_row(scratch_db, "20261201000000_feature_branch", finished=False)
    with psycopg.connect(scratch_db, autocommit=True) as conn:
        conn.execute("CREATE TABLE stray (id int)")
    out = _refused(scratch_db, "upgrade", "head")
    assert "unresolved Prisma migrations" in out
    assert "20261201000000_feature_branch" in out
    assert "prisma migrate resolve" in out
    assert _versions(scratch_db) == []


def test_an_unfinished_known_row_is_refused_even_after_a_finished_retry(scratch_db: str) -> None:
    names = _names()
    _prisma_built(scratch_db)
    _history_row(scratch_db, names[3], finished=False)  # Prisma refuses this state (P3009)
    out = _refused(scratch_db, "upgrade", "head")
    assert "unresolved Prisma migrations" in out
    assert names[3] in out
    assert _versions(scratch_db) == []


def test_a_rolled_back_row_followed_by_a_successful_retry_is_adopted(scratch_db: str) -> None:
    names = _names()
    _prisma_built(scratch_db)
    _history_row(scratch_db, names[3], finished=False, rolled_back=True)
    _ok(scratch_db, "upgrade", "head")
    assert snapshot(scratch_db) == _golden("0004_langgraph_saver_tables")


def test_a_rolled_back_unknown_row_is_ignored(scratch_db: str) -> None:
    _prisma_built(scratch_db)
    _history_row(scratch_db, "20261201000000_abandoned", finished=False, rolled_back=True)
    _ok(scratch_db, "upgrade", "head")
    assert snapshot(scratch_db) == _golden("0004_langgraph_saver_tables")


def test_refusal_advice_matches_the_case(scratch_db: str) -> None:
    names = _names()
    _prisma_built(scratch_db, names[:-1])
    out = _refused(scratch_db, "upgrade", "head")
    assert "prisma migrate deploy" in out
    assert "58d2829" in out
    assert "postgresql://" not in out


def test_unknown_migration_advice_says_drop_or_restore(scratch_db: str) -> None:
    _prisma_built(scratch_db, extra=("20270101000000_from_the_future",))
    out = _refused(scratch_db, "upgrade", "head")
    assert "drop" in out.lower() and "restore" in out
    assert "prisma migrate deploy" not in out


def test_everything_lands_in_public_even_if_a_role_schema_exists(scratch_db: str) -> None:
    """With a schema named after the role, "$user" leads the search_path. env.py pins public,
    so alembic_version, the base tables and 0004's saver tables all land there."""
    role = make_url(scratch_db).username
    assert role
    with psycopg.connect(scratch_db, autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA "{role}"')  # pyright: ignore[reportCallIssue, reportArgumentType]
    _ok(scratch_db, "upgrade", "head")
    with psycopg.connect(scratch_db, autocommit=True) as conn:
        in_role = conn.execute(
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE n.nspname = %s",
            (role,),
        ).fetchone()
    assert in_role == (0,)
    public = _tables(scratch_db)
    assert {"alembic_version", "workflows", "checkpoints", "checkpoint_migrations"} <= public
    assert snapshot(scratch_db) == _golden("0004_langgraph_saver_tables")


def test_leftover_types_without_tables_are_refused(scratch_db: str) -> None:
    with psycopg.connect(scratch_db, autocommit=True) as conn:
        conn.execute("CREATE TYPE \"UserRole\" AS ENUM ('admin', 'member')")
    out = _refused(scratch_db, "upgrade", "head")
    assert "no Prisma history" in out
    assert "already exists" not in out
    assert _versions(scratch_db) == []
