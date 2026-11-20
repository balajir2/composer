import re

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.config import get_settings
from src.db.models import LlmApiKey
from src.security.encryption import decrypt
from src.security.key_sync import PROVIDER_TO_SETTINGS_FIELD
from tests.integration.sqla_phase1.conftest import preserved_llm_keys

pytestmark = pytest.mark.integration
STR_DT = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(\.\d{6})?\+00:00$")  # str(datetime)


async def test_llm_key_upsert_get_list_delete(
    client: AsyncClient,
    admin_headers: dict[str, str],
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    provider = "typesafe"  # in _ALLOWED_PROVIDERS; any real row is restored afterwards
    field = PROVIDER_TO_SETTINGS_FIELD[provider]
    url = f"/admin/llm-keys/{provider}"
    async with preserved_llm_keys(sessionmaker_, [provider]):
        r = await client.get(url, headers=admin_headers)
        assert r.status_code == 404
        r = await client.put(url, json={"value": "sk-first-value"}, headers=admin_headers)
        assert r.status_code == 200 and r.json()["key_prefix"] == "sk-fir"
        assert STR_DT.match(r.json()["updated_at"]), r.json()["updated_at"]
        assert getattr(get_settings(), field) == "sk-first-value"
        # Backdate so a port that forgets to bump updated_at on update is caught.
        async with sessionmaker_() as s:
            await s.execute(
                text("UPDATE llm_api_keys SET updated_at='2000-01-01' WHERE provider=:p"),
                {"p": provider},
            )
            await s.commit()
        r = await client.put(url, json={"value": "sk-second"}, headers=admin_headers)
        assert r.json()["key_prefix"] == "sk-sec"
        assert STR_DT.match(r.json()["updated_at"])
        assert not r.json()["updated_at"].startswith("2000")
        got = (await client.get(url, headers=admin_headers)).json()
        assert got["provider"] == provider and got["key_prefix"] == "sk-sec"
        assert got["updated_at"] == r.json()["updated_at"]
        async with sessionmaker_() as s:
            row = await s.scalar(select(LlmApiKey).where(LlmApiKey.provider == provider))
        assert row is not None and decrypt(row.encrypted_key) == "sk-second"
        assert getattr(get_settings(), field) == "sk-second"
        r = await client.put(
            "/admin/llm-keys/not-a-provider", json={"value": "x"}, headers=admin_headers
        )
        assert r.status_code == 422
        assert (await client.delete(url, headers=admin_headers)).status_code == 204
        assert getattr(get_settings(), field) == ""
        assert (await client.get(url, headers=admin_headers)).status_code == 404
        assert (await client.delete(url, headers=admin_headers)).status_code == 404


async def test_llm_key_list_order(
    client: AsyncClient,
    admin_headers: dict[str, str],
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    providers = ["typesafe", "tavily"]  # written in reverse alphabetical order
    async with preserved_llm_keys(sessionmaker_, providers):
        for p in providers:
            r = await client.put(
                f"/admin/llm-keys/{p}", json={"value": "sk-zzz"}, headers=admin_headers
            )
            assert r.status_code == 200
        listed = (await client.get("/admin/llm-keys", headers=admin_headers)).json()
        mine = [k["provider"] for k in listed if k["provider"] in providers]
        assert mine == ["tavily", "typesafe"]
