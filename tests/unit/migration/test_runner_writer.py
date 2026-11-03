"""Behavioral tests for the one-way OAB -> Composer migration: loader, writer
idempotency, runner dry-run / partial-failure / token re-encryption behavior
(src/migration/{convex_loader,writer,runner}.py)."""

from __future__ import annotations

import base64
import json
import os
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from src.migration import convex_loader, runner, writer
from src.security.encryption import decrypt

if TYPE_CHECKING:
    from pathlib import Path

_COMPOSER_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
_OAB_KEY_BYTES = bytes(range(32))
_OAB_KEY_B64 = base64.b64encode(_OAB_KEY_BYTES).decode()


# ─── helpers ────────────────────────────────────────────────────────────────


def _write_jsonl(directory: Path, table: str, rows: list[dict[str, Any]]) -> None:
    (directory / f"{table}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )


def _oab_encrypt(plaintext: str) -> str:
    nonce = os.urandom(12)
    ct = AESGCM(_OAB_KEY_BYTES).encrypt(nonce, plaintext.encode(), None)
    return base64.b64encode(nonce + ct).decode()


def _fake_db(existing_ids: dict[str, set[str]] | None = None) -> MagicMock:
    """Fake Prisma client: find_unique answers from `existing_ids` per table."""
    existing_ids = existing_ids or {}
    db = MagicMock()
    for table in ("workflow", "workflowexecution", "mcpserver", "mcpoauthtoken"):
        ids = existing_ids.get(table, set())

        async def _find(*, where: dict[str, str], _ids: set[str] = ids) -> object | None:
            return object() if where["id"] in _ids else None

        getattr(db, table).find_unique = AsyncMock(side_effect=_find)
        getattr(db, table).create = AsyncMock()
    return db


class _FakePrismaClass:
    """Drop-in for `runner.Prisma`; the instance is shared so tests can inspect it."""

    db: MagicMock

    def __new__(cls) -> MagicMock:  # type: ignore[misc]
        return cls.db


def _install_db(monkeypatch: pytest.MonkeyPatch, db: MagicMock) -> None:
    db.connect = AsyncMock()
    db.disconnect = AsyncMock()
    _FakePrismaClass.db = db
    monkeypatch.setattr(runner, "Prisma", _FakePrismaClass)


def _users() -> list[dict[str, Any]]:
    return [{"clerkId": "clerk_a", "email": "A@Example.com"}]


def _workflow(i: int) -> dict[str, Any]:
    return {"_id": f"wf{i}", "userId": "clerk_a", "name": f"W{i}", "nodes": [], "edges": []}


# ─── convex_loader ──────────────────────────────────────────────────────────


def test_load_table_skips_blank_lines_and_returns_rows(tmp_path: Path) -> None:
    (tmp_path / "workflows.jsonl").write_text(
        '{"_id": "a"}\n\n  \n{"_id": "b"}\n', encoding="utf-8"
    )
    assert [r["_id"] for r in convex_loader.load_table(tmp_path, "workflows")] == ["a", "b"]


def test_load_table_missing_file_yields_empty_list(tmp_path: Path) -> None:
    assert convex_loader.load_table(tmp_path, "workflows") == []


def test_load_table_malformed_jsonl_raises_instead_of_silently_dropping_rows(
    tmp_path: Path,
) -> None:
    (tmp_path / "workflows.jsonl").write_text('{"_id": "a"}\n{not json}\n', encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        convex_loader.load_table(tmp_path, "workflows")


def test_load_all_requires_an_existing_directory(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="export directory not found"):
        convex_loader.load_all(tmp_path / "nope")


def test_load_all_returns_every_expected_table_even_when_files_absent(tmp_path: Path) -> None:
    _write_jsonl(tmp_path, "users", [{"clerkId": "c"}])
    tables = convex_loader.load_all(tmp_path)
    assert set(tables) == {"workflows", "executions", "mcpServers", "mcpOAuthTokens", "users"}
    assert tables["users"] == [{"clerkId": "c"}]
    assert tables["workflows"] == []


# ─── writer ─────────────────────────────────────────────────────────────────


async def test_upsert_workflow_inserts_new_row_with_json_wrapped_columns() -> None:
    db = _fake_db()
    data = {"id": "wf1", "name": "W", "nodes": [{"id": "n"}], "edges": []}
    assert await writer.upsert_workflow(db, data) is True
    created = db.workflow.create.await_args.kwargs["data"]
    assert created["id"] == "wf1"
    assert type(created["nodes"]).__name__ == "Json"
    assert type(created["edges"]).__name__ == "Json"


async def test_upsert_workflow_is_idempotent_and_never_overwrites_existing_row() -> None:
    db = _fake_db({"workflow": {"wf1"}})
    assert await writer.upsert_workflow(db, {"id": "wf1", "nodes": [], "edges": []}) is False
    db.workflow.create.assert_not_called()


async def test_upsert_execution_drops_null_optional_json_and_wraps_present_ones() -> None:
    db = _fake_db()
    data = {
        "id": "e1",
        "nodeResults": {},
        "variables": {},
        "input": None,
        "output": {"x": 1},
    }
    assert await writer.upsert_execution(db, data) is True
    created = db.workflowexecution.create.await_args.kwargs["data"]
    assert "input" not in created  # Prisma Python rejects None for Json?
    assert type(created["output"]).__name__ == "Json"


async def test_upsert_execution_skips_existing() -> None:
    db = _fake_db({"workflowexecution": {"e1"}})
    assert await writer.upsert_execution(db, {"id": "e1"}) is False
    db.workflowexecution.create.assert_not_called()


async def test_upsert_mcp_server_wraps_optional_json_only_when_set() -> None:
    db = _fake_db()
    data = {"id": "s1", "oauthConfig": {"a": 1}, "tools": None, "headers": None}
    assert await writer.upsert_mcp_server(db, data) is True
    created = db.mcpserver.create.await_args.kwargs["data"]
    assert type(created["oauthConfig"]).__name__ == "Json"
    assert "tools" not in created and "headers" not in created


async def test_upsert_mcp_server_skips_existing() -> None:
    db = _fake_db({"mcpserver": {"s1"}})
    assert await writer.upsert_mcp_server(db, {"id": "s1"}) is False
    db.mcpserver.create.assert_not_called()


async def test_upsert_mcp_oauth_token_inserts_then_skips_on_rerun() -> None:
    db = _fake_db()
    assert await writer.upsert_mcp_oauth_token(db, {"id": "t1"}) is True
    db2 = _fake_db({"mcpoauthtoken": {"t1"}})
    assert await writer.upsert_mcp_oauth_token(db2, {"id": "t1"}) is False
    db2.mcpoauthtoken.create.assert_not_called()


# ─── runner: token re-encryption ────────────────────────────────────────────


def test_reencryptor_requires_oab_key_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OAB_MCP_OAUTH_ENCRYPTION_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OAB_MCP_OAUTH_ENCRYPTION_KEY"):
        runner._make_token_reencryptor()  # pyright: ignore[reportPrivateUsage]


def test_reencryptor_round_trips_to_composer_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.config import get_settings

    monkeypatch.setenv("OAB_MCP_OAUTH_ENCRYPTION_KEY", _OAB_KEY_B64)
    monkeypatch.setenv("ENCRYPTION_KEY", _COMPOSER_KEY)
    get_settings.cache_clear()
    reencrypt = runner._make_token_reencryptor()  # pyright: ignore[reportPrivateUsage]
    source = _oab_encrypt("super-secret-token")
    out = reencrypt(source)
    assert out != source
    assert "super-secret-token" not in out
    assert decrypt(out) == "super-secret-token"


def test_reencryptor_rejects_tampered_ciphertext(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OAB_MCP_OAUTH_ENCRYPTION_KEY", _OAB_KEY_B64)
    reencrypt = runner._make_token_reencryptor()  # pyright: ignore[reportPrivateUsage]
    raw = bytearray(base64.b64decode(_oab_encrypt("tok")))
    raw[-1] ^= 0xFF
    with pytest.raises(Exception):  # noqa: B017 - cryptography raises InvalidTag
        reencrypt(base64.b64encode(bytes(raw)).decode())


# ─── runner: run_migration ──────────────────────────────────────────────────


async def test_run_migration_missing_export_dir_returns_1(tmp_path: Path) -> None:
    assert await runner.run_migration(export_dir=str(tmp_path / "gone"), dry_run=False) == 1


async def test_dry_run_returns_0_and_never_connects_to_the_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_jsonl(tmp_path, "workflows", [_workflow(1)])
    db = _fake_db()
    _install_db(monkeypatch, db)
    assert await runner.run_migration(export_dir=str(tmp_path), dry_run=True) == 0
    db.connect.assert_not_called()
    db.workflow.create.assert_not_called()


async def test_run_migration_inserts_rows_and_disconnects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_jsonl(tmp_path, "users", _users())
    _write_jsonl(tmp_path, "workflows", [_workflow(1), _workflow(2)])
    _write_jsonl(
        tmp_path,
        "executions",
        [{"_id": "e1", "workflowId": "wf1", "userId": "clerk_a", "status": "running"}],
    )
    _write_jsonl(
        tmp_path,
        "mcpServers",
        [{"_id": "s1", "name": "S", "url": "https://m.example.com", "authType": "none"}],
    )
    db = _fake_db()
    _install_db(monkeypatch, db)
    assert await runner.run_migration(export_dir=str(tmp_path), dry_run=False) == 0
    assert db.workflow.create.await_count == 2
    first = db.workflow.create.await_args_list[0].kwargs["data"]
    assert first["userId"] is None
    assert first["originalOwnerEmail"] == "a@example.com"
    exec_data = db.workflowexecution.create.await_args.kwargs["data"]
    assert exec_data["status"] == "failed"  # in-flight state is not recoverable
    assert exec_data["threadId"].startswith("migrated-")
    db.mcpserver.create.assert_awaited_once()
    db.disconnect.assert_awaited_once()


async def test_run_migration_is_idempotent_when_rows_already_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_jsonl(tmp_path, "workflows", [_workflow(1), _workflow(2)])
    db = _fake_db({"workflow": {"wf1", "wf2"}})
    _install_db(monkeypatch, db)
    assert await runner.run_migration(export_dir=str(tmp_path), dry_run=False) == 0
    db.workflow.create.assert_not_called()


async def test_run_migration_one_bad_row_does_not_abort_the_remaining_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = {"_id": "wf-bad", "userId": "clerk_a"}  # missing required `name`
    _write_jsonl(tmp_path, "workflows", [bad, _workflow(2)])
    db = _fake_db()
    _install_db(monkeypatch, db)
    assert await runner.run_migration(export_dir=str(tmp_path), dry_run=False) == 0
    assert db.workflow.create.await_count == 1
    assert db.workflow.create.await_args.kwargs["data"]["id"] == "wf2"


async def test_run_migration_database_write_failure_is_recorded_and_run_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_jsonl(tmp_path, "workflows", [_workflow(1), _workflow(2)])
    db = _fake_db()
    calls: list[str] = []

    async def _create(*, data: dict[str, Any]) -> None:
        calls.append(cast("str", data["id"]))
        if data["id"] == "wf1":
            raise RuntimeError("connection reset")

    db.workflow.create = AsyncMock(side_effect=_create)
    _install_db(monkeypatch, db)
    assert await runner.run_migration(export_dir=str(tmp_path), dry_run=False) == 0
    assert calls == ["wf1", "wf2"]
    db.disconnect.assert_awaited_once()


async def test_run_migration_reencrypts_oauth_tokens_before_storing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.config import get_settings

    monkeypatch.setenv("OAB_MCP_OAUTH_ENCRYPTION_KEY", _OAB_KEY_B64)
    monkeypatch.setenv("ENCRYPTION_KEY", _COMPOSER_KEY)
    get_settings.cache_clear()
    _write_jsonl(
        tmp_path,
        "mcpOAuthTokens",
        [
            {
                "_id": "t1",
                "mcpServerId": "s1",
                "userId": "clerk_a",
                "encryptedAccessToken": _oab_encrypt("access-1"),
                "encryptedRefreshToken": _oab_encrypt("refresh-1"),
            }
        ],
    )
    db = _fake_db()
    _install_db(monkeypatch, db)
    assert await runner.run_migration(export_dir=str(tmp_path), dry_run=False) == 0
    stored = db.mcpoauthtoken.create.await_args.kwargs["data"]
    assert decrypt(stored["encryptedAccessToken"]) == "access-1"
    assert decrypt(stored["encryptedRefreshToken"]) == "refresh-1"


async def test_run_migration_without_oab_key_fails_fast_before_any_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: a missing OAB key used to be discovered only after workflows,
    executions and servers were already written, leaving a half-applied
    one-way migration and an uncaught exception instead of exit code 1."""
    monkeypatch.delenv("OAB_MCP_OAUTH_ENCRYPTION_KEY", raising=False)
    _write_jsonl(tmp_path, "workflows", [_workflow(1)])
    _write_jsonl(
        tmp_path,
        "mcpOAuthTokens",
        [
            {
                "_id": "t1",
                "mcpServerId": "s1",
                "userId": "clerk_a",
                "encryptedAccessToken": "x",
            }
        ],
    )
    db = _fake_db()
    _install_db(monkeypatch, db)
    assert await runner.run_migration(export_dir=str(tmp_path), dry_run=False) == 1
    db.workflow.create.assert_not_called()


def test_migration_report_counters_accumulate_per_table() -> None:
    report = runner.MigrationReport()
    report.bump_inserted("workflows")
    report.bump_inserted("workflows")
    report.bump_skipped("executions")
    assert report.inserted == {"workflows": 2}
    assert report.skipped == {"executions": 1}
