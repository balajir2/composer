"""Group 8 (WebSocket half): GET /executions/{id}/ws snapshot, replay, live delivery, authz.

Starlette's synchronous ``TestClient`` on its own app; every DB setup and inspection runs on the
app's own event loop through ``client.portal.call(...)`` (a Prisma/SQLAlchemy client is bound to
the loop that created it), following tests/integration/test_events_ws_hardening.py.
"""

import json
import secrets
from collections.abc import Awaitable, Callable, Iterator
from typing import Any, TypeVar, cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, update
from starlette.websockets import WebSocketDisconnect

from src.config import get_settings
from src.db.models import User, UserRole, Workflow, WorkflowAssignment
from src.main import create_app
from tests._rate_limit_reset import reset_rate_limit_buckets
from tests.integration.sqla_phase4a.conftest import (
    HTTPX_MARK,
    Db,
    Phase4aAdapter,
    purge_execution_artifacts,
)

pytestmark = [pytest.mark.integration, HTTPX_MARK]

T = TypeVar("T")
SNAPSHOT_KEYS = {"type", "executionId", "tenantId", "timestamp", "seq", "status"}


@pytest.fixture(autouse=True)
def _drive_cloud_tasks_synchronously() -> None:  # pyright: ignore[reportUnusedFunction]
    """Shadow tests/integration/conftest.py's fixture of the same name: it requests the async
    `app` fixture, i.e. a second app on the pytest loop, which this module must not create (see
    test_events_ws_hardening.py)."""
    return None


class WsEnv:
    def __init__(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        self.client = client
        self.sm: Any = cast("Any", client.app).state.db_sessionmaker
        self.db = Db(self.sm)
        self.adapter = Phase4aAdapter(client.app, None, monkeypatch)  # type: ignore[arg-type]
        self.users: list[str] = []

    def run(self, fn: Callable[..., Awaitable[T]], *args: Any) -> T:
        return self.client.portal.call(fn, *args)  # type: ignore[union-attr]

    def user(self, *, admin: bool = False) -> tuple[str, str]:
        """Register a user; returns (user_id, access_token)."""
        self.run(reset_rate_limit_buckets)
        email = f"p4a-ws-{secrets.token_hex(6)}@example.com"
        r = self.client.post(
            "/auth/register", json={"email": email, "password": "correct-horse-battery-staple"}
        )
        assert r.status_code == 201, r.text
        uid, token = r.json()["id"], r.json()["accessToken"]
        self.users.append(uid)
        if admin:

            async def _promote() -> None:
                async with self.sm() as s:
                    await s.execute(update(User).where(User.id == uid).values(role=UserRole.ADMIN))
                    await s.commit()

            self.run(_promote)
        return uid, token

    def execution(self, owner_id: str, status: str) -> str:
        async def _make() -> str:
            wid = await self.db.seed_workflow(owner_id)
            return await self.db.seed_execution(wid, owner_id, status=status)

        return self.run(_make)

    def events(self, eid: str, specs: list[tuple[int, str, dict[str, Any]]]) -> None:
        self.run(self.db.add_events, eid, specs)

    def connect(self, eid: str, token: str | None, after: int | None = None) -> Any:
        url = f"/executions/{eid}/ws" + (f"?after={after}" if after is not None else "")
        return self.client.websocket_connect(url, subprotocols=["bearer", token] if token else None)

    def cleanup(self) -> None:
        from src.engine.events_notify import close_notify_connection

        async def _clean() -> None:
            await purge_execution_artifacts(self.sm, user_ids=self.users)
            async with self.sm() as s:
                await s.execute(delete(Workflow).where(Workflow.user_id.in_(self.users)))
                await s.execute(
                    delete(WorkflowAssignment).where(WorkflowAssignment.user_id.in_(self.users))
                )
                await s.execute(delete(User).where(User.id.in_(self.users)))
                await s.commit()
            await close_notify_connection()

        self.run(_clean)


@pytest.fixture
def ws(phase4a_env: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[WsEnv]:
    get_settings.cache_clear()
    app = create_app()
    with TestClient(app) as c:
        env = WsEnv(c, monkeypatch)
        try:
            yield env
        finally:
            env.cleanup()


def _msg(sock: Any) -> dict[str, Any]:
    return json.loads(sock.receive_text())


@pytest.mark.parametrize(
    ("status", "expected_type", "closes"),
    [
        ("queued", "workflow_started", False),
        ("running", "workflow_started", False),
        ("waiting_approval", "approval_required", False),
        ("completed", "workflow_completed", True),
        ("failed", "workflow_completed", True),
        ("canceled", "workflow_completed", True),
    ],
)
def test_snapshot_type_per_status(ws: WsEnv, status: str, expected_type: str, closes: bool) -> None:
    uid, token = ws.user()
    eid = ws.execution(uid, status)
    with ws.connect(eid, token) as sock:
        first = _msg(sock)
        assert set(first) == SNAPSHOT_KEYS
        assert first["type"] == expected_type and first["status"] == status
        assert first["executionId"] == eid and first["tenantId"] is None and first["seq"] is None
        if closes:
            with pytest.raises(WebSocketDisconnect) as exc:
                sock.receive_text()
            assert exc.value.code == 1000 and exc.value.reason == "terminal"


def test_after_cursor_replays_only_later_events_and_closes_on_terminal(ws: WsEnv) -> None:
    uid, token = ws.user()
    eid = ws.execution(uid, "running")
    ws.events(
        eid,
        [
            (5, "workflow_completed", {"status": "completed"}),
            (3, "node_started", {"node_id": "b"}),
            (1, "workflow_started", {"status": "running"}),
            (4, "node_completed", {"node_id": "b"}),
            (2, "node_completed", {"node_id": "a"}),
        ],
    )
    with ws.connect(eid, token, after=2) as sock:
        first = _msg(sock)
        assert first["type"] == "workflow_started" and first["seq"] is None  # the snapshot
        replayed = [_msg(sock) for _ in range(3)]
        assert [m["seq"] for m in replayed] == [3, 4, 5]
        assert [m["type"] for m in replayed] == [
            "node_started",
            "node_completed",
            "workflow_completed",
        ]
        assert replayed[0]["node_id"] == "b" and replayed[2]["status"] == "completed"
        assert {"type", "executionId", "tenantId", "timestamp", "seq"} <= set(replayed[0])
        with pytest.raises(WebSocketDisconnect) as exc:
            sock.receive_text()
        assert exc.value.code == 1000 and exc.value.reason == "terminal"


def test_replay_from_zero_includes_every_event(ws: WsEnv) -> None:
    uid, token = ws.user()
    eid = ws.execution(uid, "running")
    ws.events(eid, [(1, "workflow_started", {}), (2, "node_started", {"node_id": "x"})])
    with ws.connect(eid, token) as sock:
        _msg(sock)  # snapshot
        assert [_msg(sock)["seq"] for _ in range(2)] == [1, 2]


def test_event_appended_and_notified_while_connected_is_delivered(ws: WsEnv) -> None:
    uid, token = ws.user()
    eid = ws.execution(uid, "running")
    with ws.connect(eid, token) as sock:
        assert _msg(sock)["type"] == "workflow_started"  # snapshot
        seq1 = ws.run(ws.adapter.append_event, eid, "node_started", {"node_id": "live"})
        ws.run(ws.adapter.notify, eid, seq1)
        live = _msg(sock)
        assert live["seq"] == 1 and live["type"] == "node_started" and live["node_id"] == "live"
        seq2 = ws.run(ws.adapter.append_event, eid, "workflow_completed", {"status": "completed"})
        ws.run(ws.adapter.notify, eid, seq2)
        done = _msg(sock)
        assert done["seq"] == 2 and done["type"] == "workflow_completed"
        with pytest.raises(WebSocketDisconnect) as exc:
            sock.receive_text()
        assert exc.value.code == 1000


def test_authorization_close_codes(ws: WsEnv) -> None:
    owner_id, owner_token = ws.user()
    _, other_token = ws.user()
    _, admin_token = ws.user(admin=True)
    eid = ws.execution(owner_id, "completed")

    def refused(token: str | None, target: str) -> int:
        with pytest.raises(WebSocketDisconnect) as exc, ws.connect(target, token) as sock:
            sock.receive_text()
        return int(exc.value.code)

    assert refused(owner_token, "no-such-execution") == 4404
    assert refused(other_token, eid) == 4403
    assert refused(None, eid) == 4401
    assert refused("not-a-jwt", eid) == 4401
    with ws.connect(eid, admin_token) as sock:  # admin may watch anyone's execution
        assert _msg(sock)["type"] == "workflow_completed"
    with ws.connect(eid, owner_token) as sock:
        assert _msg(sock)["executionId"] == eid
