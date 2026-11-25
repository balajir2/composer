"""Fake ``executions_repo`` for executor and claim unit tests (no database).

``install`` patches the repo functions the executor and the claim path call, records every
guarded transition, and hands out a sessionmaker whose sessions are ``FakeSession``s. Tests
that prove ordering pass a shared ``events`` list: sessions then record ``session:enter`` /
``session:exit`` and every repo call records ``db:<name>`` inside it.
"""

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from src.db.repos.executions import ClaimedExecution
from tests.unit.db_fakes import FakeSessionmaker, RecordingSessionmaker


def make_row(**overrides: Any) -> SimpleNamespace:
    """An execution record with the attributes the executor reads (snake_case)."""
    base: dict[str, Any] = {
        "id": "ex1",
        "workflow_id": "wf1",
        "user_id": None,
        "status": "running",
        "input": None,
        "thread_id": "t1",
        "variables": {},
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class FakeExecutions:
    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        row: Any = None,
        *,
        statuses: list[str | None] | None = None,
        won: bool | list[bool] = True,
        events: list[str] | None = None,
        claim_result: ClaimedExecution | None = None,
        claim_hangs: bool = False,
        mark_failed_raises: BaseException | None = None,
    ) -> None:
        self.row = row
        self.claim_result = claim_result
        self.claim_hangs = claim_hangs
        self.mark_failed_raises = mark_failed_raises
        self.claim_calls: list[dict[str, Any]] = []
        self.statuses = None if statuses is None else list(statuses)
        self._won = won
        self.events = events
        self.updates: list[dict[str, Any]] = []
        self.created: list[dict[str, Any]] = []
        self.commits = 0
        self.sessionmaker: Any = (
            FakeSessionmaker() if events is None else RecordingSessionmaker(events)
        )
        self.sessionmaker.session.commit.side_effect = self._commit
        self._patch(monkeypatch)

    async def _commit(self) -> None:
        self.commits += 1
        if self.events is not None:
            self.events.append("db:commit")

    def _note(self, name: str) -> None:
        if self.events is not None:
            self.events.append(f"db:{name}")

    def _next_won(self) -> bool:
        if isinstance(self._won, list):
            return self._won.pop(0)
        return self._won

    def _patch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def get(_s: Any, _id: str) -> Any:
            self._note("get")
            return self.row

        async def get_status(_s: Any, _id: str) -> str | None:
            self._note("get_status")
            if self.statuses is not None:
                return self.statuses.pop(0)
            return None if self.row is None else self.row.status

        async def create(_s: Any, **kw: Any) -> Any:
            self._note("create")
            self.created.append(kw)
            return make_row(
                id="ex1",
                workflow_id=kw["workflow_id"],
                user_id=kw["user_id"],
                status="queued",
                input=kw["input"],
                thread_id=kw["thread_id"],
            )

        async def completed(_s: Any, execution_id: str, **kw: Any) -> bool:
            self._note("mark_completed")
            self.updates.append({"status": "completed", "execution_id": execution_id, **kw})
            return self._next_won()

        async def waiting(_s: Any, execution_id: str, **kw: Any) -> bool:
            self._note("mark_waiting_approval")
            self.updates.append({"status": "waiting_approval", "execution_id": execution_id, **kw})
            return self._next_won()

        async def claim(_s: Any, execution_id: str, **kw: Any) -> ClaimedExecution | None:
            self._note("claim")
            self.claim_calls.append({"execution_id": execution_id, **kw})
            if self.claim_hangs:
                await asyncio.sleep(30)
            return self.claim_result

        async def failed(_s: Any, execution_id: str, **kw: Any) -> bool:
            self._note("mark_failed")
            if self.mark_failed_raises is not None:
                raise self.mark_failed_raises
            self.updates.append({"status": "failed", "execution_id": execution_id, **kw})
            return self._next_won()

        base = "src.db.repos.executions."
        monkeypatch.setattr(base + "claim", claim)
        monkeypatch.setattr(base + "get", get)
        monkeypatch.setattr(base + "get_status", get_status)
        monkeypatch.setattr(base + "create", create)
        monkeypatch.setattr(base + "mark_completed_if_running", completed)
        monkeypatch.setattr(base + "mark_waiting_approval_if_running", waiting)
        monkeypatch.setattr(base + "mark_failed_if_running", failed)
