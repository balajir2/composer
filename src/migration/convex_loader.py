"""Load OAB Convex export JSON files.

`npx convex export` emits one JSON per table under <export-dir>/.  Each
file is a line-delimited JSON (JSONL).  We read them into memory because
Bounteous-internal OAB is small (handfuls of users).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

_EXPECTED_TABLES = {
    "workflows",
    "executions",
    "mcpServers",
    "mcpOAuthTokens",
    "users",
}


def load_table(export_dir: Path, table: str) -> list[dict[str, Any]]:
    """Read one table's JSONL export into a list of dicts.

    Returns empty list if the file is absent — some tables are optional.
    """
    path = export_dir / f"{table}.jsonl"
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def load_all(export_dir: Path) -> dict[str, list[dict[str, Any]]]:
    """Read all expected tables from the export directory.

    Unexpected files are ignored; missing files yield empty lists.
    """
    if not export_dir.is_dir():
        raise FileNotFoundError(f"export directory not found: {export_dir}")
    return {table: load_table(export_dir, table) for table in _EXPECTED_TABLES}


__all__ = ["load_all", "load_table"]
