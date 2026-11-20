import secrets
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db.models import ApiKey
from tests.integration.sqla_phase1.conftest import MakeUser, Member

pytestmark = pytest.mark.integration


async def test_duplicate_registration_is_409(client: AsyncClient, member: Member) -> None:
    r = await client.post(
        "/auth/register", json={"email": member.email, "password": "another-password-1"}
    )
    assert r.status_code == 409 and r.json() == {"detail": "email already registered"}


async def test_me_change_password_and_login(client: AsyncClient, member: Member) -> None:
    me = (await client.get("/auth/me", headers=member.headers)).json()
    assert me == {"id": member.id, "email": member.email, "displayName": None, "role": "member"}
    r = await client.post(
        "/auth/change-password",
        json={"currentPassword": "not-my-password", "newPassword": "brand-new-password-2"},
        headers=member.headers,
    )
    assert r.status_code == 401 and r.json() == {"detail": "current password is incorrect"}
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
    assert new.status_code == 200 and "accessToken" in new.json()


async def test_admin_me_reports_admin_role(
    client: AsyncClient, admin: Member, admin_headers: dict[str, str]
) -> None:
    me = (await client.get("/auth/me", headers=admin_headers)).json()
    assert me["id"] == admin.id and me["role"] == "admin"


async def test_deactivate_revokes_api_keys_and_blocks_access(
    client: AsyncClient,
    admin_headers: dict[str, str],
    member: Member,
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    created = await client.post("/api-keys", json={"label": "k"}, headers=member.headers)
    key = created.json()
    auth = {"Authorization": f"Bearer {key['key']}"}
    r = await client.post(
        f"/admin/users/{member.id}/role", json={"role": "admin"}, headers=admin_headers
    )
    assert r.status_code == 200 and r.json()["role"] == "admin" and r.json()["isActive"] is True
    r = await client.delete(f"/admin/users/{member.id}", headers=admin_headers)
    assert r.status_code == 200 and r.json()["isActive"] is False
    me = await client.get("/auth/me", headers=member.headers)
    assert me.status_code == 401 and me.json() == {"detail": "account is deactivated"}
    async with sessionmaker_() as s:
        revoked_at = await s.scalar(select(ApiKey.revoked_at).where(ApiKey.id == key["id"]))
    assert revoked_at is not None
    r = await client.post("/api/run/does-not-exist", json={}, headers=auth)
    assert r.status_code == 401 and r.json() == {"detail": "invalid API key"}
    login = await client.post(
        "/auth/login", json={"email": member.email, "password": member.password}
    )
    assert login.status_code == 403 and login.json() == {"detail": "account is deactivated"}
    refresh = await client.post("/auth/refresh", json={"refreshToken": member.refresh_token})
    assert refresh.status_code == 401 and refresh.json() == {"detail": "account is deactivated"}

    r = await client.post(f"/admin/users/{member.id}/reactivate", headers=admin_headers)
    assert r.status_code == 200 and r.json()["isActive"] is True
    me = await client.get("/auth/me", headers=member.headers)
    assert me.status_code == 200 and me.json()["id"] == member.id
    refresh = await client.post("/auth/refresh", json={"refreshToken": member.refresh_token})
    assert refresh.status_code == 200 and "accessToken" in refresh.json()
    # Reactivation does not resurrect revoked keys.
    r = await client.post("/api/run/does-not-exist", json={}, headers=auth)
    assert r.status_code == 401 and r.json() == {"detail": "invalid API key"}


async def test_admin_reset_password_flow(
    client: AsyncClient, admin_headers: dict[str, str], member: Member
) -> None:
    r = await client.post(f"/admin/users/{member.id}/reset-password", headers=admin_headers)
    assert r.status_code == 200
    temp = r.json()["temporaryPassword"]
    me = await client.get("/auth/me", headers=member.headers)
    assert me.status_code == 401 and me.json() == {"detail": "password change required"}
    refresh = await client.post("/auth/refresh", json={"refreshToken": member.refresh_token})
    assert refresh.status_code == 403 and refresh.json() == {"detail": "password change required"}
    login = await client.post("/auth/login", json={"email": member.email, "password": temp})
    assert login.status_code == 200, login.text
    assert set(login.json()) == {"mustChangePassword", "passwordChangeToken"}
    assert login.json()["mustChangePassword"] is True
    change = await client.post(
        "/auth/change-password",
        json={"currentPassword": temp, "newPassword": "after-reset-password-3"},
        headers={"Authorization": f"Bearer {login.json()['passwordChangeToken']}"},
    )
    assert change.status_code == 204, change.text
    again = await client.post(
        "/auth/login", json={"email": member.email, "password": "after-reset-password-3"}
    )
    assert again.status_code == 200 and "accessToken" in again.json()
    me = await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {again.json()['accessToken']}"}
    )
    assert me.status_code == 200 and me.json()["id"] == member.id


async def test_admin_user_errors_and_listing_order(
    client: AsyncClient, admin_headers: dict[str, str], make_user: MakeUser
) -> None:
    # Register limit: rate_limit_register_per_minute=5. tests/integration/conftest.py resets the
    # buckets per test, so this test (admin + 3 users = 4 registrations) fits; a 6th
    # registration in one test would get 429.
    tok = secrets.token_hex(4)
    for x in ("c", "b", "a"):  # registered in reverse order
        await make_user(email=f"p1-{tok}-{x}@example.com")
    listed = (await client.get("/admin/users", headers=admin_headers)).json()
    mine = [u["email"] for u in listed if u["email"].startswith(f"p1-{tok}-")]
    assert mine == [f"p1-{tok}-{x}@example.com" for x in ("a", "b", "c")]
    for path in ("reactivate", "role", "reset-password"):
        body: dict[str, Any] = {"role": "member"} if path == "role" else {}
        r = await client.post(f"/admin/users/nope/{path}", json=body, headers=admin_headers)
        assert r.status_code == 404 and r.json() == {"detail": "user 'nope' not found"}, path
    r = await client.delete("/admin/users/nope", headers=admin_headers)
    assert r.status_code == 404 and r.json() == {"detail": "user 'nope' not found"}


async def test_user_search_order_deactivation_and_display_name(
    client: AsyncClient, admin_headers: dict[str, str], make_user: MakeUser
) -> None:
    # Register limit: rate_limit_register_per_minute=5. tests/integration/conftest.py resets the
    # buckets per test, so this test (admin + 3 users = 4 registrations) fits; a 6th
    # registration in one test would get 429.
    tok = secrets.token_hex(4)
    c = await make_user(email=f"p1-{tok}-c@example.com", display_name=f"Quokka{tok} Cee")
    b = await make_user(email=f"p1-{tok}-b@example.com")
    a = await make_user(email=f"p1-{tok}-a@example.com")
    emails = [a.email, b.email, c.email]
    hits = (await client.get(f"/users/search?q=p1-{tok}", headers=a.headers)).json()
    assert [h["email"] for h in hits] == emails
    assert set(hits[0]) == {"id", "email", "displayName"}
    assert [h["displayName"] for h in hits] == [None, None, f"Quokka{tok} Cee"]
    # displayName match, case-insensitive; matches by name only (email has no "quokka")
    hits = (await client.get(f"/users/search?q=QUOKKA{tok}", headers=a.headers)).json()
    assert [h["id"] for h in hits] == [c.id]
    # upper-case email match
    hits = (await client.get(f"/users/search?q=P1-{tok}-B", headers=a.headers)).json()
    assert [h["id"] for h in hits] == [b.id]
    # a deactivated user is excluded
    r = await client.delete(f"/admin/users/{b.id}", headers=admin_headers)
    assert r.status_code == 200
    hits = (await client.get(f"/users/search?q=p1-{tok}", headers=a.headers)).json()
    assert [h["email"] for h in hits] == [a.email, c.email]


async def test_user_search_does_not_escape_like_wildcards_today(
    client: AsyncClient, member: Member
) -> None:
    # PINNED TODAY'S BEHAVIOUR (Prisma `contains`): "_" and "%" are NOT escaped, so
    # "p1_<hex>" matches the real address "p1-<hex>@...". If a later task deliberately
    # escapes wildcards, update this test and say so.
    local = member.email.split("@")[0]  # "p1-<hex>"
    hex_part = local.split("-", 1)[1]
    for q in (f"p1_{hex_part}", f"p1%25{hex_part}"):
        hits = (await client.get(f"/users/search?q={q}", headers=member.headers)).json()
        assert [h["id"] for h in hits] == [member.id], q
