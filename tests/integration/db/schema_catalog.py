"""A normalized catalog of the public schema, for comparing two databases' schemas.

Used to prove that a database built by Alembic alone matches one built the old way (Prisma's
20 migrations, then Alembic). Captures what pg_dump would show that matters to the app:
tables, columns in order (type, nullability, default), constraints, indexes, enum labels,
sequences, functions, and the rows of checkpoint_migrations. Ownership and privileges are
left out (they depend on the connecting role).

    uv run python -m tests.integration.db.schema_catalog <postgres url> <out.json>
"""

import json
import sys
from pathlib import Path
from typing import Any

import psycopg

# _prisma_migrations stays ignored: the adoption tests build it by hand and compare with the
# 0001/0004 goldens, at revisions where it still exists (0006 drops it).
IGNORED = frozenset({"alembic_version", "_prisma_migrations"})

_TABLES = """
SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') ORDER BY 1
"""
_COLUMNS = """
SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull,
       pg_get_expr(d.adbin, d.adrelid)
FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY c.relname, a.attnum
"""
_CONSTRAINTS = """
SELECT cl.relname, co.conname, co.contype::text, pg_get_constraintdef(co.oid)
FROM pg_constraint co JOIN pg_class cl ON cl.oid = co.conrelid
WHERE co.connamespace = 'public'::regnamespace ORDER BY 1, 2
"""
_INDEXES = "SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname = 'public' ORDER BY 1, 2"
_ENUMS = """
SELECT t.typname, array_agg(e.enumlabel::text ORDER BY e.enumsortorder)
FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid
WHERE t.typnamespace = 'public'::regnamespace GROUP BY t.typname ORDER BY 1
"""
_SEQUENCES = "SELECT sequence_name FROM information_schema.sequences WHERE sequence_schema = 'public' ORDER BY 1"
_FUNCTIONS = "SELECT proname FROM pg_proc WHERE pronamespace = 'public'::regnamespace ORDER BY 1"


def snapshot(conninfo: str, *, exclude: frozenset[str] = IGNORED) -> dict[str, Any]:
    with psycopg.connect(conninfo, autocommit=True) as conn:
        tables = [r[0] for r in conn.execute(_TABLES) if r[0] not in exclude]
        columns: dict[str, list[list[Any]]] = {t: [] for t in tables}
        for table, name, type_, not_null, default in conn.execute(_COLUMNS):
            if table in columns:
                columns[table].append([name, type_, not_null, default])
        constraints = [list(r) for r in conn.execute(_CONSTRAINTS) if r[0] not in exclude]
        indexes = [list(r) for r in conn.execute(_INDEXES) if r[0] not in exclude]
        enums = {name: list(labels) for name, labels in conn.execute(_ENUMS)}
        sequences = [r[0] for r in conn.execute(_SEQUENCES)]
        functions = [r[0] for r in conn.execute(_FUNCTIONS)]
        rows: dict[str, list[Any]] = {}
        if "checkpoint_migrations" in tables:
            rows["checkpoint_migrations"] = [
                r[0] for r in conn.execute("SELECT v FROM checkpoint_migrations ORDER BY v")
            ]
    return {
        "tables": tables,
        "columns": columns,
        "constraints": constraints,
        "indexes": indexes,
        "enums": enums,
        "sequences": sequences,
        "functions": functions,
        "rows": rows,
    }


def dump(catalog: dict[str, Any]) -> str:
    return json.dumps(catalog, indent=2, sort_keys=True) + "\n"


def main(argv: list[str]) -> int:
    conninfo, out = argv
    Path(out).write_text(dump(snapshot(conninfo)), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
