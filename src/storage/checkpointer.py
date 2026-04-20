"""Prisma-backed LangGraph checkpoint saver.

Implements the async subset of BaseCheckpointSaver against Composer's
four Prisma tables: LangGraphCheckpoint and LangGraphCheckpointWrite.
See ADR-0001.

Invariant the schema can't express (Prisma doesn't support composite-PK
FKs in schema.prisma): writes must be deleted BEFORE their parent
checkpoints. `adelete_thread()` enforces this; any future deletion path
MUST route through it or replicate the ordering explicitly.

Serialization note
------------------
JsonPlusSerializer implements SerializerProtocol's `dumps_typed` /
`loads_typed` interface (returns / takes `tuple[str, bytes]`).  The
Prisma schema stores a single `Bytes` column per value, so we encode
the (type_tag, payload) pair with a 4-byte big-endian length prefix:

    <4 bytes: len(type_tag_utf8)> | <type_tag_utf8> | <payload_bytes>

Prisma's Python client uses `Base64` objects to represent `Bytes` columns.
When reading rows from the real Prisma client the `Bytes` fields arrive as
`Base64` instances; `_loads` converts them to raw bytes via `.decode()`.
When writing we pass raw `bytes` directly — Prisma's `_validate` coerces
them into `Base64` transparently, so `# pyright: ignore[reportArgumentType]`
on the upsert calls is correct and safe.

The integration test in Task 17 exercises the full Postgres round-trip.
"""

import struct
from collections.abc import AsyncIterator, Sequence
from typing import Any, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from prisma import Base64, Prisma  # pyright: ignore[reportAttributeAccessIssue]

# Header: 4-byte big-endian unsigned int storing the byte length of the type tag.
_HEADER_FMT = ">I"
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)


def _pack(typed: tuple[str, bytes]) -> bytes:
    """Pack a (type_tag, payload) pair into a single bytes value."""
    type_tag, payload = typed
    tag_bytes = type_tag.encode()
    header = struct.pack(_HEADER_FMT, len(tag_bytes))
    return header + tag_bytes + payload


def _unpack(data: bytes) -> tuple[str, bytes]:
    """Unpack a bytes value produced by `_pack` back into (type_tag, payload)."""
    (tag_len,) = struct.unpack_from(_HEADER_FMT, data, 0)
    tag_bytes = data[_HEADER_SIZE : _HEADER_SIZE + tag_len]
    payload = data[_HEADER_SIZE + tag_len :]
    return tag_bytes.decode(), payload


def _to_raw_bytes(value: Any) -> bytes:
    """Convert a Prisma Base64 field value to raw bytes.

    The real Prisma client returns ``Base64`` objects for ``Bytes`` columns.
    Mocks (and any path that bypasses Prisma) may hand us plain ``bytes``.
    Either way we want the underlying binary data. Typed as Any because
    Prisma's generated ``Base64`` class has no static stub; the runtime
    ``isinstance`` check keeps it honest.
    """
    if isinstance(value, Base64):
        return cast("bytes", value.decode())
    return cast("bytes", value)


class PrismaCheckpointSaver(BaseCheckpointSaver[str]):  # pyright: ignore[reportMissingTypeArgument]
    """LangGraph checkpoint saver backed by Prisma-managed Postgres tables."""

    def __init__(self, db: Prisma) -> None:  # pyright: ignore[reportUnknownParameterType]
        super().__init__(serde=JsonPlusSerializer())
        self.db = db

    # ─── serialization helpers ───────────────────────────────────────────

    def _dumps(self, obj: Any) -> bytes:
        """Serialize *obj* to bytes using the typed serializer."""
        return _pack(self.serde.dumps_typed(obj))

    def _loads(self, raw: Any) -> Any:
        """Deserialize a Prisma Bytes field value serialized by `_dumps`.

        Typed as Any for the same reason as `_to_raw_bytes` above — Prisma's
        Base64 class has no static stub. `_to_raw_bytes` narrows at runtime.
        """
        return self.serde.loads_typed(_unpack(_to_raw_bytes(raw)))

    # ─── required async methods ─────────────────────────────────────────

    # NOTE(phase-6): aget_tuple without a checkpoint_id runs a
    # find_first(where={threadId, checkpointNs}, order={"createdAt": "desc"})
    # query. The schema currently has @@index([threadId]) only, which narrows
    # by thread and then scans for the namespace. Fine at Phase 1 scale;
    # when LangGraph subgraphs (multi-namespace) land and this becomes the
    # hot path, consider a follow-up migration adding
    # @@index([threadId, checkpointNs]) and benchmarking. Defer to measurement.
    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        configurable = config.get("configurable") or {}
        thread_id: str = configurable["thread_id"]
        checkpoint_ns: str = configurable.get("checkpoint_ns", "") or ""
        checkpoint_id: str | None = configurable.get("checkpoint_id")

        if checkpoint_id:
            row = await self.db.langgraphcheckpoint.find_unique(
                where={
                    "threadId_checkpointNs_checkpointId": {
                        "threadId": thread_id,
                        "checkpointNs": checkpoint_ns,
                        "checkpointId": checkpoint_id,
                    }
                }
            )
        else:
            row = await self.db.langgraphcheckpoint.find_first(
                where={"threadId": thread_id, "checkpointNs": checkpoint_ns},
                order={"createdAt": "desc"},
            )

        if row is None:
            return None

        writes = await self.db.langgraphcheckpointwrite.find_many(
            where={  # pyright: ignore[reportArgumentType]
                "threadId": row.threadId,
                "checkpointNs": row.checkpointNs,
                "checkpointId": row.checkpointId,
            },
            order=[{"taskId": "asc"}, {"idx": "asc"}],
        )

        checkpoint: Checkpoint = self._loads(row.checkpoint)
        metadata: CheckpointMetadata = self._loads(row.metadata)
        pending_writes = [(w.taskId, w.channel, self._loads(w.value)) for w in writes]

        parent_config: RunnableConfig | None = None
        if row.parentCheckpointId is not None:
            parent_config = {
                "configurable": {
                    "thread_id": row.threadId,
                    "checkpoint_ns": row.checkpointNs,
                    "checkpoint_id": row.parentCheckpointId,
                }
            }

        out_config: RunnableConfig = {
            "configurable": {
                "thread_id": row.threadId,
                "checkpoint_ns": row.checkpointNs,
                "checkpoint_id": row.checkpointId,
            }
        }
        return CheckpointTuple(
            config=out_config,
            checkpoint=checkpoint,
            metadata=metadata,
            parent_config=parent_config,
            pending_writes=pending_writes,
        )

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:  # pyright: ignore[reportReturnType]
        if config is None or "configurable" not in config:
            raise ValueError("alist requires a config with configurable.thread_id")

        configurable = config["configurable"]
        thread_id: str = configurable["thread_id"]
        checkpoint_ns: str = configurable.get("checkpoint_ns", "") or ""

        where: dict[str, Any] = {"threadId": thread_id, "checkpointNs": checkpoint_ns}
        if before is not None and "configurable" in before:
            before_id: str | None = before["configurable"].get("checkpoint_id")
            if before_id:
                before_row = await self.db.langgraphcheckpoint.find_unique(
                    where={
                        "threadId_checkpointNs_checkpointId": {
                            "threadId": thread_id,
                            "checkpointNs": checkpoint_ns,
                            "checkpointId": before_id,
                        }
                    }
                )
                if before_row is not None:
                    where["createdAt"] = {"lt": before_row.createdAt}

        rows = await self.db.langgraphcheckpoint.find_many(
            where=where,  # pyright: ignore[reportArgumentType]
            order={"createdAt": "desc"},
            take=limit,
        )
        for row in rows:
            tup = await self.aget_tuple(
                {
                    "configurable": {
                        "thread_id": row.threadId,
                        "checkpoint_ns": row.checkpointNs,
                        "checkpoint_id": row.checkpointId,
                    }
                }
            )
            if tup is not None:
                yield tup

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: dict[str, str | int | float],
    ) -> RunnableConfig:
        configurable = config.get("configurable") or {}
        thread_id: str = configurable["thread_id"]
        checkpoint_ns: str = configurable.get("checkpoint_ns", "") or ""
        checkpoint_id: str = checkpoint["id"]
        parent_checkpoint_id: str | None = configurable.get("checkpoint_id")

        serialized_checkpoint = Base64.encode(self._dumps(checkpoint))
        serialized_metadata = Base64.encode(self._dumps(metadata))

        await self.db.langgraphcheckpoint.upsert(
            where={
                "threadId_checkpointNs_checkpointId": {
                    "threadId": thread_id,
                    "checkpointNs": checkpoint_ns,
                    "checkpointId": checkpoint_id,
                }
            },
            data={  # pyright: ignore[reportArgumentType]
                "create": {
                    "threadId": thread_id,
                    "checkpointNs": checkpoint_ns,
                    "checkpointId": checkpoint_id,
                    "parentCheckpointId": parent_checkpoint_id,
                    "checkpoint": serialized_checkpoint,
                    "metadata": serialized_metadata,
                },
                "update": {
                    "parentCheckpointId": parent_checkpoint_id,
                    "checkpoint": serialized_checkpoint,
                    "metadata": serialized_metadata,
                },
            },
        )

        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
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
        thread_id: str = configurable["thread_id"]
        checkpoint_ns: str = configurable.get("checkpoint_ns", "") or ""
        checkpoint_id: str = configurable["checkpoint_id"]

        for idx, (channel, value) in enumerate(writes):
            serialized_value = Base64.encode(self._dumps(value))
            await self.db.langgraphcheckpointwrite.upsert(
                where={
                    "threadId_checkpointNs_checkpointId_taskId_idx": {
                        "threadId": thread_id,
                        "checkpointNs": checkpoint_ns,
                        "checkpointId": checkpoint_id,
                        "taskId": task_id,
                        "idx": idx,
                    }
                },
                data={  # pyright: ignore[reportArgumentType]
                    "create": {
                        "threadId": thread_id,
                        "checkpointNs": checkpoint_ns,
                        "checkpointId": checkpoint_id,
                        "taskId": task_id,
                        "idx": idx,
                        "channel": channel,
                        "value": serialized_value,
                    },
                    "update": {
                        "channel": channel,
                        "value": serialized_value,
                    },
                },
            )

    # ─── deletion (ordering enforced here; Phase 1 has no other delete path) ───

    async def adelete_thread(self, thread_id: str) -> None:
        """Delete all checkpoint rows for a thread. Writes BEFORE parents.

        The schema has no FK from LangGraphCheckpointWrite to LangGraphCheckpoint
        (Prisma can't express composite-PK FKs in schema.prisma), so writes would
        orphan on a naive delete of checkpoints. This helper enforces the order.
        """
        await self.db.langgraphcheckpointwrite.delete_many(where={"threadId": thread_id})
        await self.db.langgraphcheckpoint.delete_many(where={"threadId": thread_id})

    # ─── sync shims (async-only call sites; fail loudly if called sync) ───

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        raise NotImplementedError("Use aget_tuple (async)")

    def list(self, config: RunnableConfig | None, **_: Any) -> Any:
        raise NotImplementedError("Use alist (async)")

    def put(self, *_args: Any, **_kwargs: Any) -> Any:
        raise NotImplementedError("Use aput (async)")

    def put_writes(self, *_args: Any, **_kwargs: Any) -> None:
        raise NotImplementedError("Use aput_writes (async)")


__all__ = ["PrismaCheckpointSaver"]
