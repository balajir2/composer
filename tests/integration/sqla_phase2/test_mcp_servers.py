"""MCP server routes: CRUD, listing, ownership, sharing, oauth-config, test-connection.

Pins today's (Prisma-backed) behaviour. Test groups 1-6 of the phase 2 plan.
"""

from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient

from src.security.encryption import REDACTED_MARKER, SECRET_ENC_PREFIX, decrypt, decrypt_marked
from tests.integration.sqla_phase2.conftest import (
    HTTPX_MARK,
    ISO,
    LONG_AGO,
    Db,
    FakeMcpFactory,
    Member,
    ServerApi,
    iso_dt,
    jbody,
    jlist,
    near,
    unique,
)

pytestmark = [pytest.mark.integration, HTTPX_MARK]


# --------------------------------------------------------------------------- 1. create


async def test_create_returns_redacted_read_and_stores_secrets_encrypted(
    client: AsyncClient, member: Member, db: Db
) -> None:
    token = f"tok-{unique('secret')}"
    header_secret = f"Bearer {unique('hdrsecret')}"
    client_secret = unique("clientsecret")
    payload: dict[str, Any] = {
        "name": unique("crud"),
        "url": "https://crud.example.test/mcp",
        "description": "d",
        "category": "c",
        "authType": "bearer",
        "accessToken": token,
        "headerName": "X-Custom",
        "isShared": False,
        "headers": {"Authorization": header_secret, "X-Plain": "plain-value"},
        "oauthConfig": {
            "authorizeUrl": "https://idp.example.test/authorize",
            "tokenUrl": "https://idp.example.test/token",
            "clientId": "cid",
            "clientSecret": client_secret,
            "scopes": ["a", "b"],
            "audience": "extra-key-kept",
        },
    }
    resp = await client.post("/mcp-servers", json=payload, headers=member.headers)
    assert resp.status_code == 201, resp.text
    body = jbody(resp)

    assert set(body) == {
        "id", "userId", "name", "url", "description", "category", "authType",
        "hasAccessToken", "hasOauthConfig", "hasOauthToken", "headerName", "tools",
        "connectionStatus", "lastTested", "lastError", "enabled", "isOfficial",
        "isShared", "headers", "createdAt", "updatedAt",
    }  # fmt: skip
    assert body["userId"] == member.id
    assert body["name"] == payload["name"] and body["url"] == payload["url"]
    assert body["description"] == "d" and body["category"] == "c"
    assert body["authType"] == "bearer" and body["headerName"] == "X-Custom"
    assert body["hasAccessToken"] is True and body["hasOauthConfig"] is True
    assert body["hasOauthToken"] is False
    assert body["tools"] is None and body["lastTested"] is None and body["lastError"] is None
    assert body["connectionStatus"] == "untested"
    assert body["enabled"] is True and body["isOfficial"] is False and body["isShared"] is False
    assert body["headers"] == {"Authorization": REDACTED_MARKER, "X-Plain": "plain-value"}
    assert ISO.match(body["createdAt"]) and ISO.match(body["updatedAt"])
    # secrets are never in the response, however they are nested
    for secret in (token, header_secret, client_secret, "clientSecret", "encryptedAccessToken"):
        assert secret not in resp.text

    row = await db.server(body["id"])
    assert row is not None and row.user_id == member.id
    assert row.encrypted_access_token is not None and row.encrypted_access_token != token
    assert decrypt(row.encrypted_access_token) == token
    cfg = await db.oauth_config(body["id"])
    headers = await db.headers(body["id"])
    assert cfg["clientSecret"].startswith(SECRET_ENC_PREFIX)
    assert client_secret not in cfg["clientSecret"]
    assert decrypt_marked(cfg["clientSecret"]) == client_secret
    assert cfg["authorizeUrl"] == "https://idp.example.test/authorize"
    assert cfg["tokenUrl"] == "https://idp.example.test/token" and cfg["clientId"] == "cid"
    assert cfg["scopes"] == ["a", "b"] and cfg["audience"] == "extra-key-kept"
    assert headers["Authorization"].startswith(SECRET_ENC_PREFIX)
    assert decrypt_marked(headers["Authorization"]) == header_secret
    assert headers["X-Plain"] == "plain-value"
    assert row.connection_status == "untested" and row.last_tested is None
    assert row.enabled is True and row.is_official is False and row.is_shared is False
    assert row.created_at is not None and row.updated_at is not None


async def test_create_minimal_none_auth_has_no_secrets(
    client: AsyncClient, member: Member, db: Db
) -> None:
    resp = await client.post(
        "/mcp-servers",
        json={
            "name": unique("min"),
            "url": "https://min.example.test/mcp",
            "authType": "none",
            "accessToken": "ignored-for-none",
        },
        headers=member.headers,
    )
    assert resp.status_code == 201, resp.text
    body = jbody(resp)
    assert body["hasAccessToken"] is False and body["hasOauthConfig"] is False
    assert body["headers"] is None and body["isShared"] is False
    assert "ignored-for-none" not in resp.text
    row = await db.server(body["id"])
    assert row is not None
    assert row.encrypted_access_token is None  # token only stored for api-key / bearer
    assert row.headers is None and row.oauth_config is None
    # SQL NULL (not a JSON null value) in all three optional JSON columns
    assert await db.raw_null_flags(body["id"]) == (True, True, True)


async def test_create_oauth_auth_type_drops_access_token(
    client: AsyncClient, member: Member, servers: ServerApi, db: Db
) -> None:
    created = await servers.create_oauth(member, accessToken="not-stored")
    assert created["hasAccessToken"] is False and created["hasOauthConfig"] is True
    row = await db.server(created["id"])
    assert row is not None and row.encrypted_access_token is None


async def test_create_validation_errors(client: AsyncClient, member: Member) -> None:
    base = {"name": "n", "url": "https://v.example.test/mcp"}
    bad_type = await client.post(
        "/mcp-servers", json={**base, "authType": "digest"}, headers=member.headers
    )
    assert bad_type.status_code == 422
    assert bad_type.json() == {
        "detail": "authType must be one of none/api-key/bearer/oauth, got 'digest'"
    }
    for auth in ("bearer", "api-key"):
        r = await client.post(
            "/mcp-servers", json={**base, "authType": auth}, headers=member.headers
        )
        assert r.status_code == 422
        assert r.json() == {"detail": f"authType={auth!r} requires accessToken"}
    r = await client.post(
        "/mcp-servers",
        json={"name": "n", "url": "https://v.example.test/{NO_SUCH_SETTING_FIELD}/mcp",
              "authType": "none"},
        headers=member.headers,
    )  # fmt: skip
    assert r.status_code == 422
    assert r.json()["detail"].startswith(
        "URL template contains {NO_SUCH_SETTING_FIELD} but Settings.no_such_setting_field is empty"
    )
    missing = await client.post(
        "/mcp-servers", json={"name": "n", "authType": "none"}, headers=member.headers
    )
    assert missing.status_code == 422  # pydantic: url is required


# --------------------------------------------------------------------------- 2. listing


async def test_listing_owner_sees_own_and_shared_second_member_sees_only_shared(
    client: AsyncClient, member: Member, second_member: Member, servers: ServerApi
) -> None:
    private = await servers.create(member, isShared=False)
    shared = await servers.create(member, isShared=True)
    theirs = await servers.create(second_member, isShared=False)
    mine_ids = {private["id"], shared["id"], theirs["id"]}

    own = await client.get("/mcp-servers", headers=member.headers)
    assert own.status_code == 200
    rows = jlist(own)
    own_ids = {r["id"] for r in rows}
    assert {private["id"], shared["id"]} <= own_ids
    assert theirs["id"] not in own_ids  # another member's private server is invisible
    for r in rows:  # everything visible is owned by the caller or shared (any owner)
        assert r["userId"] == member.id or r["isShared"] is True
        assert "oauthConfig" not in r and "encryptedAccessToken" not in r

    other = await client.get("/mcp-servers", headers=second_member.headers)
    other_ids = {r["id"] for r in jlist(other)}
    assert shared["id"] in other_ids and theirs["id"] in other_ids
    assert private["id"] not in other_ids
    assert (mine_ids - {private["id"]}) <= other_ids

    # The query has no ORDER BY today: no order is guaranteed and none is pinned here.


# --------------------------------------------------------------------------- 3. get / delete


async def test_no_get_by_id_route(client: AsyncClient, member: Member, servers: ServerApi) -> None:
    created = await servers.create(member)
    r = await client.get(f"/mcp-servers/{created['id']}", headers=member.headers)
    assert r.status_code == 405  # only DELETE exists on this path today


async def test_delete_is_owner_only_403_then_404_after_delete(
    client: AsyncClient, member: Member, second_member: Member, servers: ServerApi, db: Db
) -> None:
    private = await servers.create(member, isShared=False)
    shared = await servers.create(member, isShared=True)

    # Non-owner: 403 (not 404), even for a private server (existence is not hidden), and the
    # detail names the owner.
    for srv in (private, shared):
        r = await client.delete(f"/mcp-servers/{srv['id']}", headers=second_member.headers)
        assert r.status_code == 403
        assert r.json() == {"detail": f"Only the owner ({member.id!r}) can delete this server."}
        assert await db.server(srv["id"]) is not None

    r = await client.delete(f"/mcp-servers/{private['id']}", headers=member.headers)
    assert r.status_code == 204 and r.content == b""
    assert await db.server(private["id"]) is None
    again = await client.delete(f"/mcp-servers/{private['id']}", headers=member.headers)
    assert again.status_code == 404
    assert again.json() == {"detail": f"MCP server {private['id']!r} not found."}
    unknown = await client.delete("/mcp-servers/nope", headers=member.headers)
    assert unknown.status_code == 404
    assert unknown.json() == {"detail": "MCP server 'nope' not found."}

    ids = {r["id"] for r in jlist(await client.get("/mcp-servers", headers=member.headers))}
    assert private["id"] not in ids and shared["id"] in ids


async def test_delete_server_leaves_its_oauth_tokens_behind(
    client: AsyncClient, member: Member, servers: ServerApi, db: Db
) -> None:
    """No FK / no cascade today: deleting a server does not remove token rows for it."""
    created = await servers.create_oauth(member)
    await db.seed_token(created["id"], member.id)
    r = await client.delete(f"/mcp-servers/{created['id']}", headers=member.headers)
    assert r.status_code == 204
    assert await db.token(created["id"], member.id) is not None  # orphan (cleaned by teardown)


# --------------------------------------------------------------------------- 4. share / owner


async def test_share_toggle_is_admin_only_and_changes_second_members_listing(
    client: AsyncClient,
    admin: Member,
    member: Member,
    second_member: Member,
    servers: ServerApi,
    db: Db,
) -> None:
    created = await servers.create(member, isShared=False)
    sid = created["id"]
    bystander = await servers.create(member, isShared=False)
    bystander_before = Db.snapshot(await db.server(bystander["id"]))
    await db.set_server(sid, updated_at=LONG_AGO)

    r = await client.patch(
        f"/mcp-servers/{sid}/shared", json={"isShared": True}, headers=member.headers
    )
    assert r.status_code == 403 and r.json() == {"detail": "admin role required"}  # even the owner

    r = await client.patch(
        f"/mcp-servers/{sid}/shared", json={"isShared": True}, headers=admin.headers
    )
    assert r.status_code == 200
    assert jbody(r)["isShared"] is True and jbody(r)["userId"] == member.id  # owner unchanged
    assert iso_dt(jbody(r)["updatedAt"]) > LONG_AGO
    row = await db.server(sid)
    assert row is not None and near(row.updated_at, datetime.now(UTC))
    assert Db.snapshot(await db.server(bystander["id"])) == bystander_before  # WHERE id only
    ids = {x["id"] for x in jlist(await client.get("/mcp-servers", headers=second_member.headers))}
    assert sid in ids

    r = await client.patch(
        f"/mcp-servers/{sid}/shared", json={"isShared": False}, headers=admin.headers
    )
    assert r.status_code == 200 and jbody(r)["isShared"] is False
    ids = {x["id"] for x in jlist(await client.get("/mcp-servers", headers=second_member.headers))}
    assert sid not in ids

    missing = await client.patch(
        "/mcp-servers/nope/shared", json={"isShared": True}, headers=admin.headers
    )
    assert missing.status_code == 404
    assert missing.json() == {"detail": "MCP server 'nope' not found."}


async def test_admin_assigns_owner_by_id_and_by_email(
    client: AsyncClient,
    admin: Member,
    member: Member,
    second_member: Member,
    servers: ServerApi,
    db: Db,
) -> None:
    created = await servers.create(member, isShared=False)
    sid = created["id"]
    url = f"/mcp-servers/{sid}/owner"
    bystander = await servers.create(member, isShared=True)
    bystander_before = Db.snapshot(await db.server(bystander["id"]))
    await db.set_server(sid, updated_at=LONG_AGO)

    denied = await client.patch(url, json={"userId": second_member.id}, headers=member.headers)
    assert denied.status_code == 403 and denied.json() == {"detail": "admin role required"}

    by_id = await client.patch(url, json={"userId": second_member.id}, headers=admin.headers)
    assert by_id.status_code == 200, by_id.text
    assert jbody(by_id)["userId"] == second_member.id
    assert iso_dt(jbody(by_id)["updatedAt"]) > LONG_AGO
    row = await db.server(sid)
    assert row is not None and row.user_id == second_member.id
    assert near(row.updated_at, datetime.now(UTC))
    assert Db.snapshot(await db.server(bystander["id"])) == bystander_before
    # the private server moved with its owner
    assert sid not in {
        x["id"] for x in jlist(await client.get("/mcp-servers", headers=member.headers))
    }
    assert sid in {
        x["id"] for x in jlist(await client.get("/mcp-servers", headers=second_member.headers))
    }

    # by email: matched case-insensitively (the route lowercases the address)
    by_email = await client.patch(url, json={"email": member.email.upper()}, headers=admin.headers)
    assert by_email.status_code == 200, by_email.text
    assert jbody(by_email)["userId"] == member.id
    row = await db.server(sid)
    assert row is not None and row.user_id == member.id


async def test_assign_owner_error_details(
    client: AsyncClient, admin: Member, member: Member, servers: ServerApi
) -> None:
    created = await servers.create(member)
    url = f"/mcp-servers/{created['id']}/owner"
    no_target = await client.patch(url, json={}, headers=admin.headers)
    assert no_target.status_code == 422
    assert no_target.json() == {"detail": "must provide user_id or email"}

    bad_email = await client.patch(
        url, json={"email": "Nobody-Here@Example.com"}, headers=admin.headers
    )
    assert bad_email.status_code == 404
    assert bad_email.json() == {"detail": "user with email 'Nobody-Here@Example.com' not found"}

    bad_id = await client.patch(url, json={"userId": "ghost-user"}, headers=admin.headers)
    assert bad_id.status_code == 404
    assert bad_id.json() == {"detail": "user 'ghost-user' not found"}

    # the user lookup runs before the server lookup
    no_server = await client.patch(
        "/mcp-servers/nope/owner", json={"userId": member.id}, headers=admin.headers
    )
    assert no_server.status_code == 404
    assert no_server.json() == {"detail": "MCP server 'nope' not found."}
    no_both = await client.patch(
        "/mcp-servers/nope/owner", json={"userId": "ghost-user"}, headers=admin.headers
    )
    assert no_both.json() == {"detail": "user 'ghost-user' not found"}


# --------------------------------------------------------------------------- 5. oauth-config


async def test_oauth_config_patch_replaces_whole_config_and_does_not_preserve_secret(
    client: AsyncClient, member: Member, servers: ServerApi, db: Db
) -> None:
    """The route is PATCH /mcp-servers/{id}/oauth-config (not PUT) and REPLACES the stored config.

    Today an omitted clientSecret is NOT preserved: it is stored as null. A client that echoes
    the redacted marker would have the marker itself encrypted as the new secret.
    """
    original_secret = unique("orig-secret")
    created = await servers.create_oauth(
        member, oauthConfig={"clientSecret": original_secret, "audience": "old-extra"}
    )
    sid = created["id"]
    new_cfg: dict[str, Any] = {
        "authorizeUrl": "https://new.example.test/authorize",
        "tokenUrl": "https://new.example.test/token",
        "clientId": "new-client",
    }
    bystander = await servers.create_oauth(member)
    bystander_before = Db.snapshot(await db.server(bystander["id"]))
    await db.set_server(sid, updated_at=LONG_AGO)

    # 1) omitted secret -> cleared (null), scopes default to [], old extra keys are gone
    r = await client.patch(
        f"/mcp-servers/{sid}/oauth-config", json={"oauthConfig": new_cfg}, headers=member.headers
    )
    assert r.status_code == 200, r.text
    assert jbody(r)["hasOauthConfig"] is True and original_secret not in r.text
    assert iso_dt(jbody(r)["updatedAt"]) > LONG_AGO
    row = await db.server(sid)
    assert row is not None and near(row.updated_at, datetime.now(UTC))
    assert Db.snapshot(await db.server(bystander["id"])) == bystander_before
    assert row.oauth_config == {**new_cfg, "clientSecret": None, "scopes": []}

    # 2) a new secret is stored marked-encrypted and never returned
    fresh = unique("fresh-secret")
    r = await client.patch(
        f"/mcp-servers/{sid}/oauth-config",
        json={"oauthConfig": {**new_cfg, "clientSecret": fresh, "scopes": ["s1"], "extra": 1}},
        headers=member.headers,
    )
    assert r.status_code == 200 and fresh not in r.text
    stored = await db.oauth_config(sid)
    assert stored["clientSecret"].startswith(SECRET_ENC_PREFIX)
    assert decrypt_marked(stored["clientSecret"]) == fresh
    assert stored["scopes"] == ["s1"] and stored["extra"] == 1

    # 3) echoing the redacted marker does not preserve anything: the marker is the new secret
    r = await client.patch(
        f"/mcp-servers/{sid}/oauth-config",
        json={"oauthConfig": {**new_cfg, "clientSecret": REDACTED_MARKER}},
        headers=member.headers,
    )
    assert r.status_code == 200
    assert decrypt_marked((await db.oauth_config(sid))["clientSecret"]) == REDACTED_MARKER


async def test_oauth_config_patch_errors(
    client: AsyncClient,
    admin: Member,
    member: Member,
    second_member: Member,
    servers: ServerApi,
    db: Db,
) -> None:
    oauth = await servers.create_oauth(member, isShared=True)
    bearer_srv = await servers.create(member, authType="bearer", accessToken="t")
    cfg = {
        "oauthConfig": {
            "authorizeUrl": "https://x.example.test/a",
            "tokenUrl": "https://x.example.test/t",
            "clientId": "c",
        }
    }
    before = await db.server(oauth["id"])

    r = await client.patch(
        f"/mcp-servers/{oauth['id']}/oauth-config", json=cfg, headers=second_member.headers
    )
    assert r.status_code == 403 and r.json() == {"detail": "Only the owner may edit OAuth config."}
    r = await client.patch(  # admin is not special here: owner only
        f"/mcp-servers/{oauth['id']}/oauth-config", json=cfg, headers=admin.headers
    )
    assert r.status_code == 403
    after = await db.server(oauth["id"])
    assert before is not None and after is not None and after.oauth_config == before.oauth_config

    r = await client.patch(
        f"/mcp-servers/{bearer_srv['id']}/oauth-config", json=cfg, headers=member.headers
    )
    assert r.status_code == 422
    assert r.json() == {"detail": "MCP server authType must be 'oauth' (got 'bearer')."}
    r = await client.patch("/mcp-servers/nope/oauth-config", json=cfg, headers=member.headers)
    assert r.status_code == 404 and r.json() == {"detail": "MCP server 'nope' not found."}
    r = await client.patch(
        f"/mcp-servers/{oauth['id']}/oauth-config", json={"oauthConfig": {}}, headers=member.headers
    )
    assert r.status_code == 422  # authorizeUrl / tokenUrl / clientId are required


# --------------------------------------------------------------------------- 6. test-connection


async def test_test_connection_success_updates_status_tools_and_clears_error(
    client: AsyncClient, member: Member, servers: ServerApi, fake_mcp: FakeMcpFactory, db: Db
) -> None:
    fake = fake_mcp()
    created = await servers.create(
        member, url=fake.url, authType="bearer", accessToken="mcp-bearer-secret"
    )
    sid = created["id"]
    bystander = await servers.create(member, url=fake.url)
    bystander_before = Db.snapshot(await db.server(bystander["id"]))
    await db.set_server(
        sid, last_error="stale error", connection_status="error", updated_at=LONG_AGO
    )

    r = await client.post(f"/mcp-servers/{sid}/test-connection", headers=member.headers)
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "message": "connected; 2 tool(s) available"}

    # transport: initialize + tools/list for the health check, then tools/list again for the cache
    assert fake.methods() == ["initialize", "tools/list", "tools/list"]
    assert fake.authorizations() == {"Bearer mcp-bearer-secret"}

    row = await db.server(sid)
    assert row is not None
    assert row.connection_status == "connected" and row.last_error is None
    assert row.tools == [
        {"name": "alpha", "description": "first tool"},
        {"name": "beta", "description": "second tool"},
    ]
    assert row.last_tested is not None and near(row.last_tested, datetime.now(UTC))
    assert near(row.updated_at, datetime.now(UTC))  # advanced past the backdate
    assert Db.snapshot(await db.server(bystander["id"])) == bystander_before
    listed = next(
        s for s in jlist(await client.get("/mcp-servers", headers=member.headers)) if s["id"] == sid
    )
    assert ISO.match(listed["lastTested"]) and listed["connectionStatus"] == "connected"
    assert listed["lastError"] is None
    assert listed["tools"] == row.tools


async def test_test_connection_failures_record_error(
    client: AsyncClient, member: Member, servers: ServerApi, fake_mcp: FakeMcpFactory, db: Db
) -> None:
    # transport failure: HTTP 200 with ok=false, error recorded, cached tools untouched
    down = fake_mcp(connect_error=True)
    srv = await servers.create(member, url=down.url)
    bystander = await servers.create(member, url=down.url)
    bystander_before = Db.snapshot(await db.server(bystander["id"]))
    await db.set_server(srv["id"], tools=[{"name": "kept", "description": ""}])
    r = await client.post(f"/mcp-servers/{srv['id']}/test-connection", headers=member.headers)
    assert r.status_code == 200
    body = jbody(r)
    assert body["ok"] is False
    assert body["message"].startswith("MCPHTTPError: MCP initialize transport failed: ConnectError")
    row = await db.server(srv["id"])
    assert row is not None and row.connection_status == "error"
    assert row.last_error == body["message"] and row.last_tested is not None
    assert row.tools == [{"name": "kept", "description": ""}]
    assert Db.snapshot(await db.server(bystander["id"])) == bystander_before

    # non-2xx from the server
    broken = fake_mcp(http_status=500)
    srv2 = await servers.create(member, url=broken.url)
    r = await client.post(f"/mcp-servers/{srv2['id']}/test-connection", headers=member.headers)
    assert r.status_code == 200 and jbody(r)["ok"] is False
    assert jbody(r)["message"] == (
        "MCPHTTPError: MCP initialize returned HTTP 500: scripted failure"
    )
    row2 = await db.server(srv2["id"])
    assert row2 is not None and row2.connection_status == "error"
    assert row2.last_error == jbody(r)["message"]


async def test_test_connection_tools_list_failing_after_health_ok(
    client: AsyncClient, member: Member, servers: ServerApi, fake_mcp: FakeMcpFactory, db: Db
) -> None:
    fake = fake_mcp(
        fail_tools_list_from_call=2
    )  # health's tools/list passes, the cache fetch fails
    srv = await servers.create(member, url=fake.url)
    r = await client.post(f"/mcp-servers/{srv['id']}/test-connection", headers=member.headers)
    assert r.status_code == 200
    assert r.json() == {"ok": True, "message": "connected; 2 tool(s) available"}  # health said ok
    row = await db.server(srv["id"])
    assert row is not None and row.connection_status == "error"
    assert row.last_error is not None
    assert row.last_error.startswith("tools/list failed after initialize ok: ")
    assert "HTTP 500" in row.last_error
    assert row.last_tested is not None and row.tools is None


async def test_test_connection_unknown_server_404_and_no_ownership_check(
    client: AsyncClient,
    member: Member,
    second_member: Member,
    servers: ServerApi,
    fake_mcp: FakeMcpFactory,
    db: Db,
) -> None:
    r = await client.post("/mcp-servers/nope/test-connection", headers=member.headers)
    assert r.status_code == 404 and r.json() == {"detail": "MCP server 'nope' not found."}

    # Today ANY authenticated user can test ANY server, including another member's private one,
    # and the server's own bearer token is what gets sent (no visibility check).
    fake = fake_mcp()
    private = await servers.create(
        member, url=fake.url, authType="bearer", accessToken="owner-private-token", isShared=False
    )
    r = await client.post(
        f"/mcp-servers/{private['id']}/test-connection", headers=second_member.headers
    )
    assert r.status_code == 200 and jbody(r)["ok"] is True
    assert fake.authorizations() == {"Bearer owner-private-token"}
    row = await db.server(private["id"])
    assert row is not None and row.connection_status == "connected"
