"""Concurrent refreshes of one expired token post to the IdP once (advisory lock).

New behaviour on top of the phase 2 oracle (which stays unchanged): two callers that find the
same token expired used to both POST the same refresh token, which IdPs that detect refresh
token reuse may revoke. Real Postgres, fake IdP; the second caller must wait, re-read the
refreshed row and return it without posting.
"""

import asyncio
from datetime import timedelta
from typing import Any

import pytest

from src.security.encryption import decrypt
from tests.integration.sqla_phase2.conftest import (
    HTTPX_MARK,
    Db,
    FakeEndpointFactory,
    Member,
    Phase2Adapter,
    ServerApi,
)
from tests.integration.sqla_phase2.test_cloud_storage import google

pytestmark = [pytest.mark.integration, HTTPX_MARK]


def _slow_idp(monkeypatch: pytest.MonkeyPatch, module: Any) -> None:
    """Make the token POST take 1.5 s so concurrent callers really overlap (the mocked HTTP
    answers instantly, which would let them run one after the other and hide a missing lock)."""
    original = module._post_token_form  # pyright: ignore[reportPrivateUsage]

    async def _slow(*args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(1.5)
        return await original(*args, **kwargs)

    monkeypatch.setattr(module, "_post_token_form", _slow)


async def test_two_concurrent_mcp_callers_trigger_one_refresh_post(
    member: Member,
    servers: ServerApi,
    db: Db,
    adapter: Phase2Adapter,
    fake_endpoint: FakeEndpointFactory,
) -> None:
    created = await servers.create_oauth(member)
    sid = created["id"]
    token_ep = fake_endpoint((await db.oauth_config(sid))["tokenUrl"])
    await db.seed_token(
        sid, member.id, access="old", refresh="old-refresh", expires_in=timedelta(minutes=-5)
    )
    token_ep.push(
        {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600}
    )

    results = await asyncio.gather(
        adapter.valid_access_token(sid, member.id), adapter.valid_access_token(sid, member.id)
    )

    assert results == ["new-access", "new-access"]
    assert len(token_ep.requests) == 1  # exactly one refresh POST
    row = await db.token(sid, member.id)
    assert row is not None and row.encrypted_refresh_token is not None
    assert decrypt(row.encrypted_access_token) == "new-access"
    assert decrypt(row.encrypted_refresh_token) == "new-refresh"


async def test_two_concurrent_drive_callers_trigger_one_refresh_post(
    member: Member,
    db: Db,
    adapter: Phase2Adapter,
    fake_endpoint: FakeEndpointFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.integrations.google_drive.oauth as drive_oauth

    _slow_idp(monkeypatch, drive_oauth)
    token_ep, _ = google(fake_endpoint)
    cid = await db.seed_connection(
        member.id, access="old", refresh="old-refresh", expires_in=timedelta(seconds=-30)
    )
    token_ep.push({"access_token": "new-access", "refresh_token": "rotated", "expires_in": 3600})

    results = await asyncio.gather(adapter.valid_drive_token(cid), adapter.valid_drive_token(cid))

    assert results == ["new-access", "new-access"]
    assert len(token_ep.requests) == 1
    row = await db.connection(cid)
    assert row is not None and row.encrypted_refresh_token is not None
    assert decrypt(row.encrypted_access_token) == "new-access"
    assert decrypt(row.encrypted_refresh_token) == "rotated"


async def test_failed_refresh_releases_the_lock_and_leaves_the_row_unchanged(
    member: Member,
    servers: ServerApi,
    db: Db,
    adapter: Phase2Adapter,
    fake_endpoint: FakeEndpointFactory,
) -> None:
    from src.mcp.oauth import TokenRefreshError

    created = await servers.create_oauth(member)
    sid = created["id"]
    token_ep = fake_endpoint((await db.oauth_config(sid))["tokenUrl"])
    await db.seed_token(
        sid, member.id, access="old", refresh="old-refresh", expires_in=timedelta(minutes=-5)
    )
    before = Db.snapshot(await db.token(sid, member.id))
    token_ep.push({"error": "invalid_grant"}, status=400)
    with pytest.raises(TokenRefreshError):
        await adapter.valid_access_token(sid, member.id)
    assert Db.snapshot(await db.token(sid, member.id)) == before
    # the lock is gone: the next caller refreshes without waiting
    token_ep.push({"access_token": "new-access", "expires_in": 3600})
    assert await adapter.valid_access_token(sid, member.id) == "new-access"
