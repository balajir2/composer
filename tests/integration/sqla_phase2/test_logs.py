"""No secret ever reaches the logs: access/refresh tokens, client secrets, authorization codes
and PKCE code verifiers, across the MCP OAuth callback, token refresh, test-connection and the
Google Drive callback and refresh flows. (Logs carry exception class names only.)

Captured at INFO, the production level. At DEBUG the Prisma client itself logs every query with
its values, which is outside this app's control and not asserted.
"""

import logging
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient

from src.security.encryption import decrypt_marked
from tests.integration.sqla_phase2.conftest import (
    HTTPX_MARK,
    Db,
    FakeEndpointFactory,
    FakeMcpFactory,
    Member,
    Phase2Adapter,
    ServerApi,
    query_of,
    unique,
)

pytestmark = [pytest.mark.integration, HTTPX_MARK]


def app_log_text(caplog: pytest.LogCaptureFixture) -> str:
    """Everything logged, minus httpx's own line for the in-process test client's requests
    (which necessarily shows the callback URL the test itself called)."""
    return "\n".join(
        r.getMessage() + (f"\n{r.exc_text}" if r.exc_text else "")
        for r in caplog.records
        if "http://test/" not in r.getMessage()
    )


def assert_clean(caplog: pytest.LogCaptureFixture, secrets: list[str]) -> None:
    text = app_log_text(caplog)
    leaking = [
        (r.name, r.levelname)
        for r in caplog.records
        if "http://test/" not in r.getMessage()
        and any(secret and secret in r.getMessage() for secret in secrets)
    ]
    assert not leaking, leaking
    for secret in secrets:
        assert secret and secret not in text, f"secret leaked into logs: {secret[:6]}..."


async def test_mcp_oauth_flow_and_refresh_log_no_secrets(
    client: AsyncClient,
    member: Member,
    servers: ServerApi,
    db: Db,
    adapter: Phase2Adapter,
    fake_mcp: FakeMcpFactory,
    fake_endpoint: FakeEndpointFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    fake = fake_mcp()
    created = await servers.create_oauth(member, url=fake.url, accessToken=unique("static"))
    sid = created["id"]
    cfg = await db.oauth_config(sid)
    client_secret = decrypt_marked(cfg["clientSecret"])
    token_ep = fake_endpoint(cfg["tokenUrl"])
    code = unique("authcode")
    access, refresh, access2 = unique("at"), unique("rt"), unique("at2")

    token_ep.push({"access_token": access, "refresh_token": refresh, "expires_in": 3600})
    r = await client.post(
        f"/mcp-servers/{sid}/oauth/authorize",
        json={"redirectUri": "http://backend.test/oauth/callback"},
        headers=member.headers,
    )
    state = query_of(r.json()["authorizeUrl"])["state"]
    state_row = await db.state(state)
    assert state_row is not None
    verifier = state_row.code_verifier
    cb = await client.get(
        "/oauth/callback", params={"code": code, "state": state}, follow_redirects=False
    )
    assert cb.status_code == 303

    # refresh, then an MCP test-connection that uses the refreshed bearer
    stored = await db.token(sid, member.id)
    assert stored is not None
    await db.set_token(stored.id, expires_at=datetime.now(UTC) - timedelta(days=1))
    token_ep.push({"access_token": access2, "refresh_token": unique("rt2"), "expires_in": 3600})
    assert await adapter.valid_access_token(sid, member.id) == access2
    r = await client.post(f"/mcp-servers/{sid}/test-connection", headers=member.headers)
    assert r.json()["ok"] is True

    # a failing exchange logs nothing sensitive either
    state2 = query_of(
        (
            await client.post(
                f"/mcp-servers/{sid}/oauth/authorize",
                json={"redirectUri": "http://backend.test/oauth/callback"},
                headers=member.headers,
            )
        ).json()["authorizeUrl"]
    )["state"]
    token_ep.push({"error": "invalid_grant"}, status=400)
    await client.get(
        "/oauth/callback",
        params={"code": unique("badcode"), "state": state2},
        follow_redirects=False,
    )

    assert_clean(caplog, [access, refresh, access2, client_secret, code, verifier])


async def test_static_token_test_connection_logs_no_secrets(
    client: AsyncClient,
    member: Member,
    servers: ServerApi,
    fake_mcp: FakeMcpFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    fake = fake_mcp(connect_error=True)  # exercise the error-logging path too
    bearer_secret = unique("bearer")
    header_secret = unique("hdr")
    created = await servers.create(
        member,
        url=fake.url,
        authType="bearer",
        accessToken=bearer_secret,
        headers={"Authorization": f"Bearer {header_secret}"},
    )
    r = await client.post(f"/mcp-servers/{created['id']}/test-connection", headers=member.headers)
    assert r.status_code == 200
    assert_clean(caplog, [bearer_secret, header_secret])


async def test_drive_callback_and_refresh_log_no_secrets(
    client: AsyncClient,
    member: Member,
    db: Db,
    fake_endpoint: FakeEndpointFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    token_ep = fake_endpoint("https://oauth2.googleapis.com/token")
    userinfo_ep = fake_endpoint("https://www.googleapis.com/oauth2/v2/userinfo", method="GET")
    code = unique("gcode")
    access, refresh, access2 = unique("ga"), unique("gr"), unique("ga2")
    r = await client.get("/cloud-storage/google-drive/authorize", headers=member.headers)
    state = query_of(r.json()["authorizeUrl"])["state"]

    token_ep.push({"access_token": access, "refresh_token": refresh, "expires_in": 3600})
    userinfo_ep.push({"email": f"{unique('acct')}@gmail.test"})
    cb = await client.get(
        "/cloud-storage/google-drive/callback", params={"code": code, "state": state}
    )
    assert cb.status_code == 200 and '"status": "success"' in cb.text
    (conn,) = await db.connections_for(member.id)
    await db.set_connection(conn.id, expires_at=datetime.now(UTC) - timedelta(days=1))
    token_ep.push({"access_token": access2, "expires_in": 3600})
    r = await client.post(
        f"/cloud-storage/connections/{conn.id}/picker-token", headers=member.headers
    )
    assert r.json() == {"accessToken": access2}  # the one place a token is returned to the client

    # failing exchange path
    token_ep.push({"error": "invalid_grant"}, status=400)
    await client.get(
        "/cloud-storage/google-drive/callback",
        params={"code": unique("badg"), "state": state},
    )
    assert_clean(
        caplog,
        [access, refresh, access2, code, "google-client-secret-test"],
    )
