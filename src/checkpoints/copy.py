"""Copy one thread's checkpoints between the old (legacy) and official savers, either way.

Built on the public saver interface (``alist`` / ``aput`` / ``aput_writes``), with channel
versions converted to the target's shape (see ``versions``). Idempotent: re-copying changes
nothing (both savers upsert checkpoints; the official saver ignores a repeated ordinary write,
the legacy one rewrites the same bytes). The source is only ever read. Never logs values.

Pending writes: ``CheckpointTuple.pending_writes`` carries ``(task_id, channel, value)`` only,
so per-task grouping and order are rebuilt from the tuple order (legacy: ``task_id, idx``;
official: ``task_path, task_id, idx`` with special channels at negative idx, so first). Each
task's writes are replayed as two batches, special channels (``WRITES_IDX_MAP``: error,
scheduled, interrupt, resume) and ordinary ones, as LangGraph itself writes them separately:
the official saver upserts an all-special batch at the fixed negative idx and inserts ordinary
rows at ``idx = position in batch`` (first write wins), the legacy saver stores
``idx = position in batch``. The ``task_path`` column is not exposed by the interface; copies
into the official saver store ``''`` (it only affects the order pending writes are listed in).
"""

from dataclasses import dataclass
from typing import Any

from langgraph.checkpoint.base import WRITES_IDX_MAP, BaseCheckpointSaver

from src.checkpoints.versions import Target, convert_checkpoint


@dataclass(frozen=True)
class CopyReport:
    thread_id: str
    checkpoints: int
    writes: int


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
        for t in reversed(history):  # oldest first: parents land before children
            cp = convert_checkpoint(t.checkpoint, to)
            parent = (t.parent_config or {}).get("configurable", {}).get("checkpoint_id")
            put_cfg: Any = {
                "configurable": {"thread_id": thread_id, "checkpoint_ns": ns}
                | ({"checkpoint_id": parent} if parent else {})
            }
            # Every channel version is "new": the official saver writes one blob per
            # (channel, version) and ignores ones that exist, so this fills any gap.
            await target.aput(put_cfg, cp, t.metadata, dict(cp["channel_versions"]))
            checkpoints += 1

            by_task: dict[str, list[tuple[str, Any]]] = {}
            for task_id, channel, value in t.pending_writes or []:
                by_task.setdefault(task_id, []).append((channel, value))
            write_cfg: Any = {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": ns,
                    "checkpoint_id": cp["id"],
                }
            }
            for task_id, task_writes in by_task.items():
                special = [w for w in task_writes if w[0] in WRITES_IDX_MAP]
                ordinary = [w for w in task_writes if w[0] not in WRITES_IDX_MAP]
                for batch in (special, ordinary):
                    if batch:
                        await target.aput_writes(write_cfg, batch, task_id)
                        writes += len(batch)
    return CopyReport(thread_id=thread_id, checkpoints=checkpoints, writes=writes)
