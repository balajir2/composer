"""``LangGraphExecutor.resume``: copy-on-resume before the graph runs, and fail closed when no
checkpoint exists. No database: repos and the graph are fakes."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from src.checkpoints import sync
from src.db.repos import checkpoints as checkpoints_repo
from src.engine import langgraph_executor as lge_mod
from src.engine.langgraph_executor import LangGraphExecutor
from tests.unit.db_fakes import assert_no_network_inside_session
from tests.unit.execution_fakes import FakeExecutions, make_row


class _HeadSaver(MemorySaver):
    """A saver that holds a checkpoint for every thread unless ``head`` is None."""

    def __init__(self, events: list[str], head: object | None = object()) -> None:
        super().__init__()
        self.events = events
        self.head = head

    async def aget_tuple(self, config: Any) -> Any:
        self.events.append("net:saver:aget_tuple")
        return self.head


def _setup(
    monkeypatch: pytest.MonkeyPatch, *, head: object | None = object()
) -> tuple[LangGraphExecutor, list[str], FakeExecutions, MagicMock]:
    events: list[str] = []
    fake = FakeExecutions(
        monkeypatch, make_row(workflow_id="wf1", user_id="dev", input=None), events=events
    )
    monkeypatch.setattr(
        "src.db.repos.workflows.get",
        AsyncMock(
            return_value=SimpleNamespace(
                id="wf1",
                name="n",
                nodes=[
                    {
                        "id": "s",
                        "type": "start",
                        "position": {"x": 0, "y": 0},
                        "data": {"label": "n"},
                    },
                    {
                        "id": "e",
                        "type": "end",
                        "position": {"x": 1, "y": 0},
                        "data": {"label": "n"},
                    },
                ],
                edges=[{"id": "e1", "source": "s", "target": "e"}],
            )
        ),
    )

    async def _ainvoke(*_a: Any, **_k: Any) -> dict[str, Any]:
        events.append("net:graph:ainvoke")
        return {"variables": {"lastOutput": "x"}, "node_results": {}}

    async def _aget_state(*_a: Any, **_k: Any) -> Any:
        return SimpleNamespace(next=(), tasks=())

    compiled = SimpleNamespace(ainvoke=_ainvoke, aget_state=_aget_state)

    def _build(*_a: Any, **_k: Any) -> Any:
        return compiled

    monkeypatch.setattr(lge_mod, "build_graph", _build)
    monkeypatch.setattr(lge_mod, "notify_execution_event", AsyncMock())

    async def _bring(*_a: Any, **_k: Any) -> bool:
        events.append("net:bring_forward")
        return False

    bring = AsyncMock(side_effect=_bring)
    monkeypatch.setattr(lge_mod, "bring_forward", bring)
    executor = LangGraphExecutor(
        sessionmaker=fake.sessionmaker, checkpointer=_HeadSaver(events, head)
    )
    return executor, events, fake, bring


async def test_resume_brings_the_thread_forward_before_the_graph_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, events, _fake, bring = _setup(monkeypatch)
    await executor.resume("ex1", "approved")

    bring.assert_awaited_once()
    assert bring.await_args is not None
    assert bring.await_args.args[0] is executor.checkpointer
    assert bring.await_args.args[1] is executor.sessionmaker
    assert bring.await_args.args[2] == "t1"
    assert events.index("net:bring_forward") < events.index("net:saver:aget_tuple")
    assert events.index("net:saver:aget_tuple") < events.index("net:graph:ainvoke")
    # No session is open around the saver calls or the graph run (short-session rule).
    assert_no_network_inside_session(events)
    assert any(u["status"] == "completed" for u in _fake.updates)


async def test_resume_without_a_checkpoint_fails_closed_and_never_runs_the_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, events, fake, _bring = _setup(monkeypatch, head=None)
    await executor.resume("ex1", "approved")

    assert "net:graph:ainvoke" not in events
    assert [u["status"] for u in fake.updates] == ["failed"]
    assert fake.updates[0]["failure_code"] == "checkpoint_missing"
    assert "ex1" in fake.updates[0]["error"]
    assert_no_network_inside_session(events)


async def test_missing_checkpoint_log_carries_the_id_only(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    executor, _events, _fake, _bring = _setup(monkeypatch, head=None)
    with caplog.at_level("INFO", logger="src.engine.langgraph_executor"):
        await executor.resume("ex1", "approved")
    assert any("no checkpoint to resume" in r.getMessage() for r in caplog.records)
    assert not any(r.exc_info for r in caplog.records)


async def test_a_failing_bring_forward_fails_the_run_with_its_own_code(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    executor, events, fake, bring = _setup(monkeypatch)
    bring.side_effect = RuntimeError("postgresql://u:secret@host/db copy broke")
    with caplog.at_level("INFO", logger="src.engine.langgraph_executor"):
        await executor.resume("ex1", "approved")
    assert "net:graph:ainvoke" not in events
    assert [u["status"] for u in fake.updates] == ["failed"]
    assert fake.updates[0]["failure_code"] == "checkpoint_sync_failed"
    # Log: execution id and exception class only, never the message or a traceback.
    assert any("ex1" in r.getMessage() and "RuntimeError" in r.getMessage() for r in caplog.records)
    assert not any("secret" in r.getMessage() or r.exc_info for r in caplog.records)


async def test_a_failing_checkpoint_probe_fails_the_run_with_its_own_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, events, fake, _bring = _setup(monkeypatch)

    async def _boom(*_a: Any, **_k: Any) -> Any:
        raise OSError("connection reset")

    monkeypatch.setattr(executor.checkpointer, "aget_tuple", _boom)
    await executor.resume("ex1", "approved")
    assert "net:graph:ainvoke" not in events
    assert [u["status"] for u in fake.updates] == ["failed"]
    assert fake.updates[0]["failure_code"] == "checkpoint_sync_failed"


async def test_checkpoint_failures_pass_the_attempt_id_to_the_fenced_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: without attempt_id the fenced failure refused and the run stayed running."""
    for head, bring_error, code in (
        (None, None, "checkpoint_missing"),
        (object(), RuntimeError("copy broke"), "checkpoint_sync_failed"),
    ):
        executor, _events, _fake, bring = _setup(monkeypatch, head=head)
        bring.side_effect = bring_error
        mark_failed = AsyncMock(wraps=executor._mark_failed)  # pyright: ignore[reportPrivateUsage]
        monkeypatch.setattr(executor, "_mark_failed", mark_failed)

        await executor.resume("ex1", "approved", attempt_id="attempt-1")

        mark_failed.assert_awaited_once()
        assert mark_failed.await_args is not None
        assert mark_failed.await_args.kwargs == {"failure_code": code, "attempt_id": "attempt-1"}


# --------------------------------------------------------------------------- bring_forward


async def test_bring_forward_ignores_savers_that_are_not_the_official_one() -> None:
    assert await sync.bring_forward(MemorySaver(), cast("Any", MagicMock()), "t1") is False


def _official() -> AsyncPostgresSaver:
    # The constructor only stores the connection and needs a running loop.
    return AsyncPostgresSaver(MagicMock())


class _Sessions:
    """A sessionmaker that records open/close so copies can be checked to run outside sessions."""

    def __init__(self, events: list[str]) -> None:
        self.events = events

    def __call__(self) -> "_Sessions":
        return self

    async def __aenter__(self) -> object:
        self.events.append("session:enter")
        return object()

    async def __aexit__(self, *_exc: object) -> bool:
        self.events.append("session:exit")
        return False


async def test_bring_forward_copies_only_the_namespaces_the_old_tables_lead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    seen: list[tuple[str, list[str]]] = []

    async def _ahead(_session: object, thread_id: str) -> list[str]:
        events.append("db:ahead")
        return ["", "sub"] if thread_id == "t1" else []

    async def _copy(
        _src: object, _dst: object, thread_id: str, *, to: str, namespaces: list[str]
    ) -> Any:
        events.append("net:copy")
        seen.append((to, namespaces))
        return SimpleNamespace(checkpoints=2, writes=1)

    monkeypatch.setattr(checkpoints_repo, "legacy_ahead_namespaces", _ahead)
    monkeypatch.setattr(sync, "copy_thread", _copy)
    saver = _official()

    assert await sync.bring_forward(saver, cast("Any", _Sessions(events)), "t1") is True
    assert seen == [("postgres", ["", "sub"])]
    assert_no_network_inside_session(events)


async def test_bring_forward_does_nothing_when_the_new_tables_are_ahead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    copy = AsyncMock()
    monkeypatch.setattr(checkpoints_repo, "legacy_ahead_namespaces", AsyncMock(return_value=[]))
    monkeypatch.setattr(sync, "copy_thread", copy)

    assert await sync.bring_forward(_official(), cast("Any", _Sessions(events)), "t1") is False
    copy.assert_not_awaited()


async def test_bring_forward_logs_ids_only(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(checkpoints_repo, "legacy_ahead_namespaces", AsyncMock(return_value=[""]))
    monkeypatch.setattr(
        sync, "copy_thread", AsyncMock(return_value=SimpleNamespace(checkpoints=1, writes=0))
    )
    with caplog.at_level("INFO", logger="src.checkpoints.sync"):
        await sync.bring_forward(_official(), cast("Any", _Sessions([])), "t-77")
    messages = [r.getMessage() for r in caplog.records]
    assert any("brought thread forward from the old tables" in m and "t-77" in m for m in messages)
