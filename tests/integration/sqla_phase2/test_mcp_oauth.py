"""MCP OAuth flow (authorize, callback, disconnect), token lookup/refresh and the resolver.

Pins today's (Prisma-backed) behaviour. Test groups 7-11 of the phase 2 plan. Resolver and
token helpers are reached only through the ``adapter`` fixture.
"""

import base64
import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient, Response

from src.mcp.oauth import McpTokenExpiredError, McpTokenMissingError, TokenRefreshError
from src.mcp.resolver import McpPermissionError, McpServerNotFound
from src.security.encryption import decrypt, decrypt_marked
from tests.integration.sqla_phase2.conftest import (
    HTTPX_MARK,
    LONG_AGO,
    Db,
    FakeEndpoint,
    FakeEndpointFactory,
    FakeMcpFactory,
    Member,
    Phase2Adapter,
    ServerApi,
    jbody,
    near,
    origin_of,
    query_of,
    split_url,
    unique,
)

pytestmark = [pytest.mark.integration, HTTPX_MARK]

REDIRECT = "http://backend.test/oauth/callback"
FRONTEND = "http://localhost:3000/admin/mcp-servers"


# --------------------------------------------------------------------------- helpers


async def idp(db: Db, fake_endpoint: FakeEndpointFactory, server_id: str) -> FakeEndpoint:
    """The scripted token endpoint of an OAuth server created by ServerApi.create_oauth."""
    return fake_endpoint((await db.oauth_config(server_id))["tokenUrl"])


async def authorize(
    client: AsyncClient, user: Member, server_id: str, redirect_uri: str = REDIRECT
) -> str:
    """POST oauth/authorize and return the `state` embedded in the authorize URL."""
    r = await client.post(
        f"/mcp-servers/{server_id}/oauth/authorize",
        json={"redirectUri": redirect_uri},
        headers=user.headers,
    )
    assert r.status_code == 200, r.text
    return query_of(jbody(r)["authorizeUrl"])["state"]


async def callback(client: AsyncClient, state: str, code: str = "auth-code") -> Response:
    return await client.get(
        "/oauth/callback", params={"code": code, "state": state}, follow_redirects=False
    )


def location(resp: Response) -> tuple[str, dict[str, list[str]]]:
    assert resp.status_code == 303, resp.text
    return split_url(resp.headers["location"])


async def connect(
    client: AsyncClient,
    user: Member,
    server_id: str,
    token_endpoint: FakeEndpoint,
    *,
    access: str,
    refresh: str | None = "rt",
    expires_in: int | None = 3600,
) -> None:
    """Complete authorize -> callback for `user`, with the IdP answering `access`."""
    payload: dict[str, Any] = {"access_token": access, "token_type": "Bearer"}
    if refresh is not None:
        payload["refresh_token"] = refresh
    if expires_in is not None:
        payload["expires_in"] = expires_in
    token_endpoint.push(payload)
    state = await authorize(client, user, server_id)
    base, params = location(await callback(client, state))
    assert base == FRONTEND and params["oauth"] == ["success"], params


# --------------------------------------------------------------------------- 7. authorize


async def test_authorize_returns_url_and_stores_state(
    client: AsyncClient, member: Member, servers: ServerApi, db: Db
) -> None:
    created = await servers.create_oauth(member)
    sid = created["id"]
    cfg = await db.oauth_config(sid)
    r = await client.post(
        f"/mcp-servers/{sid}/oauth/authorize",
        json={"redirectUri": REDIRECT},
        headers=member.headers,
    )
    assert r.status_code == 200, r.text
    body = jbody(r)
    assert set(body) == {"authorizeUrl"}
    assert cfg["clientSecret"] not in r.text and "secret-" not in r.text
    base, _ = split_url(body["authorizeUrl"])
    assert base == cfg["authorizeUrl"]
    q = query_of(body["authorizeUrl"])
    assert set(q) == {
        "client_id", "redirect_uri", "response_type", "state", "code_challenge",
        "code_challenge_method", "scope", "resource",
    }  # fmt: skip
    assert q["client_id"] == cfg["clientId"] and q["redirect_uri"] == REDIRECT
    assert q["response_type"] == "code" and q["code_challenge_method"] == "S256"
    assert q["scope"] == "read write"
    assert q["resource"] == origin_of(created["url"])  # RFC 8707 on the authorize URL

    row = await db.state(q["state"])
    assert row is not None
    assert row.mcp_server_id == sid and row.user_id == member.id
    assert row.redirect_uri == REDIRECT and row.scope == "read write"
    digest = hashlib.sha256(row.code_verifier.encode("ascii")).digest()
    assert base64.urlsafe_b64encode(digest).rstrip(b"=").decode() == q["code_challenge"]
    now = datetime.now(UTC)
    assert (
        now + timedelta(minutes=4, seconds=30)
        < row.expires_at
        < now + timedelta(minutes=5, seconds=30)
    )


async def test_authorize_without_scopes_stores_null_scope(
    client: AsyncClient, member: Member, servers: ServerApi, db: Db
) -> None:
    created = await servers.create_oauth(member, oauthConfig={"scopes": []})
    state = await authorize(client, member, created["id"])
    row = await db.state(state)
    assert row is not None and row.scope is None


async def test_authorize_errors_and_no_visibility_check(
    client: AsyncClient, member: Member, second_member: Member, servers: ServerApi, db: Db
) -> None:
    plain = await servers.create(member)
    r = await client.post(
        f"/mcp-servers/{plain['id']}/oauth/authorize",
        json={"redirectUri": REDIRECT},
        headers=member.headers,
    )
    assert r.status_code == 422
    assert r.json() == {"detail": "MCP server authType must be 'oauth' (got 'none')."}
    r = await client.post(
        "/mcp-servers/nope/oauth/authorize", json={"redirectUri": REDIRECT}, headers=member.headers
    )
    assert r.status_code == 404 and r.json() == {"detail": "MCP server 'nope' not found."}
    r = await client.post(
        f"/mcp-servers/{plain['id']}/oauth/authorize", json={}, headers=member.headers
    )
    assert r.status_code == 422  # redirectUri required

    # Today there is no ownership/visibility check: another member can start the flow on a
    # private server, and the state is recorded for THAT member.
    private = await servers.create_oauth(member, isShared=False)
    state = await authorize(client, second_member, private["id"])
    row = await db.state(state)
    assert row is not None and row.user_id == second_member.id


async def test_authorize_reaps_expired_states_of_any_server_or_user(
    client: AsyncClient, member: Member, second_member: Member, servers: ServerApi, db: Db
) -> None:
    created = await servers.create_oauth(member)
    other = await servers.create_oauth(second_member)
    stale = await db.seed_state(other["id"], second_member.id, expires_in=timedelta(minutes=-1))
    live = await db.seed_state(other["id"], second_member.id, expires_in=timedelta(minutes=3))
    await authorize(client, member, created["id"])
    assert await db.state(stale) is None  # reaped lazily, whoever owned it
    assert await db.state(live) is not None  # unexpired states are never touched


# --------------------------------------------------------------------------- 7. callback


async def test_callback_exchanges_code_and_stores_encrypted_tokens(
    client: AsyncClient,
    member: Member,
    servers: ServerApi,
    db: Db,
    fake_endpoint: FakeEndpointFactory,
) -> None:
    created = await servers.create_oauth(member)
    sid = created["id"]
    token_ep = await idp(db, fake_endpoint, sid)
    cfg = await db.oauth_config(sid)
    token_ep.push(
        {
            "access_token": "at-1",
            "refresh_token": "rt-1",
            "expires_in": 3600,
            "token_type": "Bearer",
            "scope": "read write extra",
        }
    )
    state = await authorize(client, member, sid)
    state_row = await db.state(state)
    assert state_row is not None
    verifier = state_row.code_verifier

    resp = await callback(client, state, code="the-code")
    assert resp.status_code == 303
    assert resp.headers["location"] == f"{FRONTEND}?serverId={sid}&oauth=success"
    assert resp.content == b""

    # one POST to the token endpoint carrying every OAuth parameter, incl. RFC 8707 `resource`
    assert len(token_ep.requests) == 1
    assert token_ep.requests[0].headers["accept"] == "application/json"
    assert token_ep.forms()[0] == {
        "grant_type": "authorization_code",
        "code": "the-code",
        "redirect_uri": REDIRECT,
        "code_verifier": verifier,
        "client_id": cfg["clientId"],
        "client_secret": decrypt_marked(cfg["clientSecret"]),
        "resource": origin_of(created["url"]),
    }

    token = await db.token(sid, member.id)
    assert token is not None
    assert (
        token.encrypted_access_token != "at-1" and decrypt(token.encrypted_access_token) == "at-1"
    )
    assert token.encrypted_refresh_token is not None
    assert token.encrypted_refresh_token != "rt-1"
    assert decrypt(token.encrypted_refresh_token) == "rt-1"
    assert token.token_type == "Bearer" and token.scope == "read write extra"
    assert near(token.expires_at, datetime.now(UTC) + timedelta(seconds=3600))
    assert await db.state(state) is None  # one-shot: consumed

    listed = (await client.get("/mcp-servers", headers=member.headers)).json()
    mine = next(s for s in listed if s["id"] == sid)
    assert mine["hasOauthToken"] is False  # the list route never computes this flag today
    assert "at-1" not in str(listed) and "rt-1" not in str(listed)


async def test_callback_without_refresh_token_or_expiry_and_reconnect_updates_in_place(
    client: AsyncClient,
    member: Member,
    servers: ServerApi,
    db: Db,
    fake_endpoint: FakeEndpointFactory,
) -> None:
    created = await servers.create_oauth(member)
    sid = created["id"]
    token_ep = await idp(db, fake_endpoint, sid)

    await connect(client, member, sid, token_ep, access="first", refresh=None, expires_in=None)
    first = await db.token(sid, member.id)
    assert first is not None and first.encrypted_refresh_token is None and first.expires_at is None
    assert first.scope == "read write"  # falls back to the scope recorded with the state
    assert first.token_type == "Bearer"
    await db.set_token(first.id, updated_at=LONG_AGO)

    await connect(client, member, sid, token_ep, access="second", refresh="rt-2")
    second = await db.token(sid, member.id)
    assert second is not None and second.id == first.id  # upsert in place, one row per user+server
    assert decrypt(second.encrypted_access_token) == "second"
    assert second.encrypted_refresh_token is not None
    assert decrypt(second.encrypted_refresh_token) == "rt-2"
    assert second.updated_at > LONG_AGO and near(second.updated_at, datetime.now(UTC))
    assert second.created_at == first.created_at


async def test_callback_replayed_and_unknown_state_redirect_with_error(
    client: AsyncClient,
    member: Member,
    servers: ServerApi,
    db: Db,
    fake_endpoint: FakeEndpointFactory,
) -> None:
    created = await servers.create_oauth(member)
    sid = created["id"]
    token_ep = await idp(db, fake_endpoint, sid)
    token_ep.push({"access_token": "at"})
    state = await authorize(client, member, sid)
    ok = await callback(client, state)
    assert location(ok)[1]["oauth"] == ["success"]

    for bad in (state, "never-issued-state"):  # replay of a consumed state / a forged state
        r = await callback(client, bad)
        base, params = location(r)
        assert base == FRONTEND
        assert params == {
            "serverId": [""],
            "oauth": ["error"],
            "detail": ["OAuth state not found (CSRF or replay)."],
        }
    assert len(token_ep.requests) == 1  # the IdP was never asked again
    token = await db.token(sid, member.id)
    assert token is not None and decrypt(token.encrypted_access_token) == "at"


async def test_callback_expired_state_redirects_with_error_and_calls_no_idp(
    client: AsyncClient,
    member: Member,
    servers: ServerApi,
    db: Db,
    fake_endpoint: FakeEndpointFactory,
) -> None:
    created = await servers.create_oauth(member)
    sid = created["id"]
    token_ep = await idp(db, fake_endpoint, sid)
    state = await authorize(client, member, sid)
    await db.set_state(state, expires_at=datetime.now(UTC) - timedelta(seconds=5))

    base, params = location(await callback(client, state))
    assert base == FRONTEND
    assert params == {
        "serverId": [sid],
        "oauth": ["error"],
        "detail": [f"OAuth state '{state}' has expired."],
    }
    assert token_ep.requests == [] and await db.token(sid, member.id) is None


async def test_callback_token_endpoint_error_consumes_state_and_stores_nothing(
    client: AsyncClient,
    member: Member,
    servers: ServerApi,
    db: Db,
    fake_endpoint: FakeEndpointFactory,
) -> None:
    created = await servers.create_oauth(member)
    sid = created["id"]
    token_ep = await idp(db, fake_endpoint, sid)
    token_ep.push({"error": "invalid_grant"}, status=400)
    state = await authorize(client, member, sid)
    base, params = location(await callback(client, state))
    assert base == FRONTEND and params["oauth"] == ["error"] and params["serverId"] == [sid]
    assert params["detail"][0].startswith("OAuth token exchange failed (HTTP 400): ")
    assert "invalid_grant" in params["detail"][0]
    assert await db.state(state) is None  # consumed BEFORE the IdP call: a retry needs a new state
    assert await db.token(sid, member.id) is None


async def test_callback_for_deleted_server_and_missing_params(
    client: AsyncClient, member: Member, servers: ServerApi, db: Db
) -> None:
    created = await servers.create_oauth(member)
    sid = created["id"]
    state = await authorize(client, member, sid)
    assert (await client.delete(f"/mcp-servers/{sid}", headers=member.headers)).status_code == 204
    base, params = location(await callback(client, state))
    assert base == FRONTEND
    assert params == {
        "serverId": [sid],
        "oauth": ["error"],
        "detail": ["Server not found for this OAuth state."],
    }
    assert await db.state(state) is not None  # not consumed on this path (cleaned by teardown)
    assert (await client.get("/oauth/callback", params={"state": state})).status_code == 422
    assert (await client.get("/oauth/callback", params={"code": "c"})).status_code == 422


# --------------------------------------------------------------------------- 8. disconnect


async def test_disconnect_deletes_only_callers_token_and_is_idempotent(
    client: AsyncClient, member: Member, second_member: Member, servers: ServerApi, db: Db
) -> None:
    created = await servers.create_oauth(member, isShared=True)
    sid = created["id"]
    await db.seed_token(sid, member.id, access="owner-at")
    await db.seed_token(sid, second_member.id, access="second-at")

    r = await client.post(f"/mcp-servers/{sid}/oauth/disconnect", headers=second_member.headers)
    assert r.status_code == 204 and r.content == b""
    assert await db.token(sid, second_member.id) is None
    assert await db.token(sid, member.id) is not None  # the owner's token is untouched

    again = await client.post(f"/mcp-servers/{sid}/oauth/disconnect", headers=second_member.headers)
    assert again.status_code == 204  # idempotent: nothing to disconnect
    unknown = await client.post("/mcp-servers/nope/oauth/disconnect", headers=member.headers)
    assert unknown.status_code == 204  # not even an existence check on the server
    r = await client.post(f"/mcp-servers/{sid}/oauth/disconnect", headers=member.headers)
    assert r.status_code == 204 and await db.token(sid, member.id) is None


# --------------------------------------------------------------------------- 9. shared fallback


async def test_shared_server_falls_back_to_owner_token_until_caller_connects(
    client: AsyncClient,
    member: Member,
    second_member: Member,
    servers: ServerApi,
    db: Db,
    adapter: Phase2Adapter,
    fake_endpoint: FakeEndpointFactory,
) -> None:
    created = await servers.create_oauth(member, isShared=True)
    sid = created["id"]
    await db.seed_token(sid, member.id, access="owner-access")

    assert await adapter.valid_access_token(sid, member.id) == "owner-access"
    assert await adapter.valid_access_token(sid, second_member.id) == "owner-access"  # fix #5

    token_ep = await idp(db, fake_endpoint, sid)
    await connect(client, second_member, sid, token_ep, access="second-access")
    assert await adapter.valid_access_token(sid, second_member.id) == "second-access"
    assert await adapter.valid_access_token(sid, member.id) == "owner-access"
    # the fallback never copies the owner's token to the caller
    assert await db.token(sid, second_member.id) is not None
    rows_for_owner = await db.token(sid, member.id)
    assert (
        rows_for_owner is not None
        and decrypt(rows_for_owner.encrypted_access_token) == "owner-access"
    )


async def test_token_lookup_errors_when_no_token_and_no_fallback(
    member: Member,
    second_member: Member,
    servers: ServerApi,
    db: Db,
    adapter: Phase2Adapter,
) -> None:
    private = await servers.create_oauth(member, isShared=False)
    await db.seed_token(private["id"], member.id)
    with pytest.raises(McpTokenMissingError) as exc:  # private: no fallback for other users
        await adapter.valid_access_token(private["id"], second_member.id)
    assert str(exc.value) == (
        f"No OAuth token found for user {second_member.id!r} on MCP server {private['id']!r}. "
        f"User must complete the OAuth flow first."
    )

    shared = await servers.create_oauth(member, isShared=True)
    with pytest.raises(McpTokenMissingError) as exc2:  # shared but the owner never connected
        await adapter.valid_access_token(shared["id"], second_member.id)
    assert str(exc2.value).startswith(
        f"No token for user {second_member.id!r} on MCP server {shared['id']!r}, "
        f"and server owner {member.id!r} has no token either (service-account fallback failed)."
    )
    with pytest.raises(McpTokenMissingError) as exc3:  # the owner themself gets no fallback
        await adapter.valid_access_token(shared["id"], member.id)
    assert str(exc3.value).startswith(f"No OAuth token found for user {member.id!r} on MCP server")


async def test_shared_server_mcp_requests_carry_the_callers_own_token_else_the_owners(
    member: Member,
    second_member: Member,
    servers: ServerApi,
    db: Db,
    adapter: Phase2Adapter,
    fake_mcp: FakeMcpFactory,
) -> None:
    fake = fake_mcp()
    created = await servers.create_oauth(member, url=fake.url, isShared=True)
    sid = created["id"]
    await db.seed_token(sid, member.id, access="owner-access")

    assert await adapter.resolve_tools(sid, second_member.id) == ["alpha", "beta"]
    assert fake.authorizations() == {"Bearer owner-access"}  # fix #5 on the wire

    await db.seed_token(sid, second_member.id, access="second-access")
    assert await adapter.resolve_tools(sid, second_member.id) == ["alpha", "beta"]
    assert fake.calls[-1].headers["authorization"] == "Bearer second-access"
    assert await adapter.resolve_tools(sid, member.id) == ["alpha", "beta"]
    assert fake.calls[-1].headers["authorization"] == "Bearer owner-access"


# --------------------------------------------------------------------------- 10. refresh


async def test_expired_token_is_refreshed_with_resource_and_row_updated(
    member: Member,
    servers: ServerApi,
    db: Db,
    adapter: Phase2Adapter,
    fake_endpoint: FakeEndpointFactory,
) -> None:
    created = await servers.create_oauth(member)
    sid = created["id"]
    cfg = await db.oauth_config(sid)
    token_ep = await idp(db, fake_endpoint, sid)
    token_id = await db.seed_token(
        sid, member.id, access="old-access", refresh="old-refresh", expires_in=timedelta(minutes=-5)
    )
    await db.set_token(token_id, updated_at=LONG_AGO)
    before = await db.token(sid, member.id)
    assert before is not None
    token_ep.push(
        {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 7200}
    )

    assert await adapter.valid_access_token(sid, member.id) == "new-access"

    assert token_ep.forms() == [
        {
            "grant_type": "refresh_token",
            "refresh_token": "old-refresh",
            "client_id": cfg["clientId"],
            "client_secret": decrypt_marked(cfg["clientSecret"]),
            "resource": origin_of(created["url"]),  # RFC 8707 on refresh too
        }
    ]
    after = await db.token(sid, member.id)
    assert after is not None and after.id == token_id
    assert after.encrypted_access_token != before.encrypted_access_token
    assert decrypt(after.encrypted_access_token) == "new-access"
    assert after.encrypted_refresh_token is not None
    assert decrypt(after.encrypted_refresh_token) == "new-refresh"  # rotated
    assert near(after.expires_at, datetime.now(UTC) + timedelta(seconds=7200))
    assert after.updated_at > LONG_AGO and near(after.updated_at, datetime.now(UTC))

    # the refreshed token is now valid: no further IdP call
    assert await adapter.valid_access_token(sid, member.id) == "new-access"
    assert len(token_ep.requests) == 1


async def test_refresh_keeps_old_refresh_token_when_idp_does_not_rotate(
    member: Member,
    servers: ServerApi,
    db: Db,
    adapter: Phase2Adapter,
    fake_endpoint: FakeEndpointFactory,
) -> None:
    created = await servers.create_oauth(member)
    sid = created["id"]
    token_ep = await idp(db, fake_endpoint, sid)
    # inside the 60s expiry buffer counts as expired
    await db.seed_token(sid, member.id, refresh="keep-me", expires_in=timedelta(seconds=30))
    token_ep.push({"access_token": "fresh"})  # no refresh_token, no expires_in

    assert await adapter.valid_access_token(sid, member.id) == "fresh"
    row = await db.token(sid, member.id)
    assert row is not None and row.encrypted_refresh_token is not None
    assert decrypt(row.encrypted_refresh_token) == "keep-me"
    assert row.expires_at is None  # an IdP that omits expires_in clears the expiry


async def test_unexpired_token_is_returned_without_contacting_the_idp(
    member: Member,
    servers: ServerApi,
    db: Db,
    adapter: Phase2Adapter,
    fake_endpoint: FakeEndpointFactory,
) -> None:
    created = await servers.create_oauth(member)
    token_ep = await idp(db, fake_endpoint, created["id"])
    await db.seed_token(
        created["id"], member.id, access="still-good", expires_in=timedelta(minutes=10)
    )
    assert await adapter.valid_access_token(created["id"], member.id) == "still-good"
    never_expires = await servers.create_oauth(member)
    await db.seed_token(never_expires["id"], member.id, access="forever", expires_in=None)
    assert await adapter.valid_access_token(never_expires["id"], member.id) == "forever"
    assert token_ep.requests == []


async def test_refresh_failures_leave_the_row_unchanged(
    member: Member,
    servers: ServerApi,
    db: Db,
    adapter: Phase2Adapter,
    fake_endpoint: FakeEndpointFactory,
) -> None:
    created = await servers.create_oauth(member)
    sid = created["id"]
    token_ep = await idp(db, fake_endpoint, sid)
    token_id = await db.seed_token(sid, member.id, access="stale", expires_in=timedelta(minutes=-1))
    before = await db.token(sid, member.id)
    token_ep.push({"error": "invalid_grant"}, status=400)
    with pytest.raises(TokenRefreshError) as exc:
        await adapter.valid_access_token(sid, member.id)
    assert str(exc.value).startswith("OAuth refresh failed (HTTP 400): ")
    after = await db.token(sid, member.id)
    assert before is not None and after is not None and after.id == token_id
    assert after.encrypted_access_token == before.encrypted_access_token
    assert after.updated_at == before.updated_at

    other = await servers.create_oauth(member)
    await db.seed_token(other["id"], member.id, refresh=None, expires_in=timedelta(minutes=-1))
    with pytest.raises(McpTokenExpiredError) as exc2:
        await adapter.valid_access_token(other["id"], member.id)
    assert str(exc2.value) == (
        f"Token for server {other['id']!r} expired and has no refresh token; "
        f"user must re-authorize."
    )


# --------------------------------------------------------------------------- 11. resolver


async def test_resolver_returns_the_servers_tools_with_static_auth(
    member: Member, servers: ServerApi, adapter: Phase2Adapter, fake_mcp: FakeMcpFactory
) -> None:
    fake = fake_mcp()
    created = await servers.create(
        member,
        url=fake.url,
        authType="bearer",
        accessToken="bearer-tok",
        headers={"X-Custom": "never-sent", "Authorization": "Bearer also-never-sent"},
    )
    sid = created["id"]
    assert await adapter.resolve_tools(sid, member.id) == ["alpha", "beta"]
    assert fake.methods() == ["tools/list"]  # the resolver lists tools only: no initialize
    assert fake.authorizations() == {"Bearer bearer-tok"}
    # custom `headers` are stored (redacted on read) but the MCP client never sends them today
    assert all("x-custom" not in c.headers for c in fake.calls)

    assert await adapter.resolve_named_tools(sid, member.id, ["beta", "ghost"]) == ["beta"]
    assert await adapter.resolve_named_tools(sid, member.id, []) == ["alpha", "beta"]
    assert await adapter.resolve_node_tools([sid], member.id) == ["alpha", "beta"]
    assert await adapter.resolve_node_tools([], member.id) == []
    assert await adapter.resolve_single_tool(sid, "beta", member.id) == "beta"
    # an unadvertised tool still yields an (open-schema) tool so the server can reject it
    assert await adapter.resolve_single_tool(sid, "unlisted", member.id) == "unlisted"


async def test_resolver_api_key_server_uses_header_name(
    member: Member, servers: ServerApi, adapter: Phase2Adapter, fake_mcp: FakeMcpFactory
) -> None:
    fake = fake_mcp()
    created = await servers.create(
        member, url=fake.url, authType="api-key", accessToken="key-123", headerName="X-Api-Key"
    )
    assert await adapter.resolve_tools(created["id"], member.id) == ["alpha", "beta"]
    assert fake.calls[0].headers["x-api-key"] == "key-123"
    assert "authorization" not in fake.calls[0].headers
    keyless = await servers.create(member, url=fake.url, authType="none")
    assert await adapter.resolve_tools(keyless["id"], member.id) == ["alpha", "beta"]
    assert "authorization" not in fake.calls[-1].headers


async def test_resolver_missing_and_forbidden_servers(
    member: Member,
    second_member: Member,
    servers: ServerApi,
    adapter: Phase2Adapter,
    fake_mcp: FakeMcpFactory,
) -> None:
    with pytest.raises(McpServerNotFound) as nf:
        await adapter.resolve_tools("no-such-server", member.id)
    assert str(nf.value) == "MCP server 'no-such-server' not found"
    with pytest.raises(McpServerNotFound) as nf2:
        await adapter.resolve_node_tools(["no-such-server"], member.id)
    assert str(nf2.value) == "MCP server 'no-such-server' not found"
    with pytest.raises(McpServerNotFound):
        await adapter.resolve_single_tool("no-such-server", "t", member.id)

    fake = fake_mcp()
    private = await servers.create(member, url=fake.url, isShared=False)
    shared = await servers.create(member, url=fake.url, isShared=True)
    sid = private["id"]
    with pytest.raises(McpPermissionError) as pe:
        await adapter.resolve_tools(sid, second_member.id)
    assert str(pe.value) == f"User {second_member.id!r} cannot use MCP server {sid!r}"
    with pytest.raises(McpPermissionError) as pe2:
        await adapter.resolve_node_tools([sid], second_member.id)
    assert str(pe2.value) == (
        f"User {second_member.id!r} cannot use MCP server {sid!r} "
        f"(owner={member.id!r}, isShared=False)"
    )
    with pytest.raises(McpPermissionError):
        await adapter.resolve_single_tool(sid, "alpha", second_member.id)
    assert fake.calls == []  # nothing was sent to a server the caller may not use
    assert await adapter.resolve_tools(shared["id"], second_member.id) == ["alpha", "beta"]


async def test_resolver_oauth_server_sends_the_callers_bearer_token(
    member: Member,
    servers: ServerApi,
    db: Db,
    adapter: Phase2Adapter,
    fake_mcp: FakeMcpFactory,
) -> None:
    fake = fake_mcp()
    created = await servers.create_oauth(member, url=fake.url)
    token = unique("oauth-at")
    await db.seed_token(created["id"], member.id, access=token)
    assert await adapter.resolve_tools(created["id"], member.id) == ["alpha", "beta"]
    assert fake.authorizations() == {f"Bearer {token}"}
