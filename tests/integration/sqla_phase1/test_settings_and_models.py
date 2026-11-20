import re
import secrets

import pytest
from httpx import AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db.models import DeploymentSetting, LlmModel
from tests.integration.sqla_phase1.conftest import Member

pytestmark = pytest.mark.integration
ISO = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?(Z|\+00:00)$")


async def test_deployment_setting_create_update_list_get(
    client: AsyncClient,
    admin_headers: dict[str, str],
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    key = f"p1.test.{secrets.token_hex(4)}"
    try:
        r = await client.get(f"/admin/deployment-settings/{key}", headers=admin_headers)
        assert r.status_code == 404
        r = await client.put(
            f"/admin/deployment-settings/{key}", json={"value": "a"}, headers=admin_headers
        )
        assert r.status_code == 200 and r.json() == {"key": key, "value": "a"}
        r = await client.put(
            f"/admin/deployment-settings/{key}", json={"value": "b"}, headers=admin_headers
        )
        assert r.json() == {"key": key, "value": "b"}
        listed = (await client.get("/admin/deployment-settings", headers=admin_headers)).json()
        keys = [i["key"] for i in listed]
        assert {"key": key, "value": "b"} in listed and keys == sorted(keys)
    finally:
        async with sessionmaker_() as s:
            await s.execute(delete(DeploymentSetting).where(DeploymentSetting.key == key))
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
        r = await client.patch(
            f"/admin/llm-models/{created['id']}", json={"enabled": False}, headers=admin_headers
        )
        assert r.status_code == 200 and r.json()["enabled"] is False and r.json()["label"] == "M1"
        r = await client.get(f"/llm-models?provider={provider}", headers=member.headers)
        assert r.json() == []
        await client.patch(
            f"/admin/llm-models/{created['id']}", json={"enabled": True}, headers=admin_headers
        )
        r = await client.get(f"/llm-models?provider={provider}", headers=member.headers)
        assert [m["modelId"] for m in r.json()] == ["m-1"]
        r = await client.patch("/admin/llm-models/nope", json={}, headers=admin_headers)
        assert r.status_code == 404
        r = await client.delete(f"/admin/llm-models/{created['id']}", headers=admin_headers)
        assert r.status_code == 204
        r = await client.delete(f"/admin/llm-models/{created['id']}", headers=admin_headers)
        assert r.status_code == 404
    finally:
        async with sessionmaker_() as s:
            await s.execute(delete(LlmModel).where(LlmModel.provider == provider))
            await s.commit()


async def test_available_models_db_only_provider_serves_enabled_rows(
    client: AsyncClient, member: Member, sessionmaker_: async_sessionmaker[AsyncSession]
) -> None:
    provider = "typesafe"  # in _DB_ONLY_PROVIDERS
    model_id = f"p1-model-{secrets.token_hex(4)}"
    async with sessionmaker_() as s:
        s.add(LlmModel(provider=provider, model_id=model_id, label="P1", enabled=True))
        await s.commit()
    try:
        r = await client.get(f"/llm-models/available?provider={provider}", headers=member.headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["provider"] == provider
        hit = [m for m in body["models"] if m["modelId"] == model_id]
        assert hit == [{"modelId": model_id, "label": "P1", "source": "db"}]
    finally:
        async with sessionmaker_() as s:
            await s.execute(delete(LlmModel).where(LlmModel.model_id == model_id))
            await s.commit()
