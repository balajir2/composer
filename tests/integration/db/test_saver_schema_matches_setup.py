"""Alembic 0004 and the library's own setup() produce identical saver tables.

setup() runs against a throwaway schema (search_path pinned to it), never against public.
"""

import secrets
from typing import Any, cast

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection, sql
from psycopg.rows import dict_row

from src.db.url import to_libpq_url

pytestmark = pytest.mark.integration

TABLES = ("checkpoint_migrations", "checkpoints", "checkpoint_blobs", "checkpoint_writes")


async def _describe(conn: AsyncConnection[Any], schema: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for table in TABLES:
        cols = await (
            await conn.execute(
                "SELECT column_name, ordinal_position, data_type, is_nullable, column_default "
                "FROM information_schema.columns WHERE table_schema = %s AND table_name = %s "
                "ORDER BY ordinal_position",
                (schema, table),
            )
        ).fetchall()
        pk = await (
            await conn.execute(
                "SELECT kcu.column_name FROM information_schema.table_constraints tc "
                "JOIN information_schema.key_column_usage kcu "
                "  ON kcu.constraint_name = tc.constraint_name "
                " AND kcu.table_schema = tc.table_schema AND kcu.table_name = tc.table_name "
                "WHERE tc.constraint_type = 'PRIMARY KEY' AND tc.table_schema = %s "
                "AND tc.table_name = %s ORDER BY kcu.ordinal_position",
                (schema, table),
            )
        ).fetchall()
        idx = await (
            await conn.execute(
                "SELECT indexname, replace(indexdef, %s, 'public.') AS indexdef FROM pg_indexes "
                "WHERE schemaname = %s AND tablename = %s ORDER BY indexname",
                (f"{schema}.", schema, table),
            )
        ).fetchall()
        out[table] = {"columns": cols, "pk": pk, "indexes": idx}
    out["migration_rows"] = await (
        await conn.execute(
            sql.SQL("SELECT v FROM {}.checkpoint_migrations ORDER BY v").format(
                sql.Identifier(schema)
            )
        )
    ).fetchall()
    return out


async def test_alembic_tables_equal_what_setup_creates(test_database_url: str) -> None:
    url = to_libpq_url(test_database_url)
    scratch = f"saver_ref_{secrets.token_hex(4)}"
    kwargs: dict[str, Any] = {
        "autocommit": True,
        "row_factory": dict_row,
        "prepare_threshold": None,
    }
    async with await AsyncConnection.connect(url, **kwargs) as admin:
        await admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(scratch)))
        try:
            async with await AsyncConnection.connect(
                url, options=f"-c search_path={scratch}", **kwargs
            ) as scoped:
                await AsyncPostgresSaver(cast("Any", scoped)).setup()
            from_setup = await _describe(admin, scratch)
            from_alembic = await _describe(admin, "public")
        finally:
            await admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(scratch)))
    assert from_alembic == from_setup
    assert from_alembic["migration_rows"] == [{"v": i} for i in range(10)]
