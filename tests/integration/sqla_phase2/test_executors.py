"""Executor and poller entry points that reach the four tables: agent-node tool resolution through
the tool registry, the `mcp` node (both modes), the download-pdf node (Google Drive) and the
internal file-trigger poller. All of them go through the ``adapter`` fixture, whose bodies own
the per-execution context setup.

Pins today's (Prisma-backed) behaviour, including the error text when no database is attached.
"""

import re
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]

from src.security.encryption import decrypt
from tests.integration.sqla_phase2.conftest import (
    HTTPX_MARK,
    LONG_AGO,
    Db,
    DriveConnectionMissingError,
    FakeEndpointFactory,
    FakeMcpFactory,
    McpPermissionError,
    McpServerNotFound,
    Member,
    Phase2Adapter,
    ServerApi,
    near,
    unique,
)

pytestmark = [pytest.mark.integration, HTTPX_MARK]

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
DRIVE_URL = re.compile(r"https://www\.googleapis\.com/(upload/)?drive/v3/files.*")


# --------------------------------------------------------------------------- tool registry


async def test_registry_resolves_mcp_tools_for_an_agent_node(
    member: Member,
    second_member: Member,
    servers: ServerApi,
    adapter: Phase2Adapter,
    fake_mcp: FakeMcpFactory,
) -> None:
    fake = fake_mcp()
    private = await servers.create(member, url=fake.url, isShared=False)
    shared = await servers.create(member, url=fake.url, isShared=True)
    assert await adapter.registry_tools([private["id"], shared["id"]], member.id) == [
        "alpha",
        "beta",
        "alpha",
        "beta",
    ]
    assert await adapter.registry_tools([shared["id"]], second_member.id) == ["alpha", "beta"]
    assert await adapter.registry_tools([], second_member.id, with_db=False) == []

    with pytest.raises(McpPermissionError):
        await adapter.registry_tools([private["id"]], second_member.id)
    with pytest.raises(McpServerNotFound):
        await adapter.registry_tools(["no-such-server"], member.id)
    with pytest.raises(RuntimeError) as exc:
        await adapter.registry_tools([shared["id"]], member.id, with_db=False)
    assert str(exc.value).startswith("BuildContext.db is required for MCP tool resolution.")


# --------------------------------------------------------------------------- mcp node


async def test_mcp_node_deterministic_mode_calls_the_tool_with_the_users_access(
    member: Member,
    second_member: Member,
    servers: ServerApi,
    adapter: Phase2Adapter,
    fake_mcp: FakeMcpFactory,
) -> None:
    fake = fake_mcp()
    private = await servers.create(
        member, url=fake.url, authType="bearer", accessToken="mcp-secret", isShared=False
    )
    delta = await adapter.run_mcp_node_deterministic(private["id"], "alpha", {"q": "hi"}, member.id)
    assert delta["variables"] == {"lastOutput": "ok"}
    assert delta["current_node_id"] == "m"
    result = delta["node_results"]["m"]
    assert result["status"] == "completed" and result["output"] == "ok"
    assert result["input"] == {"q": "hi"}
    assert fake.methods() == ["tools/list", "tools/call"]
    assert fake.calls[-1].params == {"name": "alpha", "arguments": {"q": "hi"}}
    assert fake.authorizations() == {"Bearer mcp-secret"}

    with pytest.raises(McpPermissionError):
        await adapter.run_mcp_node_deterministic(private["id"], "alpha", {}, second_member.id)
    with pytest.raises(McpPermissionError) as dev:  # no user id in state falls back to "dev"
        await adapter.run_mcp_node_deterministic(private["id"], "alpha", {}, None)
    assert str(dev.value) == f"User 'dev' cannot use MCP server {private['id']!r}"
    with pytest.raises(McpServerNotFound):
        await adapter.run_mcp_node_deterministic("no-such-server", "alpha", {}, member.id)
    with pytest.raises(RuntimeError) as exc:
        await adapter.run_mcp_node_deterministic(
            private["id"], "alpha", {}, member.id, with_db=False
        )
    assert str(exc.value).startswith("McpExecutor requires src.engine.context.set_current_db")


async def test_mcp_node_on_a_shared_oauth_server_uses_and_refreshes_the_owners_token(
    member: Member,
    second_member: Member,
    servers: ServerApi,
    db: Db,
    adapter: Phase2Adapter,
    fake_mcp: FakeMcpFactory,
    fake_endpoint: FakeEndpointFactory,
) -> None:
    fake = fake_mcp()
    created = await servers.create_oauth(member, url=fake.url, isShared=True)
    sid = created["id"]
    token_ep = fake_endpoint((await db.oauth_config(sid))["tokenUrl"])
    token_id = await db.seed_token(
        sid, member.id, access="owner-old", refresh="rt", expires_in=timedelta(minutes=-1)
    )
    await db.set_token(token_id, updated_at=LONG_AGO)
    token_ep.push({"access_token": "owner-new", "expires_in": 3600})

    delta = await adapter.run_mcp_node_deterministic(sid, "beta", {}, second_member.id)

    assert delta["variables"] == {"lastOutput": "ok"}
    assert len(token_ep.requests) == 1  # refreshed once, for the first MCP request only
    assert fake.methods() == ["tools/list", "tools/call"]
    assert fake.authorizations() == {"Bearer owner-new"}
    row = await db.token(sid, member.id)
    assert row is not None and decrypt(row.encrypted_access_token) == "owner-new"
    assert row.updated_at > LONG_AGO
    assert await db.token(sid, second_member.id) is None


async def test_mcp_node_agent_mode_runs_a_scripted_model_over_the_servers_tools(
    member: Member,
    second_member: Member,
    servers: ServerApi,
    adapter: Phase2Adapter,
    fake_mcp: FakeMcpFactory,
) -> None:
    fake = fake_mcp()
    private = await servers.create(member, url=fake.url, isShared=False)
    sid = private["id"]
    delta = await adapter.run_mcp_node_agent(
        sid,
        member.id,
        selected_tool_names=["alpha"],
        tool_to_call="alpha",
        final_text="all done",
    )
    assert delta["variables"] == {"lastOutput": "all done"}
    result = delta["node_results"]["m"]
    assert result["status"] == "completed" and result["output"] == "all done"
    assert result["input"] == "do the thing"
    assert fake.methods() == ["tools/list", "tools/call"]
    assert fake.calls[-1].params["name"] == "alpha"
    assert adapter.bound_tool_names == ["alpha"]  # only the selected tool is offered to the model

    with pytest.raises(McpPermissionError):
        await adapter.run_mcp_node_agent(
            sid, second_member.id, selected_tool_names=[], tool_to_call="alpha", final_text="x"
        )
    with pytest.raises(RuntimeError) as exc:
        await adapter.run_mcp_node_agent(
            sid,
            member.id,
            selected_tool_names=[],
            tool_to_call="alpha",
            final_text="x",
            with_db=False,
        )
    assert str(exc.value).startswith("McpExecutor requires src.engine.context.set_current_db")


# --------------------------------------------------------------------------- download-pdf


def drive_recorder(httpx_mock: HTTPXMock) -> list[httpx.Request]:  # pyright: ignore[reportUnknownParameterType]
    """Mock the Drive v3 metadata and upload endpoints; return the list of requests seen."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"files": []})
        if request.method == "POST":
            return httpx.Response(200, json={"id": "new-file-id"})
        return httpx.Response(200, json={})

    httpx_mock.add_callback(handler, url=DRIVE_URL, is_reusable=True)  # pyright: ignore[reportUnknownMemberType]
    return seen


async def test_download_pdf_writes_to_drive_with_the_connections_token(
    member: Member,
    db: Db,
    adapter: Phase2Adapter,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
    fake_endpoint: FakeEndpointFactory,
) -> None:
    seen = drive_recorder(httpx_mock)
    token_ep = fake_endpoint(GOOGLE_TOKEN_URL)
    cid = await db.seed_connection(member.id, access="drive-valid")

    delta = await adapter.run_download_pdf_to_drive(cid, "folder-1")
    assert delta["variables"] == {"lastOutput": "drive://folder-1/report.pdf"}
    assert delta["node_results"]["dp"]["status"] == "completed"
    assert {r.headers["authorization"] for r in seen} == {"Bearer drive-valid"}
    upload = seen[-1]
    assert upload.method == "PATCH" and upload.content.startswith(b"%PDF-")
    assert token_ep.requests == []

    # an expired connection is refreshed first and the new token is what Drive sees
    expired = await db.seed_connection(
        member.id, access="drive-old", refresh="rt", expires_in=timedelta(seconds=-5), scope="a b"
    )
    await db.set_connection(expired, updated_at=LONG_AGO)
    token_ep.push({"access_token": "drive-new", "expires_in": 3600})
    seen.clear()
    await adapter.run_download_pdf_to_drive(expired, "folder-2")
    assert {r.headers["authorization"] for r in seen} == {"Bearer drive-new"}
    row = await db.connection(expired)
    assert row is not None and decrypt(row.encrypted_access_token) == "drive-new"
    assert row.scope == "a b" and near(row.updated_at, datetime.now(UTC))


async def test_download_pdf_errors_for_missing_connection_and_missing_db(
    member: Member, db: Db, adapter: Phase2Adapter, httpx_mock: HTTPXMock
) -> None:  # pyright: ignore[reportUnknownParameterType]
    drive_recorder(httpx_mock)
    with pytest.raises(DriveConnectionMissingError) as exc:
        await adapter.run_download_pdf_to_drive("no-such-connection", "folder-1")
    assert str(exc.value) == "No CloudStorageConnection found for id 'no-such-connection'."
    cid = await db.seed_connection(member.id)
    with pytest.raises(RuntimeError) as no_db:
        await adapter.run_download_pdf_to_drive(cid, "folder-1", with_db=False)
    assert str(no_db.value).startswith(
        "DownloadPdfExecutor requires src.engine.context.set_current_db"
    )


# --------------------------------------------------------------------------- poller


def _trigger_node(connection_id: str, folder_id: str) -> dict[str, object]:
    return {
        "id": "ft",
        "type": "file-trigger",
        "position": {"x": 0, "y": 0},
        "data": {
            "label": "trigger",
            "provider": "google-drive",
            "connectionId": connection_id,
            "driveFolderId": folder_id,
            "targetInputVariable": "doc",
        },
    }


async def test_poll_file_triggers_reads_connections_refreshes_and_skips_foreign_ones(
    member: Member,
    second_member: Member,
    db: Db,
    adapter: Phase2Adapter,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
    fake_endpoint: FakeEndpointFactory,
) -> None:
    all_seen = drive_recorder(httpx_mock)
    token_ep = fake_endpoint(GOOGLE_TOKEN_URL)
    folder = unique("folder-poll")
    old_refresh, new_access = unique("poll-rt"), unique("poll-new")
    mine = await db.seed_connection(
        member.id,
        access=unique("poll-old"),
        refresh=old_refresh,
        expires_in=timedelta(seconds=-5),
        scope="a b",
    )
    await db.set_connection(mine, updated_at=LONG_AGO)
    theirs = await db.seed_connection(
        second_member.id,
        access="theirs-old",
        refresh=unique("theirs-rt"),
        expires_in=timedelta(seconds=-5),
    )
    theirs_before = Db.snapshot(await db.connection(theirs))  # pyright: ignore[reportArgumentType]
    await db.seed_production_workflow(
        member.id,
        [
            _trigger_node(mine, folder),
            _trigger_node(theirs, folder),
            _trigger_node("no-such-conn", folder),
        ],
    )
    token_ep.push({"access_token": new_access, "expires_in": 3600})

    result = await adapter.poll_file_triggers()

    assert result["triggered"] == 0  # tolerant of other production workflows in a shared DB
    # only the caller's own connection is used: refreshed once, then one Drive list call
    refreshes = [f for f in token_ep.forms() if f["refresh_token"] == old_refresh]
    assert len(refreshes) == 1
    seen = [r for r in all_seen if folder in r.url.params.get("q", "")]
    assert [r.method for r in seen] == ["GET"]
    assert seen[0].headers["authorization"] == f"Bearer {new_access}"
    assert not [r for r in all_seen if r.headers["authorization"] == "Bearer theirs-old"]
    row = await db.connection(mine)
    assert row is not None and decrypt(row.encrypted_access_token) == new_access
    assert row.scope == "a b" and near(row.updated_at, datetime.now(UTC))
    # a connection owned by someone else is never touched (confused-deputy guard)
    assert Db.snapshot(await db.connection(theirs)) == theirs_before  # pyright: ignore[reportArgumentType]
