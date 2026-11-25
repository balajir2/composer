"""Checkpoint history through the ``BaseCheckpointSaver`` interface, and execution delete.

History is read only through ``app.state.checkpointer`` (``aget_tuple``, ``alist``, ``aput_writes``,
``adelete_thread``): the interface the executor and LangGraph rely on. Row counts are read with raw
SQL over whichever checkpoint tables exist (see conftest). Deleting an execution must remove the
checkpoint rows of its thread from EVERY checkpoint table, in the same step, and nothing else.
"""

from typing import Any

import pytest
from httpx import AsyncClient

from tests.integration.sqla_phase4a.conftest import HTTPX_MARK, Db, ExecApi, Member, jbody
from tests.integration.sqla_phase4b.conftest import resume
from tests.integration.sqla_phase5.conftest import (
    checkpoint_row_count,
    checkpoint_row_counts,
    start_paused,
)

pytestmark = [pytest.mark.integration, HTTPX_MARK]


def _cfg(thread: str, **extra: Any) -> Any:
    return {"configurable": {"thread_id": thread, "checkpoint_ns": "", **extra}}


async def _history(app: Any, thread: str, **kw: Any) -> list[Any]:
    return [t async for t in app.state.checkpointer.alist(_cfg(thread), **kw)]


async def test_history_is_newest_first_linked_and_the_head_carries_the_interrupt(
    app: Any, client: AsyncClient, db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    _eid, thread = await start_paused(client, db, member, monkeypatch)
    history = await _history(app, thread)
    # input (-1), the start node's step (0), the interrupted approval step (1)
    assert [t.metadata["step"] for t in history] == [1, 0, -1]
    assert [t.metadata["source"] for t in history] == ["loop", "loop", "input"]
    ids = [t.config["configurable"]["checkpoint_id"] for t in history]
    assert ids == sorted(ids, reverse=True)  # ids are time-ordered: newest first
    for t in history:
        assert t.config["configurable"]["thread_id"] == thread
        assert t.config["configurable"]["checkpoint_ns"] == ""
    for newer, older_id in zip(history[:-1], ids[1:], strict=True):
        assert newer.parent_config is not None
        assert newer.parent_config["configurable"]["checkpoint_id"] == older_id
        assert newer.parent_config["configurable"]["thread_id"] == thread
    assert history[-1].parent_config is None

    head = await app.state.checkpointer.aget_tuple(_cfg(thread))
    assert head is not None and head.config == history[0].config
    assert head.checkpoint["id"] == ids[0]
    interrupts = [w for w in head.pending_writes if w[1] == "__interrupt__"]
    assert len(interrupts) == 1  # the pause: this is what ``aget_state(...).next`` is built from
    (interrupt,) = interrupts[0][2]
    assert interrupt.value["node_id"] == "ua" and interrupt.value["prompt"] == "Please approve"
    # older entries do not carry it
    assert not [w for t in history[1:] for w in t.pending_writes if w[1] == "__interrupt__"]


async def test_aget_tuple_by_checkpoint_id_returns_that_checkpoint(
    app: Any, client: AsyncClient, db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    _eid, thread = await start_paused(client, db, member, monkeypatch)
    history = await _history(app, thread)
    for t in history:
        again = await app.state.checkpointer.aget_tuple(
            _cfg(thread, checkpoint_id=t.config["configurable"]["checkpoint_id"])
        )
        assert again is not None
        assert again.config == t.config and again.parent_config == t.parent_config
        assert again.checkpoint["id"] == t.checkpoint["id"]
        assert again.metadata["step"] == t.metadata["step"]
    missing = await app.state.checkpointer.aget_tuple(_cfg(thread, checkpoint_id="no-such-id"))
    assert missing is None


async def test_alist_limit_and_before(
    app: Any, client: AsyncClient, db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    _eid, thread = await start_paused(client, db, member, monkeypatch)
    history = await _history(app, thread)
    assert len(history) == 3
    assert [t.config for t in await _history(app, thread, limit=1)] == [history[0].config]
    assert [t.config for t in await _history(app, thread, limit=2)] == [
        h.config for h in history[:2]
    ]
    older = await _history(app, thread, before=history[0].config)
    assert [t.config for t in older] == [h.config for h in history[1:]]
    assert await _history(app, thread, before=history[-1].config) == []


async def test_history_is_per_thread_and_empty_for_an_unknown_thread(
    app: Any, client: AsyncClient, db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    _a, thread_a = await start_paused(client, db, member, monkeypatch)
    _b, thread_b = await start_paused(client, db, member, monkeypatch)
    ids_a = {t.config["configurable"]["checkpoint_id"] for t in await _history(app, thread_a)}
    ids_b = {t.config["configurable"]["checkpoint_id"] for t in await _history(app, thread_b)}
    assert ids_a and ids_b and not ids_a & ids_b
    assert await app.state.checkpointer.aget_tuple(_cfg("no-such-thread")) is None
    assert await _history(app, "no-such-thread") == []


async def test_pending_writes_are_idempotent_and_ordered_by_task_then_index(
    app: Any, client: AsyncClient, db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The executor re-writes a task's writes when a step is replayed: the same (task, index) must
    update in place, never duplicate."""
    _eid, thread = await start_paused(client, db, member, monkeypatch)
    saver = app.state.checkpointer
    head = await saver.aget_tuple(_cfg(thread))
    assert head is not None
    cfg = head.config
    before = list(head.pending_writes)
    await saver.aput_writes(cfg, [("p5_chan_a", "one"), ("p5_chan_b", "two")], "task-b")
    await saver.aput_writes(cfg, [("p5_chan_c", "three")], "task-a")
    await saver.aput_writes(cfg, [("p5_chan_a", "one-again"), ("p5_chan_b", "two")], "task-b")
    after = (await saver.aget_tuple(_cfg(thread))).pending_writes
    added = [w for w in after if w not in before]
    assert added == [
        ("task-a", "p5_chan_c", "three"),
        ("task-b", "p5_chan_a", "one-again"),
        ("task-b", "p5_chan_b", "two"),
    ]
    assert len(after) == len(before) + 3


async def test_resume_adds_history_and_keeps_the_old_entries(
    app: Any, client: AsyncClient, db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    eid, thread = await start_paused(client, db, member, monkeypatch)
    before = [t.config["configurable"]["checkpoint_id"] for t in await _history(app, thread)]
    assert (await resume(client, member, eid, "approved")).status_code == 200
    after = [t.config["configurable"]["checkpoint_id"] for t in await _history(app, thread)]
    assert after[-len(before) :] == before and len(after) > len(before)
    latest = await app.state.checkpointer.aget_tuple(_cfg(thread))
    assert latest is not None and latest.config["configurable"]["checkpoint_id"] == after[0]
    assert not [w for w in latest.pending_writes if w[1] == "__interrupt__"]  # nothing pending


async def test_adelete_thread_removes_that_thread_only(
    app: Any, client: AsyncClient, db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    _a, thread_a = await start_paused(client, db, member, monkeypatch)
    _b, thread_b = await start_paused(client, db, member, monkeypatch)
    by_before = await checkpoint_row_counts(db.sm, thread_b)
    assert await checkpoint_row_count(db.sm, thread_a) > 0
    await app.state.checkpointer.adelete_thread(thread_a)
    assert await app.state.checkpointer.aget_tuple(_cfg(thread_a)) is None
    assert await _history(app, thread_a) == []
    assert set((await checkpoint_row_counts(db.sm, thread_a)).values()) == {0}
    assert await checkpoint_row_counts(db.sm, thread_b) == by_before
    assert len(await _history(app, thread_b)) == 3


async def test_delete_removes_every_checkpoint_row_of_the_thread_and_nothing_else(
    app: Any,
    client: AsyncClient,
    exec_api: ExecApi,
    db: Db,
    member: Member,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    eid, thread = await start_paused(client, db, member, monkeypatch)
    other, other_thread = await start_paused(client, db, member, monkeypatch)
    assert (await resume(client, member, eid, "approved")).status_code == 200
    assert (await db.must(eid)).status == "completed"
    assert await checkpoint_row_count(db.sm, thread) > 0
    other_counts = await checkpoint_row_counts(db.sm, other_thread)
    other_head = await app.state.checkpointer.aget_tuple(_cfg(other_thread))
    other_row = db.snapshot(await db.must(other))
    other_events = await db.event_count([other])

    r = await exec_api.delete(member, eid)
    assert r.status_code == 204 and r.content == b""

    assert set((await checkpoint_row_counts(db.sm, thread)).values()) == {0}  # every table
    assert await app.state.checkpointer.aget_tuple(_cfg(thread)) is None
    assert await _history(app, thread) == []
    assert await db.execution(eid) is None
    assert await db.event_count([eid]) == 0
    # the bystander is untouched ...
    assert await checkpoint_row_counts(db.sm, other_thread) == other_counts
    assert db.snapshot(await db.must(other)) == other_row
    assert await db.event_count([other]) == other_events
    # ... and still resumes from its own checkpoint
    again = await app.state.checkpointer.aget_tuple(_cfg(other_thread))
    assert again is not None and again.config == other_head.config  # type: ignore[union-attr]
    assert (await resume(client, member, other, "approved")).status_code == 200
    assert (await db.must(other)).status == "completed"


async def test_delete_of_a_canceled_paused_run_removes_its_checkpoints(
    app: Any,
    client: AsyncClient,
    exec_api: ExecApi,
    db: Db,
    member: Member,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    eid, thread = await start_paused(client, db, member, monkeypatch)
    assert (await exec_api.cancel(member, eid)).status_code == 200
    assert await checkpoint_row_count(db.sm, thread) > 0  # cancel keeps the history
    assert (await exec_api.delete(member, eid)).status_code == 204
    assert set((await checkpoint_row_counts(db.sm, thread)).values()) == {0}
    assert await app.state.checkpointer.aget_tuple(_cfg(thread)) is None


async def test_bulk_delete_removes_exactly_the_requested_threads(
    app: Any, client: AsyncClient, db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    done: list[tuple[str, str]] = []
    for _ in range(2):
        eid, thread = await start_paused(client, db, member, monkeypatch)
        assert (await resume(client, member, eid, "approved")).status_code == 200
        done.append((eid, thread))
    keep, keep_thread = await start_paused(client, db, member, monkeypatch)  # still paused
    keep_counts = await checkpoint_row_counts(db.sm, keep_thread)
    for _eid, thread in done:
        assert await checkpoint_row_count(db.sm, thread) > 0

    r = await client.post(
        "/executions/delete-bulk",
        json={"executionIds": [e for e, _ in done] + [keep]},
        headers=member.headers,
    )
    assert r.status_code == 200, r.text
    assert jbody(r) == {"deletedCount": 2, "skippedCount": 1}  # the paused run is skipped
    for eid, thread in done:
        assert await db.execution(eid) is None
        assert set((await checkpoint_row_counts(db.sm, thread)).values()) == {0}
        assert await app.state.checkpointer.aget_tuple(_cfg(thread)) is None
    assert await checkpoint_row_counts(db.sm, keep_thread) == keep_counts
    assert (await db.must(keep)).status == "waiting_approval"


async def test_delete_of_an_active_run_is_refused_and_keeps_its_checkpoints(
    exec_api: ExecApi, client: AsyncClient, db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    eid, thread = await start_paused(client, db, member, monkeypatch)
    counts = await checkpoint_row_counts(db.sm, thread)
    r = await exec_api.delete(member, eid)
    assert r.status_code == 409, r.text
    assert await checkpoint_row_counts(db.sm, thread) == counts
    assert (await resume(client, member, eid, "approved")).status_code == 200  # still resumable
    assert (await db.must(eid)).status == "completed"
