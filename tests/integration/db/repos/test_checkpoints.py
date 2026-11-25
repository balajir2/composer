import secrets

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import LangGraphCheckpoint, LangGraphCheckpointWrite
from src.db.repos import checkpoints as repo

pytestmark = pytest.mark.integration


async def _seed(session: AsyncSession, thread_id: str) -> None:
    session.add(
        LangGraphCheckpoint(
            thread_id=thread_id,
            checkpoint_ns="",
            checkpoint_id=secrets.token_hex(4),
            checkpoint=b"c",
            metadata_=b"m",
        )
    )
    session.add(
        LangGraphCheckpointWrite(
            thread_id=thread_id,
            checkpoint_ns="",
            checkpoint_id=secrets.token_hex(4),
            task_id="t",
            idx=0,
            channel="ch",
            value=b"v",
        )
    )
    await session.flush()


async def _counts(session: AsyncSession, thread_id: str) -> tuple[int, int]:
    a = await session.scalar(
        select(func.count())
        .select_from(LangGraphCheckpoint)
        .where(LangGraphCheckpoint.thread_id == thread_id)
    )
    b = await session.scalar(
        select(func.count())
        .select_from(LangGraphCheckpointWrite)
        .where(LangGraphCheckpointWrite.thread_id == thread_id)
    )
    return a or 0, b or 0


async def test_delete_threads_removes_both_tables_for_exactly_those_threads(
    session: AsyncSession,
) -> None:
    t1, t2, keep = (f"t-{secrets.token_hex(4)}" for _ in range(3))
    for t in (t1, t2, keep):
        await _seed(session, t)
    await repo.delete_threads(session, [t1, t2])
    assert await _counts(session, t1) == (0, 0)
    assert await _counts(session, t2) == (0, 0)
    assert await _counts(session, keep) == (1, 1)


async def test_delete_threads_with_no_threads_is_a_noop(session: AsyncSession) -> None:
    keep = f"t-{secrets.token_hex(4)}"
    await _seed(session, keep)
    await repo.delete_threads(session, [])
    assert await _counts(session, keep) == (1, 1)


async def test_delete_threads_accepts_more_thread_ids_than_postgres_has_parameters(
    session: AsyncSession,
) -> None:
    keep, gone = (f"t-{secrets.token_hex(4)}" for _ in range(2))
    await _seed(session, keep)
    await _seed(session, gone)
    await repo.delete_threads(session, [f"absent-{i}" for i in range(70_000)] + [gone])
    assert await _counts(session, gone) == (0, 0)
    assert await _counts(session, keep) == (1, 1)


# --- legacy_* functions (LegacyCheckpointSaver's SQL) ---


def _row(
    thread: str, cid: str, parent: str | None = None, ns: str = ""
) -> repo.LegacyCheckpointRow:
    return repo.LegacyCheckpointRow(
        thread_id=thread,
        checkpoint_ns=ns,
        checkpoint_id=cid,
        parent_checkpoint_id=parent,
        checkpoint=b"c-" + cid.encode(),
        metadata=b"m-" + cid.encode(),
    )


async def test_legacy_upsert_get_and_replace(session: AsyncSession) -> None:
    t = f"t-{secrets.token_hex(4)}"
    await repo.legacy_upsert_checkpoint(session, _row(t, "a"))
    got = await repo.legacy_get(session, t, "", "a")
    assert got == _row(t, "a")
    await repo.legacy_upsert_checkpoint(
        session,
        repo.LegacyCheckpointRow(t, "", "a", "p", b"new", b"newm"),
    )
    got = await repo.legacy_get(session, t, "", "a")
    assert got == repo.LegacyCheckpointRow(t, "", "a", "p", b"new", b"newm")
    assert await repo.legacy_get(session, t, "", "zzz") is None
    assert await repo.legacy_get(session, t, "other-ns", "a") is None


async def test_legacy_get_without_id_is_newest_by_checkpoint_id(session: AsyncSession) -> None:
    t = f"t-{secrets.token_hex(4)}"
    await repo.legacy_upsert_checkpoint(session, _row(t, "b"))
    await repo.legacy_upsert_checkpoint(session, _row(t, "a"))
    await session.execute(
        text("UPDATE langgraph_checkpoints SET created_at = now() WHERE thread_id = :t"),
        {"t": t},
    )
    latest = await repo.legacy_get(session, t, "", None)
    assert latest is not None and latest.checkpoint_id == "b"


def _ids(rows: list[repo.LegacyCheckpointRow]) -> list[str]:
    return [r.checkpoint_id for r in rows]


async def test_legacy_list_order_before_limit_and_namespaces(session: AsyncSession) -> None:
    t = f"t-{secrets.token_hex(4)}"
    for cid in ("a", "b", "c"):
        await repo.legacy_upsert_checkpoint(session, _row(t, cid))
    await repo.legacy_upsert_checkpoint(session, _row(t, "z", ns="sub"))
    assert _ids(await repo.legacy_list(session, t, "", before_id=None, limit=None)) == [
        "c",
        "b",
        "a",
    ]
    assert _ids(await repo.legacy_list(session, t, "", before_id="c", limit=None)) == ["b", "a"]
    assert _ids(await repo.legacy_list(session, t, "", before_id=None, limit=1)) == ["c"]
    assert await repo.legacy_namespaces(session, t) == ["", "sub"]
    assert await repo.legacy_namespaces(session, "absent") == []


async def test_legacy_writes_upsert_and_order(session: AsyncSession) -> None:
    t = f"t-{secrets.token_hex(4)}"
    await repo.legacy_upsert_writes(session, t, "", "c1", "tb", [(1, "x", b"1"), (0, "y", b"0")])
    await repo.legacy_upsert_writes(session, t, "", "c1", "ta", [(0, "z", b"2")])
    await repo.legacy_upsert_writes(session, t, "", "c1", "tb", [(0, "y2", b"9")])
    got = await repo.legacy_writes(session, t, "", "c1")
    assert got == [
        repo.LegacyWriteRow("ta", 0, "z", b"2"),
        repo.LegacyWriteRow("tb", 0, "y2", b"9"),
        repo.LegacyWriteRow("tb", 1, "x", b"1"),
    ]
    assert await repo.legacy_writes(session, t, "", "other") == []
    await repo.legacy_upsert_writes(session, t, "", "c1", "tc", [])  # no-op
