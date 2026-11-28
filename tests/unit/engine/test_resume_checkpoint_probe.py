"""``LangGraphExecutor.resume``: fail closed when no checkpoint exists or the probe read fails.
No database: repos and the graph are fakes."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from langgraph.checkpoint.memory import MemorySaver

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
) -> tuple[LangGraphExecutor, list[str], FakeExecutions]:
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

    executor = LangGraphExecutor(
        sessionmaker=fake.sessionmaker, checkpointer=_HeadSaver(events, head)
    )
    return executor, events, fake


async def test_resume_without_a_checkpoint_fails_closed_and_never_runs_the_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, events, fake = _setup(monkeypatch, head=None)
    await executor.resume("ex1", "approved")

    assert "net:graph:ainvoke" not in events
    assert [u["status"] for u in fake.updates] == ["failed"]
    assert fake.updates[0]["failure_code"] == "checkpoint_missing"
    assert "ex1" in fake.updates[0]["error"]
    assert_no_network_inside_session(events)


async def test_missing_checkpoint_log_carries_the_id_only(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    executor, _events, _fake = _setup(monkeypatch, head=None)
    with caplog.at_level("INFO", logger="src.engine.langgraph_executor"):
        await executor.resume("ex1", "approved")
    assert any("no checkpoint to resume" in r.getMessage() for r in caplog.records)
    assert not any(r.exc_info for r in caplog.records)


async def test_a_failing_checkpoint_probe_fails_the_run_with_its_own_code(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    executor, events, fake = _setup(monkeypatch)

    async def _boom(*_a: Any, **_k: Any) -> Any:
        raise OSError("connection reset")

    monkeypatch.setattr(executor.checkpointer, "aget_tuple", _boom)
    with caplog.at_level("INFO", logger="src.engine.langgraph_executor"):
        await executor.resume("ex1", "approved")
    assert "net:graph:ainvoke" not in events
    assert [u["status"] for u in fake.updates] == ["failed"]
    assert fake.updates[0]["failure_code"] == "checkpoint_sync_failed"
    # Log: execution id and exception class only, never the message or a traceback.
    assert any("ex1" in r.getMessage() and "OSError" in r.getMessage() for r in caplog.records)
    assert not any("connection reset" in r.getMessage() or r.exc_info for r in caplog.records)


async def test_checkpoint_failures_pass_the_attempt_id_to_the_fenced_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: without attempt_id the fenced failure refused and the run stayed running."""
    for head, probe_error, code in (
        (None, None, "checkpoint_missing"),
        (object(), OSError("connection reset"), "checkpoint_sync_failed"),
    ):
        executor, _events, _fake = _setup(monkeypatch, head=head)
        if probe_error is not None:

            async def _boom(*_a: Any, _err: Exception = probe_error, **_k: Any) -> Any:
                raise _err

            monkeypatch.setattr(executor.checkpointer, "aget_tuple", _boom)
        mark_failed = AsyncMock(wraps=executor._mark_failed)  # pyright: ignore[reportPrivateUsage]
        monkeypatch.setattr(executor, "_mark_failed", mark_failed)

        await executor.resume("ex1", "approved", attempt_id="attempt-1")

        mark_failed.assert_awaited_once()
        assert mark_failed.await_args is not None
        assert mark_failed.await_args.kwargs == {"failure_code": code, "attempt_id": "attempt-1"}


def test_resume_no_longer_imports_the_old_table_bridge() -> None:
    assert not hasattr(lge_mod, "bring_forward")
    assert not hasattr(lge_mod, "CheckpointSyncFailed")
    assert hasattr(lge_mod, "CheckpointReadFailed")
