"""Header values and tokens never leak through error text, and unsafe ones are refused at create."""

import logging

import httpx
import pytest
from httpx import AsyncClient
from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]

from src.security.encryption import decrypt, encrypt
from tests.integration.sqla_phase2.conftest import HTTPX_MARK, Db, Member, ServerApi, unique

pytestmark = [pytest.mark.integration, HTTPX_MARK]

SECRET = "SECRET-TOKEN-VALUE"
URL = "https://bad-header.example.test/mcp"


async def test_stored_token_with_a_newline_leaks_into_no_error_text_or_log(
    client: AsyncClient,
    member: Member,
    servers: ServerApi,
    db: Db,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
    caplog: pytest.LogCaptureFixture,
) -> None:
    created = await servers.create(member, url=URL, authType="bearer", accessToken="fine")
    sid = created["id"]
    # saved before create validated tokens: ends in a newline
    await db.set_server(sid, encrypted_access_token=encrypt(f"{SECRET}\nX"))
    # what h11 raises at send time, with the value in its message
    httpx_mock.add_exception(
        httpx.LocalProtocolError(f"Illegal header value b'Bearer {SECRET}\nX'"), url=URL
    )
    with caplog.at_level(logging.DEBUG):
        r = await client.post(f"/mcp-servers/{sid}/test-connection", headers=member.headers)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False and SECRET not in r.text
    row = await db.server(sid)
    assert row is not None and row.last_error and SECRET not in row.last_error
    listed = await client.get("/mcp-servers", headers=member.headers)
    assert SECRET not in listed.text
    assert SECRET not in caplog.text


async def test_create_rejects_unsafe_headers_and_tokens_without_echoing_values(
    client: AsyncClient, member: Member
) -> None:
    base = {"name": unique("hdr"), "url": URL, "authType": "none"}
    bad_headers = [
        {"X-Api-Key": f"{SECRET}\ninjected"},
        {"Bad Name": "v"},
        {"Host": "evil.example.com"},
        {"Content-Length": "5"},
        {"X-Api-Key": f"{SECRET}☃"},  # not latin-1: fails at send time otherwise
    ]
    for headers in bad_headers:
        r = await client.post(
            "/mcp-servers", json={**base, "headers": headers}, headers=member.headers
        )
        assert r.status_code == 422, headers
        assert SECRET not in r.text
        assert next(iter(headers)).strip() in r.text  # the detail names the header
    r = await client.post(
        "/mcp-servers",
        json={**base, "authType": "bearer", "accessToken": f"{SECRET}\nrest"},
        headers=member.headers,
    )
    assert r.status_code == 422 and SECRET not in r.text
    r = await client.post(
        "/mcp-servers",
        json={**base, "authType": "bearer", "accessToken": f"{SECRET}☃"},
        headers=member.headers,
    )
    assert r.status_code == 422 and SECRET not in r.text and "☃" not in r.text


async def test_create_strips_surrounding_whitespace_from_token_and_header_values(
    client: AsyncClient, member: Member, db: Db
) -> None:
    r = await client.post(
        "/mcp-servers",
        json={
            "name": unique("strip"),
            "url": URL,
            "authType": "bearer",
            "accessToken": "tok-123\n",
            "headers": {" X-Tenant ": "  t1\n"},
        },
        headers=member.headers,
    )
    assert r.status_code == 201, r.text
    row = await db.server(r.json()["id"])
    assert row is not None and row.encrypted_access_token is not None
    assert decrypt(row.encrypted_access_token) == "tok-123"
    assert row.headers == {"X-Tenant": "t1"}
