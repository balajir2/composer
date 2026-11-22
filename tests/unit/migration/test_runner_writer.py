"""Behavioral tests for the one-way OAB -> Composer migration: loader, writer
idempotency, runner dry-run / partial-failure / token re-encryption behavior
(src/migration/{convex_loader,writer,runner}.py)."""

from __future__ import annotations

import base64
import json
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from src.db.repos import mcp_oauth as mcp_oauth_repo
from src.db.repos import mcp_servers as mcp_servers_repo
from src.db.repos import workflows as workflows_repo
from src.migration import convex_loader, runner, transformers, writer
from src.security.encryption import decrypt
from tests.unit.db_fakes import FakeSession

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncSession

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
    for table in ("workflowexecution",):
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


@pytest.fixture(autouse=True)
def _mcp_server_session(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:  # pyright: ignore[reportUnusedFunction]
    """MCP servers go through SQLAlchemy: stub the one-shot session and the repo insert."""
    session = FakeSession()

    @asynccontextmanager
    async def _scope(_settings: object) -> AsyncGenerator[FakeSession]:
        yield session

    insert = AsyncMock(return_value=True)
    monkeypatch.setattr(runner, "one_shot_session", _scope)
    monkeypatch.setattr(mcp_servers_repo, "insert_if_absent", insert)
    return insert


@pytest.fixture(autouse=True)
def _workflow_insert(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:  # pyright: ignore[reportUnusedFunction]
    """Workflows go through SQLAlchemy: stub the repo insert."""
    insert = AsyncMock(return_value=True)
    monkeypatch.setattr(workflows_repo, "insert_if_absent", insert)
    return insert


@pytest.fixture(autouse=True)
def _token_insert_stub(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:  # pyright: ignore[reportUnusedFunction]
    """MCP OAuth tokens go through SQLAlchemy too: stub the repo insert."""
    insert = AsyncMock(return_value=True)
    monkeypatch.setattr(mcp_oauth_repo, "insert_token_if_absent", insert)
    return insert


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


async def test_upsert_workflow_maps_columns_and_passes_plain_json(
    _workflow_insert: AsyncMock,
) -> None:
    session = FakeSession()
    data = {
        "id": "wf1",
        "userId": None,
        "originalOwnerEmail": "a@example.com",
        "name": "W",
        "tags": ["x"],
        "estimatedTime": "5m",
        "isTemplate": True,
        "nodes": [{"id": "n"}],
        "edges": [],
    }
    assert await writer.upsert_workflow(cast("AsyncSession", session), data) is True
    assert _workflow_insert.await_args is not None
    assert _workflow_insert.await_args.args[0] is session
    values = _workflow_insert.await_args.args[1]
    assert values["id"] == "wf1" and values["user_id"] is None
    assert values["original_owner_email"] == "a@example.com"
    assert values["estimated_time"] == "5m" and values["is_template"] is True
    assert values["nodes"] == [{"id": "n"}] and values["tags"] == ["x"]  # plain, no Json wrapper
    assert "userId" not in values and "estimatedTime" not in values


async def test_upsert_workflow_reports_the_repo_answer(_workflow_insert: AsyncMock) -> None:
    _workflow_insert.return_value = False
    session = cast("AsyncSession", FakeSession())
    assert await writer.upsert_workflow(session, {"id": "wf1", "nodes": [], "edges": []}) is False


def test_workflow_transformer_keys_are_all_known_to_the_writer_column_map() -> None:
    """A renamed transformer key must fail here, not silently drop a column on insert."""
    row = {"_id": "wf1", "userId": "clerk_a", "name": "W"}
    out = transformers.workflow_row(row, _users())
    assert set(out) <= set(writer._WORKFLOW_COLUMNS)  # pyright: ignore[reportPrivateUsage]


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


async def test_upsert_mcp_server_maps_columns_and_passes_plain_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    insert = AsyncMock(return_value=True)
    monkeypatch.setattr(mcp_servers_repo, "insert_if_absent", insert)
    session = FakeSession()
    data = {
        "id": "s1",
        "authType": "none",
        "userId": None,
        "originalOwnerEmail": "a@example.com",
        "oauthConfig": {"a": 1},
        "tools": None,
        "headers": None,
        "lastTested": 1_700_000_000_000,
    }
    assert await writer.upsert_mcp_server(cast("AsyncSession", session), data) is True
    assert insert.await_args is not None
    values = insert.await_args.args[1]
    assert insert.await_args.args[0] is session
    assert values["auth_type"] == "none" and values["original_owner_email"] == "a@example.com"
    assert values["oauth_config"] == {"a": 1}  # plain dict, no prisma Json wrapper
    assert values["tools"] is None and values["headers"] is None
    assert values["last_tested"] == datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC)
    assert "oauthConfig" not in values


_AWARE = datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1_700_000_000_000, _AWARE),
        (1_700_000_000_000.0, _AWARE),
        ("2023-11-14T22:13:20Z", _AWARE),
        ("2023-11-14T22:13:20+00:00", _AWARE),
        ("2023-11-14T22:13:20", _AWARE),  # naive ISO: taken as UTC
        ("2023-11-14T23:13:20+01:00", _AWARE),
        (datetime(2023, 11, 14, 22, 13, 20), _AWARE),  # naive datetime
        (_AWARE, _AWARE),
        (None, None),
    ],
)
def test_as_datetime_always_returns_aware_utc(value: object, expected: datetime | None) -> None:
    got = writer._as_datetime(value)  # pyright: ignore[reportPrivateUsage]
    assert got == expected
    if got is not None:
        assert got.utcoffset() is not None  # aware; the column type normalises the offset


def test_transformer_keys_are_all_known_to_the_writer_column_map() -> None:
    """A renamed transformer key must fail here, not silently drop a column on insert."""
    row = {
        "_id": "s1",
        "userId": "clerk_a",
        "name": "S",
        "url": "https://m.example.com",
        "authType": "none",
    }
    out = transformers.mcp_server_row(row, _users())
    assert set(out) <= set(writer._MCP_SERVER_COLUMNS)  # pyright: ignore[reportPrivateUsage]


async def test_upsert_mcp_server_skips_existing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_servers_repo, "insert_if_absent", AsyncMock(return_value=False))
    assert (
        await writer.upsert_mcp_server(cast("AsyncSession", FakeSession()), {"id": "s1"}) is False
    )


async def test_upsert_mcp_oauth_token_maps_columns_and_reports_the_repo_answer(
    _token_insert_stub: AsyncMock,
) -> None:
    _token_insert_stub.side_effect = [True, False]
    data = {
        "id": "t1",
        "mcpServerId": "s1",
        "userId": "u1",
        "encryptedAccessToken": "enc-a",
        "encryptedRefreshToken": None,
        "expiresAt": 1_700_000_000_000,
        "scope": None,
        "tokenType": "Bearer",
    }
    session = cast("AsyncSession", FakeSession())
    assert await writer.upsert_mcp_oauth_token(session, data) is True
    assert await writer.upsert_mcp_oauth_token(session, data) is False
    values = _token_insert_stub.await_args_list[0].args[1]
    assert values["id"] == "t1" and values["mcp_server_id"] == "s1"
    assert values["user_id"] == "u1" and values["encrypted_access_token"] == "enc-a"
    assert values["expires_at"] == datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC)
    assert values["token_type"] == "Bearer" and values["encrypted_refresh_token"] is None


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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _workflow_insert: AsyncMock
) -> None:
    _write_jsonl(tmp_path, "workflows", [_workflow(1)])
    db = _fake_db()
    _install_db(monkeypatch, db)
    assert await runner.run_migration(export_dir=str(tmp_path), dry_run=True) == 0
    db.connect.assert_not_called()
    _workflow_insert.assert_not_awaited()


async def test_run_migration_inserts_rows_and_disconnects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _mcp_server_session: AsyncMock,
    _workflow_insert: AsyncMock,
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
    assert _workflow_insert.await_count == 2
    first = _workflow_insert.await_args_list[0].args[1]
    assert first["user_id"] is None
    assert first["original_owner_email"] == "a@example.com"
    exec_data = db.workflowexecution.create.await_args.kwargs["data"]
    assert exec_data["status"] == "failed"  # in-flight state is not recoverable
    assert exec_data["threadId"].startswith("migrated-")
    _mcp_server_session.assert_awaited_once()
    db.disconnect.assert_awaited_once()


async def test_run_migration_is_idempotent_when_rows_already_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _workflow_insert: AsyncMock
) -> None:
    _write_jsonl(tmp_path, "workflows", [_workflow(1), _workflow(2)])
    _workflow_insert.return_value = False
    _install_db(monkeypatch, _fake_db())
    assert await runner.run_migration(export_dir=str(tmp_path), dry_run=False) == 0
    assert _workflow_insert.await_count == 2  # skipped, not failed


async def test_run_migration_one_bad_row_does_not_abort_the_remaining_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _workflow_insert: AsyncMock
) -> None:
    bad = {"_id": "wf-bad", "userId": "clerk_a"}  # missing required `name`
    _write_jsonl(tmp_path, "workflows", [bad, _workflow(2)])
    _install_db(monkeypatch, _fake_db())
    assert await runner.run_migration(export_dir=str(tmp_path), dry_run=False) == 0
    assert _workflow_insert.await_count == 1
    assert _workflow_insert.await_args.args[1]["id"] == "wf2"  # type: ignore[union-attr]


async def test_run_migration_database_write_failure_commits_per_row_and_logs_class_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    _workflow_insert: AsyncMock,
) -> None:
    _write_jsonl(tmp_path, "workflows", [_workflow(1), _workflow(2)])
    session = FakeSession()
    calls: list[str] = []

    @asynccontextmanager
    async def _scope(_settings: object) -> AsyncGenerator[FakeSession]:
        yield session

    async def _insert(_session: object, values: dict[str, Any]) -> bool:
        calls.append(cast("str", values["id"]))
        if values["id"] == "wf1":
            raise RuntimeError("INSERT failed [parameters: (FAKE-SECRET,)]")
        return True

    _workflow_insert.side_effect = _insert
    monkeypatch.setattr(runner, "one_shot_session", _scope)
    db = _fake_db()
    _install_db(monkeypatch, db)
    with caplog.at_level("WARNING"):
        assert await runner.run_migration(export_dir=str(tmp_path), dry_run=False) == 0
    assert calls == ["wf1", "wf2"]
    session.rollback.assert_awaited_once()
    session.commit.assert_awaited_once()  # only wf2 committed
    assert "workflows row 'wf1': RuntimeError" in caplog.text
    assert "FAKE-SECRET" not in caplog.text
    db.disconnect.assert_awaited_once()


async def test_run_migration_reencrypts_oauth_tokens_before_storing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _token_insert_stub: AsyncMock
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
    assert _token_insert_stub.await_args is not None
    stored = _token_insert_stub.await_args.args[1]
    assert decrypt(stored["encrypted_access_token"]) == "access-1"
    assert decrypt(stored["encrypted_refresh_token"]) == "refresh-1"


async def test_run_migration_logs_only_the_class_of_a_token_insert_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    _token_insert_stub: AsyncMock,
) -> None:
    """DB errors embed bound parameters (token ciphertext); the log must not."""
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
            }
        ],
    )
    _token_insert_stub.side_effect = RuntimeError(
        "INSERT failed [parameters: (FAKE-SECRET-CIPHERTEXT,)]"
    )
    _install_db(monkeypatch, _fake_db())
    with caplog.at_level("WARNING"):
        assert await runner.run_migration(export_dir=str(tmp_path), dry_run=False) == 0
    assert "mcpOAuthTokens row 't1': RuntimeError" in caplog.text
    assert "FAKE-SECRET-CIPHERTEXT" not in caplog.text


async def test_run_migration_without_oab_key_fails_fast_before_any_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _workflow_insert: AsyncMock
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
    _install_db(monkeypatch, _fake_db())
    assert await runner.run_migration(export_dir=str(tmp_path), dry_run=False) == 1
    _workflow_insert.assert_not_awaited()


def test_migration_report_counters_accumulate_per_table() -> None:
    report = runner.MigrationReport()
    report.bump_inserted("workflows")
    report.bump_inserted("workflows")
    report.bump_skipped("executions")
    assert report.inserted == {"workflows": 2}
    assert report.skipped == {"executions": 1}
