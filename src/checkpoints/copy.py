"""Copy one thread's checkpoints between the old (legacy) and official savers, either way.

Built on the public saver interface (``alist`` / ``aput`` / ``aput_writes``), with channel
versions converted to the target's shape (see ``versions``). Idempotent: re-copying changes
nothing. The source is only ever read. Never logs values.

Lineage only: per namespace the copy follows ``parent_config`` from the newest checkpoint back to
the root and copies just that chain. Orphaned branches of a forked history are skipped, which
keeps blob versions from colliding across branches (the official saver keys a blob by
``(channel, version)`` only) and shrinks the window in which a crash can leave a partial copy.

Crash safety: for each checkpoint the pending writes are stored BEFORE the checkpoint row.
Neither table set has foreign keys, and readers find writes through the checkpoint row, so a copy
that dies midway leaves the target's newest checkpoint id behind the source's (the deploy guard
and copy-on-resume compare newest ids), never "caught up" without its pending writes. A re-copy
finishes the job.

Pending writes: ``CheckpointTuple.pending_writes`` carries ``(task_id, channel, value)`` only,
so per-task grouping and order are rebuilt from the tuple order (legacy: ``task_id, idx``;
official: ``task_path, task_id, idx`` with special channels at negative idx, so first).
- Official target: each task's writes go as two batches, special channels (``WRITES_IDX_MAP``:
  error, scheduled, interrupt, resume) and ordinary ones, as LangGraph writes them. The official
  saver upserts an all-special batch at the fixed negative idx and inserts ordinary rows at
  ``idx = position in batch`` (first write wins).
- Legacy target: ONE batch per task, special channels first, because the legacy saver numbers
  every ``aput_writes`` call from 0 (a second call would overwrite the first).
The ``task_path`` column is not exposed by the interface; copies into the official saver store
``''`` (it only affects the order pending writes are listed in).

Legacy upserts while the official saver keeps the first ordinary write; this is safe because
LangGraph never re-runs a task whose writes are already pending, so a task's rows do not change.
"""

from dataclasses import dataclass
from typing import Any

from langgraph.checkpoint.base import WRITES_IDX_MAP, BaseCheckpointSaver, CheckpointTuple
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.types import _DeltaSnapshot  # pyright: ignore[reportPrivateUsage]

from src.checkpoints.versions import Target, convert_checkpoint


@dataclass(frozen=True)
class CopyReport:
    thread_id: str
    checkpoints: int
    writes: int


def _lineage(history: list[CheckpointTuple]) -> list[CheckpointTuple]:
    """The newest checkpoint and its ancestors, oldest first (``history`` is newest first)."""
    if not history:
        return []
    by_id = {(t.config.get("configurable") or {})["checkpoint_id"]: t for t in history}
    chain: list[CheckpointTuple] = []
    current: CheckpointTuple | None = history[0]
    while current is not None:
        chain.append(current)
        parent = (current.parent_config or {}).get("configurable", {}).get("checkpoint_id")
        current = by_id.get(parent) if parent else None
    chain.reverse()
    return chain


def _write_batches(
    task_writes: list[tuple[str, Any]], target: BaseCheckpointSaver[Any]
) -> list[list[tuple[str, Any]]]:
    special = [w for w in task_writes if w[0] in WRITES_IDX_MAP]
    ordinary = [w for w in task_writes if w[0] not in WRITES_IDX_MAP]
    if isinstance(target, AsyncPostgresSaver):
        return [b for b in (special, ordinary) if b]
    return [special + ordinary]


async def copy_thread(
    source: BaseCheckpointSaver[Any],
    target: BaseCheckpointSaver[Any],
    thread_id: str,
    *,
    to: Target,
    namespaces: list[str],
) -> CopyReport:
    checkpoints = 0
    writes = 0
    for ns in namespaces:
        list_cfg: Any = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ns}}
        history = [t async for t in source.alist(list_cfg)]
        for t in _lineage(history):  # oldest first: parents land before children
            if any(isinstance(v, _DeltaSnapshot) for v in t.checkpoint["channel_values"].values()):
                raise NotImplementedError("delta channels are not supported by the copy tool")
            cp = convert_checkpoint(t.checkpoint, to)
            parent = (t.parent_config or {}).get("configurable", {}).get("checkpoint_id")
            put_cfg: Any = {
                "configurable": {"thread_id": thread_id, "checkpoint_ns": ns}
                | ({"checkpoint_id": parent} if parent else {})
            }
            write_cfg: Any = {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": ns,
                    "checkpoint_id": cp["id"],
                }
            }

            by_task: dict[str, list[tuple[str, Any]]] = {}
            for task_id, channel, value in t.pending_writes or []:
                by_task.setdefault(task_id, []).append((channel, value))
            # Writes first, checkpoint row last (see the module docstring).
            for task_id, task_writes in by_task.items():
                for batch in _write_batches(task_writes, target):
                    await target.aput_writes(write_cfg, batch, task_id)
                    writes += len(batch)

            # Every channel version is "new": the official saver writes one blob per
            # (channel, version) and ignores ones that exist, so this fills any gap.
            await target.aput(put_cfg, cp, t.metadata, dict(cp["channel_versions"]))
            checkpoints += 1
    return CopyReport(thread_id=thread_id, checkpoints=checkpoints, writes=writes)
