"""Unit tests for `_run_with_persistence` in src/api/run.py.

The detached `executor.run()` task is the only thing standing between a
created execution row and an eventually-persisted terminal status.  The
wrapper is its second-line defence: if the executor's own try/except is
bypassed (uncaught crash, task cancellation on shutdown), the wrapper
reaches directly to the DB and stamps `failed`.

These tests bypass FastAPI and the LangGraphExecutor altogether — they
exercise the wrapper with stub objects so the policy is observable in
isolation.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from src.api.run import _run_with_persistence  # pyright: ignore[reportPrivateUsage]


@dataclass
class _FakeExecTable:
    update_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def update(self, *, where: dict[str, Any], data: dict[str, Any]) -> None:
        self.update_calls.append((where["id"], data))


@dataclass
class _FakeDb:
    workflowexecution: _FakeExecTable = field(default_factory=_FakeExecTable)


class _SuccessExecutor:
    async def run(self, execution_id: str) -> None:
        return None


class _CrashingExecutor:
    async def run(self, execution_id: str) -> None:
        raise RuntimeError("simulated executor crash")


class _CanceledExecutor:
    async def run(self, execution_id: str) -> None:
        raise asyncio.CancelledError


async def test_normal_completion_does_not_touch_db() -> None:
    """Happy path — executor.run() returns clean → wrapper does nothing."""
    db = _FakeDb()
    await _run_with_persistence(_SuccessExecutor(), db, "exe1")  # type: ignore[arg-type]
    assert db.workflowexecution.update_calls == []


async def test_uncaught_executor_crash_marks_row_failed() -> None:
    """If executor.run() raises, the wrapper persists 'failed' before re-raising."""
    db = _FakeDb()
    with pytest.raises(RuntimeError, match="simulated executor crash"):
        await _run_with_persistence(_CrashingExecutor(), db, "exe1")  # type: ignore[arg-type]
    [(exec_id, data)] = db.workflowexecution.update_calls
    assert exec_id == "exe1"
    assert data["status"] == "failed"
    assert "RuntimeError" in data["error"]
    assert "simulated executor crash" in data["error"]
    assert data["completedAt"] is not None


async def test_cancellation_marks_row_failed_and_re_raises() -> None:
    """CancelledError → wrapper marks row failed, then propagates the cancel."""
    db = _FakeDb()
    with pytest.raises(asyncio.CancelledError):
        await _run_with_persistence(_CanceledExecutor(), db, "exe1")  # type: ignore[arg-type]
    [(exec_id, data)] = db.workflowexecution.update_calls
    assert exec_id == "exe1"
    assert data["status"] == "failed"
    assert "canceled" in data["error"].lower()


async def test_db_failure_during_persist_does_not_mask_original_exception() -> None:
    """If the DB write itself fails, the original exception still propagates."""

    @dataclass
    class _BrokenExecTable:
        async def update(self, *, where: dict[str, Any], data: dict[str, Any]) -> None:
            raise ConnectionError("postgres unreachable")

    db = _FakeDb(workflowexecution=_BrokenExecTable())  # type: ignore[arg-type]
    # Caller must still see the executor's original error, not the DB error.
    with pytest.raises(RuntimeError, match="simulated executor crash"):
        await _run_with_persistence(_CrashingExecutor(), db, "exe1")  # type: ignore[arg-type]
