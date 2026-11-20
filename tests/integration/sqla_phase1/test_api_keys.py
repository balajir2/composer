import pytest
from httpx import AsyncClient

from tests.integration.sqla_phase1.conftest import Member

pytestmark = pytest.mark.integration


async def test_api_key_lifecycle_and_external_auth(client: AsyncClient, member: Member) -> None:
    created = await client.post(
        "/api-keys",
        json={"label": "ci", "expiresAt": "2099-01-01T00:00:00"},
        headers=member.headers,
    )
    assert created.status_code == 201, created.text  # naive expiresAt is accepted today
    body = created.json()
    assert body["key"].startswith("ck_") and body["expiresAt"].startswith("2099-01-01T00:00:00")
    listed = (await client.get("/api-keys", headers=member.headers)).json()
    assert [k["id"] for k in listed] == [body["id"]] and "key" not in listed[0]
    assert listed[0]["lastUsedAt"] is None
    # Today: a valid ck_ key + unknown slug is 404 (not 401); a revoked key is 401.
    auth = {"Authorization": f"Bearer {body['key']}"}
    ext = await client.post("/api/run/does-not-exist", json={}, headers=auth)
    assert ext.status_code == 404, ext.text
    after = (await client.get("/api-keys", headers=member.headers)).json()
    assert after[0]["lastUsedAt"] is not None
    r = await client.delete(f"/api-keys/{body['id']}", headers=member.headers)
    assert r.status_code == 204
    r = await client.delete(f"/api-keys/{body['id']}", headers=member.headers)
    assert r.status_code == 204  # idempotent
    revoked = await client.post("/api/run/does-not-exist", json={}, headers=auth)
    assert revoked.status_code == 401
    assert (await client.delete("/api-keys/nope", headers=member.headers)).status_code == 404
