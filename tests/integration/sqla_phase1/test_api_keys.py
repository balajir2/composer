import asyncio

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db.models import ApiKey
from tests.integration.sqla_phase1.conftest import ISO, Member, bearer

pytestmark = pytest.mark.integration
RUN = "/api/run/does-not-exist"  # POST /api/run/{slug}: valid key + unknown slug is 404


async def test_api_key_lifecycle_and_external_auth(client: AsyncClient, member: Member) -> None:
    created = await client.post(
        "/api-keys",
        json={"label": "ci", "expiresAt": "2099-01-01T00:00:00"},
        headers=member.headers,
    )
    assert created.status_code == 201, created.text  # naive expiresAt is accepted today
    body = created.json()
    assert body["key"].startswith("ck_") and body["expiresAt"] == "2099-01-01T00:00:00Z"
    assert ISO.match(body["createdAt"]) and body["label"] == "ci" and body["keyPrefix"]
    listed = (await client.get("/api-keys", headers=member.headers)).json()
    assert [k["id"] for k in listed] == [body["id"]] and "key" not in listed[0]
    assert listed[0]["lastUsedAt"] is None and listed[0]["revokedAt"] is None
    assert listed[0]["expiresAt"] == "2099-01-01T00:00:00Z"
    auth = bearer(body["key"])
    ext = await client.post(RUN, json={}, headers=auth)
    assert ext.status_code == 404, ext.text
    after = (await client.get("/api-keys", headers=member.headers)).json()
    assert ISO.match(after[0]["lastUsedAt"]) and ISO.match(after[0]["createdAt"])
    r = await client.delete(f"/api-keys/{body['id']}", headers=member.headers)
    assert r.status_code == 204
    first = (await client.get("/api-keys", headers=member.headers)).json()[0]["revokedAt"]
    r = await client.delete(f"/api-keys/{body['id']}", headers=member.headers)
    assert r.status_code == 204  # idempotent
    second = (await client.get("/api-keys", headers=member.headers)).json()[0]["revokedAt"]
    assert first is not None and second == first  # not re-stamped
    revoked = await client.post(RUN, json={}, headers=auth)
    assert revoked.status_code == 401 and revoked.json() == {"detail": "invalid API key"}
    after = (await client.get("/api-keys", headers=member.headers)).json()
    assert ISO.match(after[0]["revokedAt"])
    r = await client.delete("/api-keys/nope", headers=member.headers)
    assert r.status_code == 404 and r.json() == {"detail": "API key 'nope' not found."}


async def test_api_keys_list_newest_first(client: AsyncClient, member: Member) -> None:
    ids: list[str] = []
    for label in ("k1", "k2", "k3"):
        r = await client.post("/api-keys", json={"label": label}, headers=member.headers)
        assert r.status_code == 201
        ids.append(r.json()["id"])
        # Distinct created_at for newest-first ordering. Windows' clock ticks about 15.6 ms
        # and the timestamp column has millisecond resolution, so a 20 ms gap could still
        # land two keys on one value and leave the order undefined.
        await asyncio.sleep(0.05)
    listed = (await client.get("/api-keys", headers=member.headers)).json()
    assert [k["id"] for k in listed] == list(reversed(ids))
    assert [k["label"] for k in listed] == ["k3", "k2", "k1"]
    assert listed[0]["expiresAt"] is None


async def test_expired_api_key_is_rejected(
    client: AsyncClient, member: Member, sessionmaker_: async_sessionmaker[AsyncSession]
) -> None:
    key = (await client.post("/api-keys", json={"label": "old"}, headers=member.headers)).json()
    async with sessionmaker_() as s:
        await s.execute(
            text("UPDATE api_keys SET expires_at='2000-01-01' WHERE id=:i"), {"i": key["id"]}
        )
        await s.commit()
    r = await client.post(RUN, json={}, headers=bearer(key["key"]))
    assert r.status_code == 401 and r.json() == {"detail": "API key expired"}


async def test_cannot_revoke_another_members_key(
    client: AsyncClient, member: Member, second_member: Member
) -> None:
    key = (await client.post("/api-keys", json={"label": "mine"}, headers=member.headers)).json()
    r = await client.delete(f"/api-keys/{key['id']}", headers=second_member.headers)
    assert r.status_code == 404
    assert (await client.get("/api-keys", headers=second_member.headers)).json() == []
    listed = (await client.get("/api-keys", headers=member.headers)).json()
    assert listed[0]["revokedAt"] is None
    assert (await client.post(RUN, json={}, headers=bearer(key["key"]))).status_code == 404


async def test_admin_revoke_api_key(
    client: AsyncClient,
    admin_headers: dict[str, str],
    member: Member,
    second_member: Member,
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    key = (await client.post("/api-keys", json={"label": "adm"}, headers=member.headers)).json()
    url = f"/admin/users/{member.id}/api-keys/{key['id']}/revoke"
    r = await client.post(
        f"/admin/users/{second_member.id}/api-keys/{key['id']}/revoke", headers=admin_headers
    )
    assert r.status_code == 404  # key belongs to someone else
    r = await client.post(f"/admin/users/{member.id}/api-keys/nope/revoke", headers=admin_headers)
    assert r.status_code == 404
    r = await client.post(url, headers=member.headers)
    assert r.status_code == 403
    assert (await client.post(RUN, json={}, headers=bearer(key["key"]))).status_code == 404
    assert (await client.post(url, headers=admin_headers)).status_code == 204
    async with sessionmaker_() as s:
        first = await s.scalar(select(ApiKey.revoked_at).where(ApiKey.id == key["id"]))
    assert (await client.post(url, headers=admin_headers)).status_code == 204  # idempotent
    async with sessionmaker_() as s:
        second = await s.scalar(select(ApiKey.revoked_at).where(ApiKey.id == key["id"]))
    assert first is not None and second == first  # not re-stamped
    r = await client.post(RUN, json={}, headers=bearer(key["key"]))
    assert r.status_code == 401 and r.json() == {"detail": "invalid API key"}
    listed = (await client.get("/api-keys", headers=member.headers)).json()
    assert ISO.match(listed[0]["revokedAt"])
