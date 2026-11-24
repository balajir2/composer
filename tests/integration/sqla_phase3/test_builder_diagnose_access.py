"""Security regression: builder 'diagnose a failed run' must not leak other users' executions."""

from typing import Any

import pytest
from httpx import AsyncClient

from src.db.models import WorkflowExecution
from tests.integration.sqla_phase3.conftest import (
    HTTPX_MARK,
    Db,
    Member,
    jbody,
    unique,
)

pytestmark = [pytest.mark.integration, HTTPX_MARK]

ERROR_MARKER = "LEAK-ERROR-7f3a"
RESULT_MARKER = "LEAK-NODE-RESULT-91bc"


class _RecordingModel:
    def __init__(self) -> None:
        self.seen: list[str] = []

    async def ainvoke(self, messages: Any, **kwargs: Any) -> Any:
        from langchain_core.messages import AIMessage

        self.seen.extend(str(getattr(m, "content", "")) for m in messages)
        return AIMessage(content="ok")

    def bind_tools(self, tools: Any) -> "_RecordingModel":
        return self


async def _seed_failed_execution(db: Db, workflow_id: str, user_id: str) -> str:
    async with db.sm() as s:
        row = WorkflowExecution(
            workflow_id=workflow_id,
            user_id=user_id,
            status="failed",
            error=ERROR_MARKER,
            node_results={"n1": {"output": RESULT_MARKER}},
            variables={"safe": "v"},
            thread_id=unique("thread"),
        )
        s.add(row)
        await s.commit()
        return row.id


async def _diagnose(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    who: Member,
    workflow_id: str,
    execution_id: str,
) -> tuple[Any, str]:
    import src.api.builder as builder_module

    model = _RecordingModel()
    monkeypatch.setattr(builder_module, "build_chat_model", lambda *_args, **_kwargs: model)  # pyright: ignore[reportUnknownLambdaType]
    r = await client.post(
        f"/builder/{workflow_id}/message",
        json={"message": "why did it fail?", "executionId": execution_id},
        headers=who.headers,
    )
    return r, "\n".join(model.seen)


async def test_cannot_diagnose_another_users_execution_on_another_workflow(
    client: AsyncClient,
    member: Member,
    second_member: Member,
    db: Db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w1 = await db.seed(member)  # A's workflow
    w2 = await db.seed(second_member)  # B's workflow
    victim_exec = await _seed_failed_execution(db, w1, member.id)

    r, llm_text = await _diagnose(client, monkeypatch, second_member, w2, victim_exec)

    assert r.status_code == 200, r.text
    for leaked in (ERROR_MARKER, RESULT_MARKER, victim_exec):
        assert leaked not in llm_text
        assert leaked not in r.text


async def test_cannot_diagnose_another_users_execution_on_same_shared_workflow(
    client: AsyncClient,
    member: Member,
    second_member: Member,
    db: Db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w1 = await db.seed(member)
    await db.assign(w1, second_member.id, member.id)
    victim_exec = await _seed_failed_execution(db, w1, member.id)

    r, llm_text = await _diagnose(client, monkeypatch, second_member, w1, victim_exec)

    assert r.status_code == 200, r.text
    assert ERROR_MARKER not in llm_text and RESULT_MARKER not in llm_text


async def test_can_diagnose_own_execution_of_the_workflow(
    client: AsyncClient,
    member: Member,
    db: Db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w1 = await db.seed(member)
    own_exec = await _seed_failed_execution(db, w1, member.id)

    r, llm_text = await _diagnose(client, monkeypatch, member, w1, own_exec)

    assert r.status_code == 200, r.text
    assert jbody(r)["reply"]
    assert ERROR_MARKER in llm_text and RESULT_MARKER in llm_text
