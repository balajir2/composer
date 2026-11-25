"""SQLAlchemy port of the old Prisma checkpoint saver, over the OLD checkpoint tables.

Kept until phase 7 so the copy tool and copy-on-resume can read and write
``langgraph_checkpoints`` / ``langgraph_checkpoint_writes`` after Prisma is removed. The byte
format is unchanged: each value is ``>I`` tag length | tag | payload around the typed
``JsonPlusSerializer`` output. Differences from the Prisma saver: the newest checkpoint and the
``before`` bound use ``checkpoint_id`` order (not ``created_at``, which has millisecond precision
and can tie). Writes upsert (last wins), as before.

Each method opens one short session and closes it before returning; ``alist`` reads every row in
one session and yields afterwards, so no connection is held while the consumer works.
"""

import struct
from collections.abc import AsyncIterator, Sequence
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db.repos import checkpoints as checkpoints_repo
from src.db.repos.checkpoints import LegacyCheckpointRow, LegacyWriteRow

_HEADER_FMT = ">I"
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)
_ASYNC_ONLY = "Use the async API"


def _pack(typed: tuple[str, bytes]) -> bytes:
    tag, payload = typed
    tag_bytes = tag.encode()
    return struct.pack(_HEADER_FMT, len(tag_bytes)) + tag_bytes + payload


def _unpack(data: bytes) -> tuple[str, bytes]:
    (tag_len,) = struct.unpack_from(_HEADER_FMT, data, 0)
    tag = data[_HEADER_SIZE : _HEADER_SIZE + tag_len].decode()
    return tag, data[_HEADER_SIZE + tag_len :]


class LegacyCheckpointSaver(BaseCheckpointSaver[int]):
    """Async checkpoint saver over the old tables, one short session per call."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        super().__init__(serde=JsonPlusSerializer())
        self._sm = sessionmaker

    def _dumps(self, obj: Any) -> bytes:
        return _pack(self.serde.dumps_typed(obj))

    def _loads(self, raw: bytes) -> Any:
        return self.serde.loads_typed(_unpack(raw))

    def _tuple(self, row: LegacyCheckpointRow, writes: list[LegacyWriteRow]) -> CheckpointTuple:
        checkpoint: Checkpoint = self._loads(row.checkpoint)
        metadata: CheckpointMetadata = self._loads(row.metadata)
        parent: RunnableConfig | None = None
        if row.parent_checkpoint_id is not None:
            parent = {
                "configurable": {
                    "thread_id": row.thread_id,
                    "checkpoint_ns": row.checkpoint_ns,
                    "checkpoint_id": row.parent_checkpoint_id,
                }
            }
        return CheckpointTuple(
            config={
                "configurable": {
                    "thread_id": row.thread_id,
                    "checkpoint_ns": row.checkpoint_ns,
                    "checkpoint_id": row.checkpoint_id,
                }
            },
            checkpoint=checkpoint,
            metadata=metadata,
            parent_config=parent,
            pending_writes=[(w.task_id, w.channel, self._loads(w.value)) for w in writes],
        )

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        configurable = config.get("configurable") or {}
        thread_id: str = configurable["thread_id"]
        ns: str = configurable.get("checkpoint_ns", "") or ""
        async with self._sm() as s:
            row = await checkpoints_repo.legacy_get(
                s, thread_id, ns, configurable.get("checkpoint_id")
            )
            if row is None:
                return None
            writes = await checkpoints_repo.legacy_writes(
                s, row.thread_id, row.checkpoint_ns, row.checkpoint_id
            )
        return self._tuple(row, writes)

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        if config is None or "configurable" not in config:
            raise ValueError("alist requires a config with configurable.thread_id")
        configurable = config["configurable"]
        thread_id: str = configurable["thread_id"]
        ns: str = configurable.get("checkpoint_ns", "") or ""
        pairs: list[tuple[LegacyCheckpointRow, list[LegacyWriteRow]]] = []
        async with self._sm() as s:
            before_id: str | None = None
            if before is not None and "configurable" in before:
                candidate = before["configurable"].get("checkpoint_id")
                # An unknown `before` id imposes no bound (as the Prisma saver behaved).
                if candidate and await checkpoints_repo.legacy_get(s, thread_id, ns, candidate):
                    before_id = candidate
            rows = await checkpoints_repo.legacy_list(
                s, thread_id, ns, before_id=before_id, limit=limit
            )
            for row in rows:
                writes = await checkpoints_repo.legacy_writes(
                    s, row.thread_id, row.checkpoint_ns, row.checkpoint_id
                )
                pairs.append((row, writes))
        for row, writes in pairs:
            yield self._tuple(row, writes)

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: dict[str, str | int | float],
    ) -> RunnableConfig:
        configurable = config.get("configurable") or {}
        thread_id: str = configurable["thread_id"]
        ns: str = configurable.get("checkpoint_ns", "") or ""
        row = LegacyCheckpointRow(
            thread_id=thread_id,
            checkpoint_ns=ns,
            checkpoint_id=checkpoint["id"],
            parent_checkpoint_id=configurable.get("checkpoint_id"),
            checkpoint=self._dumps(checkpoint),
            metadata=self._dumps(metadata),
        )
        async with self._sm() as s:
            await checkpoints_repo.legacy_upsert_checkpoint(s, row)
            await s.commit()
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": ns,
                "checkpoint_id": checkpoint["id"],
            }
        }

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        configurable = config.get("configurable") or {}
        packed = [(i, ch, self._dumps(v)) for i, (ch, v) in enumerate(writes)]
        async with self._sm() as s:
            await checkpoints_repo.legacy_upsert_writes(
                s,
                configurable["thread_id"],
                configurable.get("checkpoint_ns", "") or "",
                configurable["checkpoint_id"],
                task_id,
                packed,
            )
            await s.commit()

    async def adelete_thread(self, thread_id: str) -> None:
        async with self._sm() as s:
            await checkpoints_repo.delete_threads(s, [thread_id])
            await s.commit()

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        raise NotImplementedError(_ASYNC_ONLY)

    def list(self, config: RunnableConfig | None, **_: Any) -> Any:
        raise NotImplementedError(_ASYNC_ONLY)

    def put(self, *_args: Any, **_kwargs: Any) -> Any:
        raise NotImplementedError(_ASYNC_ONLY)

    def put_writes(self, *_args: Any, **_kwargs: Any) -> None:
        raise NotImplementedError(_ASYNC_ONLY)


__all__ = ["LegacyCheckpointSaver"]
