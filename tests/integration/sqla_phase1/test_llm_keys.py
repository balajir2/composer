import re

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db.models import LlmApiKey

pytestmark = pytest.mark.integration
STR_DT = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(\.\d+)?\+00:00$")  # str(datetime)


async def test_llm_key_upsert_get_list_delete(
    client: AsyncClient,
    admin_headers: dict[str, str],
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    provider = "typesafe"  # in _ALLOWED_PROVIDERS; the original row (if any) is restored afterwards
    async with sessionmaker_() as s:
        original = await s.scalar(select(LlmApiKey).where(LlmApiKey.provider == provider))
        if original is not None:
            s.expunge(original)
    url = f"/admin/llm-keys/{provider}"
    try:
        r = await client.put(url, json={"value": "sk-first-value"}, headers=admin_headers)
        assert r.status_code == 200 and r.json()["key_prefix"] == "sk-fir"
        assert STR_DT.match(r.json()["updated_at"]), r.json()["updated_at"]
        r = await client.put(url, json={"value": "sk-second"}, headers=admin_headers)
        assert r.json()["key_prefix"] == "sk-sec"
        got = (await client.get(url, headers=admin_headers)).json()
        assert got["provider"] == provider and got["key_prefix"] == "sk-sec"
        listed = (await client.get("/admin/llm-keys", headers=admin_headers)).json()
        assert [k["provider"] for k in listed] == sorted(k["provider"] for k in listed)
        r = await client.put(
            "/admin/llm-keys/not-a-provider", json={"value": "x"}, headers=admin_headers
        )
        assert r.status_code == 422
        assert (await client.delete(url, headers=admin_headers)).status_code == 204
        assert (await client.get(url, headers=admin_headers)).status_code == 404
    finally:
        async with sessionmaker_() as s:
            await s.execute(delete(LlmApiKey).where(LlmApiKey.provider == provider))
            if original is not None:
                s.add(original)
            await s.commit()
