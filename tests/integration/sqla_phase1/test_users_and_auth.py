import secrets

import pytest
from httpx import AsyncClient

from tests.integration.sqla_phase1.conftest import Member

pytestmark = pytest.mark.integration


async def test_duplicate_registration_is_409(client: AsyncClient, member: Member) -> None:
    r = await client.post(
        "/auth/register", json={"email": member.email, "password": "another-password-1"}
    )
    assert r.status_code == 409


async def test_me_change_password_and_login(client: AsyncClient, member: Member) -> None:
    me = (await client.get("/auth/me", headers=member.headers)).json()
    assert me == {"id": member.id, "email": member.email, "displayName": None, "role": "member"}
    r = await client.post(
        "/auth/change-password",
        json={"currentPassword": member.password, "newPassword": "brand-new-password-2"},
        headers=member.headers,
    )
    assert r.status_code == 204, r.text
    old = await client.post(
        "/auth/login", json={"email": member.email, "password": member.password}
    )
    assert old.status_code == 401
    new = await client.post(
        "/auth/login", json={"email": member.email, "password": "brand-new-password-2"}
    )
    assert new.status_code == 200


async def test_admin_role_deactivate_reactivate_and_reset(
    client: AsyncClient, admin_headers: dict[str, str], member: Member
) -> None:
    r = await client.post(
        f"/admin/users/{member.id}/role", json={"role": "admin"}, headers=admin_headers
    )
    assert r.status_code == 200 and r.json()["role"] == "admin" and r.json()["isActive"] is True
    r = await client.delete(f"/admin/users/{member.id}", headers=admin_headers)
    assert r.json()["isActive"] is False
    assert (await client.get("/auth/me", headers=member.headers)).status_code == 401
    r = await client.post(f"/admin/users/{member.id}/reactivate", headers=admin_headers)
    assert r.json()["isActive"] is True
    r = await client.post(f"/admin/users/{member.id}/reset-password", headers=admin_headers)
    temp = r.json()["temporaryPassword"]
    assert (await client.get("/auth/me", headers=member.headers)).status_code == 401
    login = await client.post("/auth/login", json={"email": member.email, "password": temp})
    assert login.status_code == 200, login.text
    listed = (await client.get("/admin/users", headers=admin_headers)).json()
    emails = [u["email"] for u in listed]
    assert member.email in emails and emails == sorted(emails)
    r = await client.post("/admin/users/nope/reactivate", headers=admin_headers)
    assert r.status_code == 404


async def test_user_search_is_case_insensitive(client: AsyncClient, member: Member) -> None:
    r = await client.get(f"/users/search?q={member.email[:8].upper()}", headers=member.headers)
    hits = r.json()
    assert any(h["id"] == member.id for h in hits)
    assert set(hits[0]) == {"id", "email", "displayName"}
    token = secrets.token_hex(3)
    wild = (await client.get(f"/users/search?q=p1_{token}", headers=member.headers)).json()
    assert wild == []


async def test_user_search_does_not_escape_like_wildcards_today(
    client: AsyncClient, member: Member
) -> None:
    # PINNED TODAY'S BEHAVIOUR (Prisma `contains`): "_" and "%" are NOT escaped, so
    # "p1_<hex>" matches the real address "p1-<hex>@...". The plan expected literal
    # treatment; the plan's random-token query is vacuous (it can never match either way).
    # If a later task deliberately escapes wildcards, update this test and say so.
    local = member.email.split("@")[0]  # "p1-<hex>"
    hex_part = local.split("-", 1)[1]
    for q in (f"p1_{hex_part}", f"p1%25{hex_part}"):
        hits = (await client.get(f"/users/search?q={q}", headers=member.headers)).json()
        assert [h["id"] for h in hits] == [member.id], q
