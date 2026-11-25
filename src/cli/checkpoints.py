"""`composer checkpoints` subcommand handlers: copy paused runs between the two savers.

Output carries ids and counts only, never checkpoint contents or exception messages.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from src.checkpoints.copy import copy_thread
from src.checkpoints.legacy import LegacyCheckpointSaver
from src.checkpoints.saver import open_one_shot_saver
from src.config import get_settings
from src.db.engine import build_engine
from src.db.repos import checkpoints as checkpoints_repo
from src.db.repos import executions as executions_repo
from src.db.session import build_sessionmaker

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from langgraph.checkpoint.base import BaseCheckpointSaver
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.checkpoints.versions import Target


@asynccontextmanager
async def _sessionmaker() -> AsyncGenerator[async_sessionmaker[AsyncSession]]:
    engine = build_engine(get_settings(), for_migrations=True)  # NullPool: one-shot
    try:
        yield build_sessionmaker(engine)
    finally:
        await engine.dispose()


async def _newest_ids(
    saver: BaseCheckpointSaver[Any], thread_id: str, namespaces: list[str]
) -> dict[str, str | None]:
    """Newest checkpoint id per namespace (ids are time ordered), None when there is none."""
    out: dict[str, str | None] = {}
    for ns in namespaces:
        cfg: Any = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ns}}
        tup = await saver.aget_tuple(cfg)
        out[ns] = (tup.config.get("configurable") or {})["checkpoint_id"] if tup else None
    return out


async def checkpoints_copy(
    to: Target, executions: list[str], all_waiting: bool, force: bool
) -> int:
    """Copy each target execution's thread.

    2 = refused (nothing copied), 1 = a copy failed or copied nothing. Only exception class names
    are printed: driver messages can carry host, database and user names.
    """
    try:
        return await _copy(to, executions, all_waiting, force)
    except Exception as exc:
        print(f"failed: {type(exc).__name__}")
        return 1


async def _copy(to: Target, executions: list[str], all_waiting: bool, force: bool) -> int:
    async with _sessionmaker() as sm:
        targets: list[tuple[str, str]] = []
        async with sm() as session:
            if all_waiting:
                targets.extend(await checkpoints_repo.waiting_threads(session))
            for eid in executions:
                record = await executions_repo.get(session, eid)
                if record is None:
                    print(f"refused {eid}: execution not found")
                    return 2
                if record.status != "waiting_approval" and not force:
                    print(f"refused {eid}: status is {record.status} (use --force)")
                    return 2
                targets.append((eid, record.thread_id))

        failed = False
        refused = False
        legacy = LegacyCheckpointSaver(sm)
        for eid, thread_id in dict.fromkeys(targets):
            try:
                async with sm() as session:
                    namespaces = (
                        await checkpoints_repo.legacy_namespaces(session, thread_id)
                        if to == "postgres"
                        else await checkpoints_repo.saver_namespaces(session, thread_id)
                    )
                async with open_one_shot_saver(get_settings()) as official:
                    source, target = (legacy, official) if to == "postgres" else (official, legacy)
                    if not force:
                        src_ids = await _newest_ids(source, thread_id, namespaces)
                        dst_ids = await _newest_ids(target, thread_id, namespaces)
                        if any(
                            dst is not None and (src_ids[ns] is None or dst > str(src_ids[ns]))
                            for ns, dst in dst_ids.items()
                        ):
                            print(f"refused {eid}: target is ahead of source (use --force)")
                            refused = True
                            continue
                    report = await copy_thread(
                        source, target, thread_id, to=to, namespaces=namespaces
                    )
                print(f"copied {eid}: {report.checkpoints} checkpoints, {report.writes} writes")
                if report.checkpoints == 0 and not force:
                    print(f"warning {eid}: nothing to copy, the source has no checkpoints")
                    failed = True
            except Exception as exc:
                failed = True
                print(f"failed {eid}: {type(exc).__name__}")
        if refused:
            return 2
        return 1 if failed else 0
