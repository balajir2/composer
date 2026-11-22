"""Google Drive cloud-storage connections: authorize, callback, listing, picker token.

Pins today's (Prisma-backed) behaviour. Test group 12 of the phase 2 plan. Google is mocked
with httpx_mock; the Drive state is a self-contained encrypted payload (no table).
"""

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient, Response

from src.integrations.google_drive.oauth import OAUTH_SCOPES
from src.security.encryption import decrypt, encrypt
from tests.integration.sqla_phase2.conftest import (
    HTTPX_MARK,
    LONG_AGO,
    Db,
    DriveConnectionMissingError,
    FakeEndpoint,
    FakeEndpointFactory,
    MakeUser,
    Member,
    Phase2Adapter,
    jbody,
    jlist,
    near,
    query_of,
    split_url,
    unique,
)

pytestmark = [pytest.mark.integration, HTTPX_MARK]

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
CALLBACK_URI = "http://backend.test/cloud-storage/google-drive/callback"


# --------------------------------------------------------------------------- helpers


def popup(resp: Response) -> dict[str, str]:
    """The postMessage payload embedded in the popup-closing HTML page."""
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/html")
    m = re.search(r"postMessage\((\{.*?\}), '\*'\)", resp.text)
    assert m is not None, resp.text
    payload: dict[str, str] = json.loads(m.group(1))
    assert payload["type"] == "composer:google-drive-oauth"
    return payload


async def drive_state(client: AsyncClient, user: Member) -> str:
    r = await client.get("/cloud-storage/google-drive/authorize", headers=user.headers)
    assert r.status_code == 200, r.text
    return query_of(jbody(r)["authorizeUrl"])["state"]


def drive_callback(client: AsyncClient, state: str, code: str = "google-code") -> Any:
    return client.get("/cloud-storage/google-drive/callback", params={"code": code, "state": state})


def google(fake_endpoint: FakeEndpointFactory) -> tuple[FakeEndpoint, FakeEndpoint]:
    return fake_endpoint(GOOGLE_TOKEN_URL), fake_endpoint(GOOGLE_USERINFO_URL, method="GET")


# --------------------------------------------------------------------------- authorize


async def test_authorize_url_carries_scopes_offline_access_and_encrypted_state(
    client: AsyncClient, member: Member
) -> None:
    r = await client.get("/cloud-storage/google-drive/authorize", headers=member.headers)
    assert r.status_code == 200
    body = jbody(r)
    assert set(body) == {"authorizeUrl"}
    base, _ = split_url(body["authorizeUrl"])
    assert base == "https://accounts.google.com/o/oauth2/v2/auth"
    q = query_of(body["authorizeUrl"])
    assert q["client_id"] == "google-client-id-test"
    assert q["redirect_uri"] == CALLBACK_URI
    assert q["response_type"] == "code" and q["access_type"] == "offline"
    assert q["prompt"] == "consent"
    assert q["scope"].split() == [
        "https://www.googleapis.com/auth/drive.readonly",
        "https://www.googleapis.com/auth/drive.metadata",
        "https://www.googleapis.com/auth/userinfo.email",
    ]
    assert member.id not in q["state"]  # opaque (encrypted), not a readable user id
    assert "google-client-secret-test" not in r.text


# --------------------------------------------------------------------------- callback


async def test_callback_creates_connection_and_a_second_grant_updates_it_in_place_keeping_the_refresh_token_and_requested_scopes(
    client: AsyncClient,
    member: Member,
    db: Db,
    fake_endpoint: FakeEndpointFactory,
) -> None:
    token_ep, userinfo_ep = google(fake_endpoint)
    email = f"{unique('acct')}@gmail.test"
    state = await drive_state(client, member)

    token_ep.push(
        {"access_token": "ga-1", "refresh_token": "gr-1", "expires_in": 3600, "scope": "s1 s2"}
    )
    userinfo_ep.push({"email": email})
    resp = await drive_callback(client, state, code="code-1")
    assert popup(resp) == {
        "type": "composer:google-drive-oauth",
        "status": "success",
        "detail": "",
    }
    assert token_ep.forms() == [
        {
            "grant_type": "authorization_code",
            "code": "code-1",
            "redirect_uri": CALLBACK_URI,
            "client_id": "google-client-id-test",
            "client_secret": "google-client-secret-test",
        }
    ]  # Google needs no RFC 8707 `resource`
    assert userinfo_ep.requests[0].headers["authorization"] == "Bearer ga-1"

    rows = await db.connections_for(member.id)
    assert len(rows) == 1
    first = rows[0]
    assert first.provider == "google-drive" and first.account_email == email
    assert (
        first.encrypted_access_token not in ("ga-1", "")
        and "ga-1" not in first.encrypted_access_token
    )
    assert first.encrypted_refresh_token is not None and "gr-1" not in first.encrypted_refresh_token
    assert decrypt(first.encrypted_access_token) == "ga-1"
    assert decrypt(first.encrypted_refresh_token) == "gr-1"
    assert first.scope == "s1 s2"
    assert near(first.expires_at, datetime.now(UTC) + timedelta(seconds=3600))
    await db.set_connection(first.id, updated_at=LONG_AGO)

    # Same account again (the Drive state is TTL-bound, NOT single-use, so it can be replayed):
    # same row, new access token. Google omitted refresh_token and scope on the re-grant: the
    # stored refresh token is kept, and the scope becomes the REQUESTED scopes (RFC 6749 5.1).
    token_ep.push({"access_token": "ga-2", "expires_in": 1800})
    userinfo_ep.push({"email": email})
    assert popup(await drive_callback(client, state, code="code-2"))["status"] == "success"
    rows = await db.connections_for(member.id)
    assert len(rows) == 1 and rows[0].id == first.id
    second = rows[0]
    assert decrypt(second.encrypted_access_token) == "ga-2"
    assert second.encrypted_refresh_token == first.encrypted_refresh_token  # kept byte-for-byte
    assert second.encrypted_refresh_token is not None
    assert decrypt(second.encrypted_refresh_token) == "gr-1"
    assert second.scope == OAUTH_SCOPES  # the new payload carried no scope: the requested ones
    assert near(second.expires_at, datetime.now(UTC) + timedelta(seconds=1800))
    assert second.created_at == first.created_at
    assert second.updated_at > LONG_AGO and near(second.updated_at, datetime.now(UTC))

    # A different account for the same user and provider is a separate connection.
    token_ep.push({"access_token": "ga-3", "refresh_token": "gr-3"})
    userinfo_ep.push({"email": f"other-{email}"})
    assert popup(await drive_callback(client, state))["status"] == "success"
    assert len(await db.connections_for(member.id)) == 2


async def test_callback_failures_render_error_popup_and_store_nothing(
    client: AsyncClient,
    member: Member,
    db: Db,
    fake_endpoint: FakeEndpointFactory,
) -> None:
    token_ep, userinfo_ep = google(fake_endpoint)

    tampered = popup(await drive_callback(client, "not-an-encrypted-state"))
    assert tampered["status"] == "error"
    assert tampered["detail"].startswith("OAuth state is invalid or tampered: ")

    expired_state = encrypt(
        json.dumps(
            {"user_id": member.id, "exp": (datetime.now(UTC) - timedelta(minutes=1)).isoformat()}
        )
    )
    expired = popup(await drive_callback(client, expired_state))
    assert expired == {
        "type": "composer:google-drive-oauth",
        "status": "error",
        "detail": "OAuth state has expired.",
    }
    assert token_ep.requests == []  # state is checked before Google is contacted

    state = await drive_state(client, member)
    token_ep.push({"error": "invalid_grant"}, status=400)
    bad_exchange = popup(await drive_callback(client, state))
    assert bad_exchange["status"] == "error"
    assert bad_exchange["detail"].startswith("Google token exchange failed (HTTP 400): ")

    token_ep.push({"access_token": "ga"})
    userinfo_ep.push({"error": "unauthorized"}, status=401)
    bad_userinfo = popup(await drive_callback(client, state))
    assert bad_userinfo["status"] == "error"
    assert bad_userinfo["detail"].startswith("Google userinfo fetch failed (HTTP 401): ")

    assert await db.connections_for(member.id) == []
    missing = await client.get("/cloud-storage/google-drive/callback", params={"code": "c"})
    assert missing.status_code == 422


# --------------------------------------------------------------------------- listing


async def test_connections_list_is_scoped_to_the_caller_and_filters_by_provider(
    client: AsyncClient, member: Member, second_member: Member, make_user: MakeUser, db: Db
) -> None:
    a = await db.seed_connection(member.id, email="a@gmail.test")
    b = await db.seed_connection(member.id, email="b@gmail.test")
    c = await db.seed_connection(member.id, provider="dropbox", email="c@dropbox.test")
    other = await db.seed_connection(second_member.id, email="x@gmail.test")

    r = await client.get("/cloud-storage/connections", headers=member.headers)
    assert r.status_code == 200
    rows = jlist(r)
    assert {x["id"] for x in rows} == {a, b, c}  # exactly the caller's, whatever the order
    assert all(set(x) == {"id", "provider", "accountEmail"} for x in rows)
    by_id = {x["id"]: x for x in rows}
    assert by_id[a] == {"id": a, "provider": "google-drive", "accountEmail": "a@gmail.test"}
    assert by_id[c]["provider"] == "dropbox"
    assert "drive-access" not in r.text and "drive-refresh" not in r.text

    only_google = jlist(
        await client.get(
            "/cloud-storage/connections",
            params={"provider": "google-drive"},
            headers=member.headers,
        )
    )
    assert {x["id"] for x in only_google} == {a, b}
    only_dropbox = jlist(
        await client.get(
            "/cloud-storage/connections", params={"provider": "dropbox"}, headers=member.headers
        )
    )
    assert {x["id"] for x in only_dropbox} == {c}
    nothing = jlist(
        await client.get(
            "/cloud-storage/connections", params={"provider": "box"}, headers=member.headers
        )
    )
    assert nothing == []

    theirs = jlist(await client.get("/cloud-storage/connections", headers=second_member.headers))
    assert {x["id"] for x in theirs} == {other}
    fresh = await client.get("/cloud-storage/connections", headers=(await make_user()).headers)
    assert fresh.status_code == 200 and fresh.json() == []


# --------------------------------------------------------------------------- picker token


async def test_picker_token_returns_stored_token_while_valid(
    client: AsyncClient, member: Member, db: Db, fake_endpoint: FakeEndpointFactory
) -> None:
    token_ep, _ = google(fake_endpoint)
    cid = await db.seed_connection(
        member.id, access="valid-access", expires_in=timedelta(minutes=30)
    )
    r = await client.post(f"/cloud-storage/connections/{cid}/picker-token", headers=member.headers)
    assert r.status_code == 200
    assert r.json() == {"accessToken": "valid-access"}
    never = await db.seed_connection(member.id, access="no-expiry", expires_in=None)
    r = await client.post(
        f"/cloud-storage/connections/{never}/picker-token", headers=member.headers
    )
    assert r.json() == {"accessToken": "no-expiry"}
    assert token_ep.requests == []


async def test_picker_token_refreshes_expired_token_and_updates_row(
    client: AsyncClient,
    member: Member,
    second_member: Member,
    db: Db,
    fake_endpoint: FakeEndpointFactory,
) -> None:
    token_ep, _ = google(fake_endpoint)
    cid = await db.seed_connection(
        member.id,
        access="old",
        refresh="old-refresh",
        expires_in=timedelta(seconds=-30),
        scope="s1 s2",
    )
    await db.set_connection(cid, updated_at=LONG_AGO)
    original = await db.connection(cid)
    assert original is not None
    mine_too = await db.seed_connection(member.id, expires_in=timedelta(seconds=-30))
    theirs = await db.seed_connection(second_member.id, expires_in=timedelta(seconds=-30))
    bystanders = {
        i: Db.snapshot(await db.connection(i))  # pyright: ignore[reportArgumentType]
        for i in (mine_too, theirs)
    }
    token_ep.push({"access_token": "refreshed", "expires_in": 3000})  # refresh token not rotated

    r = await client.post(f"/cloud-storage/connections/{cid}/picker-token", headers=member.headers)
    assert r.status_code == 200 and r.json() == {"accessToken": "refreshed"}
    assert token_ep.forms() == [
        {
            "grant_type": "refresh_token",
            "refresh_token": "old-refresh",
            "client_id": "google-client-id-test",
            "client_secret": "google-client-secret-test",
        }
    ]
    row = await db.connection(cid)
    assert row is not None
    assert decrypt(row.encrypted_access_token) == "refreshed"
    assert row.encrypted_refresh_token is not None
    assert decrypt(row.encrypted_refresh_token) == "old-refresh"  # kept when Google omits one
    # columns a refresh must not touch
    assert row.scope == "s1 s2" and row.created_at == original.created_at
    assert row.user_id == member.id and row.provider == "google-drive"
    assert row.account_email == original.account_email
    for i, snap in bystanders.items():
        assert Db.snapshot(await db.connection(i)) == snap  # pyright: ignore[reportArgumentType]
    assert near(row.expires_at, datetime.now(UTC) + timedelta(seconds=3000))
    assert row.updated_at > LONG_AGO and near(row.updated_at, datetime.now(UTC))

    # inside the 60s buffer counts as expired; a rotated refresh token is persisted
    await db.set_connection(cid, expires_at=datetime.now(UTC) + timedelta(seconds=20))
    token_ep.push({"access_token": "refreshed-2", "refresh_token": "rotated", "expires_in": 3600})
    r = await client.post(f"/cloud-storage/connections/{cid}/picker-token", headers=member.headers)
    assert r.json() == {"accessToken": "refreshed-2"}
    row = await db.connection(cid)
    assert row is not None and row.encrypted_refresh_token is not None
    assert decrypt(row.encrypted_refresh_token) == "rotated"
    assert len(token_ep.requests) == 2


async def test_picker_token_error_statuses(
    client: AsyncClient,
    member: Member,
    second_member: Member,
    db: Db,
    fake_endpoint: FakeEndpointFactory,
) -> None:
    token_ep, _ = google(fake_endpoint)
    theirs = await db.seed_connection(second_member.id)

    # another user's connection and an unknown id are indistinguishable: 404, same detail
    for cid in (theirs, "no-such-connection"):
        r = await client.post(
            f"/cloud-storage/connections/{cid}/picker-token", headers=member.headers
        )
        assert r.status_code == 404
        assert r.json() == {"detail": "Connection not found."}
    assert token_ep.requests == []
    owner = await client.post(
        f"/cloud-storage/connections/{theirs}/picker-token", headers=second_member.headers
    )
    assert owner.status_code == 200 and owner.json() == {"accessToken": "drive-access"}

    # expired, no refresh token: 409 (the UI shows "reconnect")
    stuck = await db.seed_connection(member.id, refresh=None, expires_in=timedelta(minutes=-1))
    r = await client.post(
        f"/cloud-storage/connections/{stuck}/picker-token", headers=member.headers
    )
    assert r.status_code == 409
    assert r.json() == {
        "detail": f"Drive connection {stuck!r} expired with no refresh token; user must reconnect."
    }

    # Google rejects the refresh: 409 and the row is untouched
    expired = await db.seed_connection(member.id, expires_in=timedelta(minutes=-1))
    before = await db.connection(expired)
    token_ep.push({"error": "invalid_grant"}, status=400)
    r = await client.post(
        f"/cloud-storage/connections/{expired}/picker-token", headers=member.headers
    )
    assert r.status_code == 409
    assert r.json()["detail"].startswith("Google token refresh failed (HTTP 400): ")
    after = await db.connection(expired)
    assert before is not None and after is not None
    assert after.encrypted_access_token == before.encrypted_access_token
    assert after.updated_at == before.updated_at


async def test_drive_token_helper_for_missing_connection(adapter: Phase2Adapter) -> None:
    with pytest.raises(DriveConnectionMissingError) as exc:
        await adapter.valid_drive_token("no-such-connection")
    assert str(exc.value) == "No CloudStorageConnection found for id 'no-such-connection'."
