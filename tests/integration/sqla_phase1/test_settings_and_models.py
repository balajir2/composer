import secrets
from datetime import datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db.models import DeploymentSetting, LlmModel
from tests.integration.sqla_phase1.conftest import ISO, Member

pytestmark = pytest.mark.integration


async def test_deployment_setting_create_update_list_get(
    client: AsyncClient,
    admin_headers: dict[str, str],
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    key = f"p1.test.{secrets.token_hex(4)}"
    url = f"/admin/deployment-settings/{key}"
    try:
        r = await client.get(url, headers=admin_headers)
        assert r.status_code == 404 and r.json() == {"detail": f"setting '{key}' not set"}
        r = await client.put(url, json={"value": "a"}, headers=admin_headers)
        assert r.status_code == 200 and r.json() == {"key": key, "value": "a"}
        # Backdate so a port that forgets to bump updated_at on update is caught.
        async with sessionmaker_() as s:
            await s.execute(
                text("UPDATE deployment_settings SET updated_at='2000-01-01' WHERE key=:k"),
                {"k": key},
            )
            await s.commit()
        r = await client.put(url, json={"value": "b"}, headers=admin_headers)
        assert r.json() == {"key": key, "value": "b"}
        async with sessionmaker_() as s:
            stamped = await s.scalar(
                select(DeploymentSetting.updated_at).where(DeploymentSetting.key == key)
            )
        assert stamped is not None and stamped > datetime(2020, 1, 1, tzinfo=stamped.tzinfo)
        r = await client.get(url, headers=admin_headers)
        assert r.status_code == 200 and r.json() == {"key": key, "value": "b"}
    finally:
        async with sessionmaker_() as s:
            await s.execute(delete(DeploymentSetting).where(DeploymentSetting.key == key))
            await s.commit()


async def test_deployment_settings_list_order(
    client: AsyncClient,
    admin_headers: dict[str, str],
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    tok = secrets.token_hex(4)
    keys = [f"p1.test.{tok}.{x}" for x in ("c", "b", "a")]  # inserted in reverse order
    try:
        for k in keys:
            r = await client.put(
                f"/admin/deployment-settings/{k}", json={"value": "v"}, headers=admin_headers
            )
            assert r.status_code == 200
        listed = (await client.get("/admin/deployment-settings", headers=admin_headers)).json()
        mine = [i["key"] for i in listed if i["key"].startswith(f"p1.test.{tok}.")]
        assert mine == [f"p1.test.{tok}.{x}" for x in ("a", "b", "c")]
    finally:
        async with sessionmaker_() as s:
            await s.execute(delete(DeploymentSetting).where(DeploymentSetting.key.in_(keys)))
            await s.commit()


async def test_deployment_settings_are_admin_only(client: AsyncClient, member: Member) -> None:
    r = await client.get("/admin/deployment-settings", headers=member.headers)
    assert r.status_code == 403


async def test_llm_model_crud_conflict_and_member_listing(
    client: AsyncClient,
    admin_headers: dict[str, str],
    member: Member,
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    provider = f"p1prov{secrets.token_hex(3)}"
    try:
        body = {"provider": provider, "modelId": "m-1", "label": "M1", "enabled": True}
        r = await client.post("/admin/llm-models", json=body, headers=admin_headers)
        assert r.status_code == 201, r.text
        created = r.json()
        assert created["modelId"] == "m-1" and created["verificationStatus"] is None
        assert ISO.match(created["createdAt"]) and ISO.match(created["updatedAt"])
        r = await client.post("/admin/llm-models", json=body, headers=admin_headers)
        assert r.status_code == 409
        assert r.json()["detail"].startswith("Model already exists for provider")
        # Backdate so a port that forgets to bump updated_at on update is caught.
        async with sessionmaker_() as s:
            await s.execute(
                text("UPDATE llm_models SET updated_at='2000-01-01' WHERE id=:i"),
                {"i": created["id"]},
            )
            await s.commit()
        r = await client.patch(
            f"/admin/llm-models/{created['id']}", json={"enabled": False}, headers=admin_headers
        )
        assert r.status_code == 200 and r.json()["enabled"] is False and r.json()["label"] == "M1"
        assert ISO.match(r.json()["updatedAt"]) and not r.json()["updatedAt"].startswith("2000")
        assert r.json()["createdAt"] == created["createdAt"]
        r = await client.get(f"/llm-models?provider={provider}", headers=member.headers)
        assert r.json() == []
        await client.patch(
            f"/admin/llm-models/{created['id']}", json={"enabled": True}, headers=admin_headers
        )
        r = await client.get(f"/llm-models?provider={provider}", headers=member.headers)
        assert [m["modelId"] for m in r.json()] == ["m-1"]
        r = await client.patch("/admin/llm-models/nope", json={}, headers=admin_headers)
        assert r.status_code == 404 and r.json() == {"detail": "Model 'nope' not found."}
        r = await client.delete(f"/admin/llm-models/{created['id']}", headers=admin_headers)
        assert r.status_code == 204
        r = await client.delete(f"/admin/llm-models/{created['id']}", headers=admin_headers)
        assert r.status_code == 404
    finally:
        async with sessionmaker_() as s:
            await s.execute(delete(LlmModel).where(LlmModel.provider == provider))
            await s.commit()


async def test_llm_model_listing_order(
    client: AsyncClient,
    admin_headers: dict[str, str],
    member: Member,
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    tok = secrets.token_hex(3)
    pa, pb = f"p1prov{tok}a", f"p1prov{tok}b"
    try:
        async with sessionmaker_() as s:  # inserted in reverse order
            for prov, mid in ((pb, "m-2"), (pb, "m-1"), (pa, "m-2"), (pa, "m-1")):
                s.add(LlmModel(provider=prov, model_id=mid, label=None, enabled=True))
            await s.commit()
        listed = (await client.get("/admin/llm-models", headers=admin_headers)).json()
        mine = [(m["provider"], m["modelId"]) for m in listed if m["provider"] in (pa, pb)]
        assert mine == [(pa, "m-1"), (pa, "m-2"), (pb, "m-1"), (pb, "m-2")]
        r = await client.get(f"/llm-models?provider={pb}", headers=member.headers)
        assert [m["modelId"] for m in r.json()] == ["m-1", "m-2"]
        enabled = (await client.get("/llm-models", headers=member.headers)).json()
        mine = [(m["provider"], m["modelId"]) for m in enabled if m["provider"] in (pa, pb)]
        assert mine == [(pa, "m-1"), (pa, "m-2"), (pb, "m-1"), (pb, "m-2")]
    finally:
        async with sessionmaker_() as s:
            await s.execute(delete(LlmModel).where(LlmModel.provider.in_([pa, pb])))
            await s.commit()


async def test_available_models_db_only_provider_serves_enabled_rows_in_order(
    client: AsyncClient, member: Member, sessionmaker_: async_sessionmaker[AsyncSession]
) -> None:
    provider = "typesafe"  # in _DB_ONLY_PROVIDERS
    tok = secrets.token_hex(4)
    ids = {x: f"p1-{tok}-{x}" for x in ("b", "x", "a")}  # inserted in reverse; x is disabled
    async with sessionmaker_() as s:
        for x in ("b", "x", "a"):
            s.add(LlmModel(provider=provider, model_id=ids[x], label=f"L{x}", enabled=x != "x"))
        await s.commit()
    try:
        r = await client.get(f"/llm-models/available?provider={provider}", headers=member.headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["provider"] == provider
        mine = [m for m in body["models"] if m["modelId"].startswith(f"p1-{tok}-")]
        assert mine == [
            {"modelId": ids["a"], "label": "La", "source": "db"},
            {"modelId": ids["b"], "label": "Lb", "source": "db"},
        ]
    finally:
        async with sessionmaker_() as s:
            await s.execute(delete(LlmModel).where(LlmModel.model_id.in_(list(ids.values()))))
            await s.commit()
