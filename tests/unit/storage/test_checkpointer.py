"""Tests for PrismaCheckpointSaver.

These are unit tests that mock the Prisma client. The real Prisma round-trip
is exercised by the integration test in Task 17.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langgraph.checkpoint.base import CheckpointMetadata

from src.storage.checkpointer import PrismaCheckpointSaver


def _mock_db() -> MagicMock:
    db = MagicMock()
    db.langgraphcheckpoint = MagicMock()
    db.langgraphcheckpoint.upsert = AsyncMock()
    db.langgraphcheckpoint.find_first = AsyncMock(return_value=None)
    db.langgraphcheckpoint.find_unique = AsyncMock(return_value=None)
    db.langgraphcheckpoint.find_many = AsyncMock(return_value=[])
    db.langgraphcheckpoint.delete_many = AsyncMock()
    db.langgraphcheckpointwrite = MagicMock()
    db.langgraphcheckpointwrite.upsert = AsyncMock()
    db.langgraphcheckpointwrite.find_many = AsyncMock(return_value=[])
    db.langgraphcheckpointwrite.delete_many = AsyncMock()
    return db


@pytest.fixture
def saver() -> tuple[PrismaCheckpointSaver, MagicMock]:
    db = _mock_db()
    return PrismaCheckpointSaver(db), db


def _config(thread_id: str = "t1", checkpoint_id: str | None = None) -> dict[str, Any]:
    cfg: dict[str, Any] = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    if checkpoint_id is not None:
        cfg["configurable"]["checkpoint_id"] = checkpoint_id
    return cfg


def _checkpoint(cid: str = "cp1") -> dict[str, Any]:
    # Minimal valid Checkpoint dict shape for the default JsonPlusSerializer.
    return {
        "v": 1,
        "id": cid,
        "ts": "2026-04-20T00:00:00Z",
        "channel_values": {"variables": {"x": 1}},
        "channel_versions": {"variables": "1"},
        "versions_seen": {},
        "pending_sends": [],
    }


async def test_aput_upserts_checkpoint_with_serialized_bytes(
    saver: tuple[PrismaCheckpointSaver, MagicMock],
) -> None:
    s, db = saver
    config = _config()
    result_config = await s.aput(
        config,  # pyright: ignore[reportArgumentType]
        _checkpoint(),  # pyright: ignore[reportArgumentType]
        CheckpointMetadata(),
        {},
    )
    assert db.langgraphcheckpoint.upsert.await_count == 1
    call = db.langgraphcheckpoint.upsert.await_args
    assert call is not None
    # The `data.create.checkpoint` and `data.create.metadata` fields must be bytes
    create_payload = call.kwargs["data"]["create"]
    assert isinstance(create_payload["checkpoint"], bytes)
    assert isinstance(create_payload["metadata"], bytes)
    assert create_payload["threadId"] == "t1"
    # Config should be updated with the checkpoint_id
    configurable = result_config.get("configurable") or {}
    assert configurable["checkpoint_id"] == "cp1"


async def test_aput_writes_upserts_each_write(
    saver: tuple[PrismaCheckpointSaver, MagicMock],
) -> None:
    s, db = saver
    config = _config(checkpoint_id="cp1")
    writes: list[tuple[str, Any]] = [("variables", {"y": 2}), ("loop_results", [1, 2])]
    await s.aput_writes(config, writes, "task-abc")  # pyright: ignore[reportArgumentType]
    assert db.langgraphcheckpointwrite.upsert.await_count == 2


async def test_aget_tuple_returns_none_when_missing(
    saver: tuple[PrismaCheckpointSaver, MagicMock],
) -> None:
    s, _db = saver
    result = await s.aget_tuple(_config())  # pyright: ignore[reportArgumentType]
    assert result is None


async def test_adelete_thread_deletes_writes_before_checkpoints(
    saver: tuple[PrismaCheckpointSaver, MagicMock],
) -> None:
    """Guards against orphaned writes — writes must be deleted BEFORE the parent
    checkpoint rows, since Prisma can't express a composite-PK FK from writes to
    checkpoints in schema.prisma. The saver owns this ordering contract.
    """
    s, db = saver

    # Record call order using a shared order-capture list
    call_order: list[str] = []

    async def _writes_delete(*_args: Any, **_kwargs: Any) -> None:
        call_order.append("writes")

    async def _checkpoints_delete(*_args: Any, **_kwargs: Any) -> None:
        call_order.append("checkpoints")

    db.langgraphcheckpointwrite.delete_many = AsyncMock(side_effect=_writes_delete)
    db.langgraphcheckpoint.delete_many = AsyncMock(side_effect=_checkpoints_delete)

    await s.adelete_thread("t-42")

    assert call_order == ["writes", "checkpoints"]
    # Both deletions were scoped by thread_id
    writes_call = db.langgraphcheckpointwrite.delete_many.await_args
    checkpoints_call = db.langgraphcheckpoint.delete_many.await_args
    assert writes_call is not None
    assert checkpoints_call is not None
    assert writes_call.kwargs["where"]["threadId"] == "t-42"
    assert checkpoints_call.kwargs["where"]["threadId"] == "t-42"
