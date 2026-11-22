"""Groups 3-4: list (visibility matrix, filters, pagination, ordering) and search."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient

from tests.integration.sqla_phase3.conftest import (
    HTTPX_MARK,
    Db,
    Member,
    jbody,
    unique,
)

pytestmark = [pytest.mark.integration, HTTPX_MARK]

BASE = datetime(2021, 6, 1, tzinfo=UTC)


def at(minutes: int) -> datetime:
    return BASE + timedelta(minutes=minutes)


def ids(body: dict[str, Any]) -> list[str]:
    return [i["id"] for i in body["items"]]


async def list_(client: AsyncClient, who: Member, **params: Any) -> tuple[int, dict[str, Any]]:
    resp = await client.get("/workflows", params=params, headers=who.headers)
    return resp.status_code, jbody(resp)


# --------------------------------------------------------------------------- 3. list


async def _matrix(db: Db, a: Member, b: Member, cat: str) -> dict[str, str]:
    """a/b private+public rows, plus a private row of b assigned to a. A unique category makes
    the totals exact (no dependence on rows other tests or users created)."""
    rows = {
        "a_priv": await db.seed(a, category=cat, updated_at=at(1)),
        "a_pub": await db.seed(a, category=cat, is_public=True, updated_at=at(2)),
        "b_priv": await db.seed(b, category=cat, updated_at=at(3)),
        "b_pub": await db.seed(b, category=cat, is_public=True, updated_at=at(4)),
        "b_assigned": await db.seed(b, category=cat, updated_at=at(5)),
    }
    await db.assign(rows["b_assigned"], a.id, b.id)
    return rows


async def test_list_visibility_matrix(
    client: AsyncClient, db: Db, member: Member, second_member: Member, admin: Member
) -> None:
    cat = unique("cat")
    r = await _matrix(db, member, second_member, cat)

    # non-admin: public + own + assigned
    code, body = await list_(client, member, category=cat)
    assert code == 200 and body["total"] == 4
    assert set(ids(body)) == {r["a_priv"], r["a_pub"], r["b_pub"], r["b_assigned"]}

    code, body = await list_(client, second_member, category=cat)
    assert body["total"] == 4
    assert set(ids(body)) == {r["b_priv"], r["b_pub"], r["b_assigned"], r["a_pub"]}

    # mine=true: own + assigned only (public rows of others excluded)
    code, body = await list_(client, member, category=cat, mine="true")
    assert body["total"] == 3
    assert set(ids(body)) == {r["a_priv"], r["a_pub"], r["b_assigned"]}

    code, body = await list_(client, second_member, category=cat, mine="true")
    assert body["total"] == 3
    assert set(ids(body)) == {r["b_priv"], r["b_pub"], r["b_assigned"]}

    # admin without mine: everything
    code, body = await list_(client, admin, category=cat)
    assert body["total"] == 5 and set(ids(body)) == set(r.values())

    # admin with mine: strictly their own (they own and are assigned none)
    code, body = await list_(client, admin, category=cat, mine="true")
    assert body["total"] == 0 and body["items"] == []

    # mine=false behaves like absent
    code, body = await list_(client, member, category=cat, mine="false")
    assert body["total"] == 4


async def test_list_mine_for_admin_includes_assigned(
    client: AsyncClient, db: Db, member: Member, admin: Member
) -> None:
    cat = unique("cat")
    own = await db.seed(admin, category=cat, updated_at=at(1))
    other = await db.seed(member, category=cat, updated_at=at(2))
    other_assigned = await db.seed(member, category=cat, updated_at=at(3))
    await db.assign(other_assigned, admin.id, member.id)
    _code, body = await list_(client, admin, category=cat, mine="true")
    assert body["total"] == 2 and set(ids(body)) == {own, other_assigned}
    assert other not in ids(body)


async def test_list_owned_public_and_assigned_row_appears_once(
    client: AsyncClient, db: Db, member: Member
) -> None:
    cat = unique("cat")
    wid = await db.seed(member, category=cat, is_public=True)
    await db.assign(wid, member.id, member.id)
    for params in ({}, {"mine": "true"}):
        _code, body = await list_(client, member, category=cat, **params)
        assert body["total"] == 1 and ids(body) == [wid]


async def test_list_optional_filters(
    client: AsyncClient, db: Db, member: Member, second_member: Member, admin: Member
) -> None:
    cat = unique("cat")
    other_cat = unique("cat")
    tpl_pub = await db.seed(member, category=cat, is_template=True, is_public=True)
    tpl_priv = await db.seed(member, category=cat, is_template=True)
    plain_pub = await db.seed(member, category=cat, is_public=True)
    plain_priv = await db.seed(member, category=cat)
    elsewhere = await db.seed(member, category=other_cat, is_template=True, is_public=True)

    async def q(**params: Any) -> set[str]:
        code, body = await list_(client, member, **params)
        assert code == 200
        assert body["total"] == len(body["items"])
        return set(ids(body))

    assert await q(category=cat) == {tpl_pub, tpl_priv, plain_pub, plain_priv}
    assert await q(category=cat, isTemplate="true") == {tpl_pub, tpl_priv}
    assert await q(category=cat, isTemplate="false") == {plain_pub, plain_priv}
    assert await q(category=cat, isPublic="true") == {tpl_pub, plain_pub}
    assert await q(category=cat, isPublic="false") == {tpl_priv, plain_priv}
    assert await q(category=cat, isTemplate="true", isPublic="true") == {tpl_pub}
    assert await q(category=cat, isTemplate="false", isPublic="false") == {plain_priv}
    assert await q(category=other_cat) == {elsewhere}
    # category is an exact match, not a prefix or case-folded match
    assert await q(category=cat[:-1]) == set()
    assert await q(category=cat.upper()) == set()

    # filters AND with the visibility predicate: another user's private rows stay hidden
    _code, body = await list_(client, second_member, category=cat, isPublic="false")
    assert body["total"] == 0
    # ...and filters apply to the admin feed too
    _code, body = await list_(client, admin, category=cat, isPublic="false")
    assert set(ids(body)) == {tpl_priv, plain_priv} and body["total"] == 2


async def test_list_orders_by_updated_at_desc_and_paginates(
    client: AsyncClient, db: Db, member: Member
) -> None:
    cat = unique("cat")
    # inserted oldest-first and newest-last so neither insertion order nor id order is the answer
    seeded: dict[int, str] = {}
    for minute in (30, 10, 50, 20, 40):
        seeded[minute] = await db.seed(member, category=cat, updated_at=at(minute))
    expected = [seeded[m] for m in (50, 40, 30, 20, 10)]

    _code, body = await list_(client, member, category=cat)
    assert ids(body) == expected
    assert (body["total"], body["limit"], body["offset"]) == (5, 50, 0)

    _code, page1 = await list_(client, member, category=cat, limit=2)
    _code, page2 = await list_(client, member, category=cat, limit=2, offset=2)
    _code, page3 = await list_(client, member, category=cat, limit=2, offset=4)
    _code, beyond = await list_(client, member, category=cat, limit=2, offset=10)
    assert ids(page1) == expected[:2] and (page1["limit"], page1["offset"]) == (2, 0)
    assert ids(page2) == expected[2:4] and (page2["limit"], page2["offset"]) == (2, 2)
    assert ids(page3) == expected[4:] and page3["offset"] == 4
    assert beyond["items"] == [] and beyond["total"] == 5 and beyond["offset"] == 10
    for page in (page1, page2, page3):
        assert page["total"] == 5


async def test_list_orders_by_current_updated_at_after_an_update(
    client: AsyncClient, db: Db, member: Member
) -> None:
    cat = unique("cat")
    old = await db.seed(member, category=cat, updated_at=at(1))
    mid = await db.seed(member, category=cat, updated_at=at(2))
    new = await db.seed(member, category=cat, updated_at=at(3))
    _, body = await list_(client, member, category=cat)
    assert ids(body) == [new, mid, old]
    # an admin-flags write bumps updated_at to now, which moves the oldest row to the top
    admin_resp = await client.get(f"/workflows/{old}", headers=member.headers)
    assert admin_resp.status_code == 200
    r = await client.put(
        f"/workflows/{old}",
        json={**{k: admin_resp.json()[k] for k in ("name", "nodes", "edges")}, "category": cat},
        headers=member.headers,
    )
    assert r.status_code == 200
    _, body = await list_(client, member, category=cat)
    assert ids(body) == [old, new, mid]


@pytest.mark.parametrize(
    "params",
    [{"limit": 0}, {"limit": 101}, {"limit": -1}, {"offset": -1}, {"limit": "x"}],
)
async def test_list_param_bounds_are_422(
    client: AsyncClient, member: Member, params: dict[str, Any]
) -> None:
    resp = await client.get("/workflows", params=params, headers=member.headers)
    assert resp.status_code == 422


async def test_list_limit_bounds_accepted(client: AsyncClient, member: Member, db: Db) -> None:
    cat = unique("cat")
    await db.seed(member, category=cat)
    for limit in (1, 100):
        code, body = await list_(client, member, category=cat, limit=limit)
        assert code == 200 and body["limit"] == limit and body["total"] == 1


# --------------------------------------------------------------------------- 4. search


async def _search(
    client: AsyncClient, who: Member, q: str, **params: Any
) -> tuple[int, dict[str, Any]]:
    resp = await client.get("/workflows/search", params={"q": q, **params}, headers=who.headers)
    return resp.status_code, jbody(resp)


async def test_search_visibility_matrix(
    client: AsyncClient, db: Db, member: Member, second_member: Member, admin: Member
) -> None:
    tok = unique("zqv")
    a_priv = await db.seed(member, name=f"{tok} a-priv", updated_at=at(1))
    a_pub = await db.seed(member, name=f"{tok} a-pub", is_public=True, updated_at=at(2))
    b_priv = await db.seed(second_member, name=f"{tok} b-priv", updated_at=at(3))
    b_pub = await db.seed(second_member, name=f"{tok} b-pub", is_public=True, updated_at=at(4))
    b_assigned = await db.seed(second_member, name=f"{tok} b-asg", updated_at=at(5))
    await db.assign(b_assigned, member.id, second_member.id)

    code, body = await _search(client, member, tok)
    assert code == 200 and body["total"] == 4
    assert set(ids(body)) == {a_priv, a_pub, b_pub, b_assigned}
    assert ids(body) == [b_assigned, b_pub, a_pub, a_priv]  # updated_at desc

    code, body = await _search(client, second_member, tok)
    assert set(ids(body)) == {b_priv, b_pub, b_assigned, a_pub} and body["total"] == 4

    code, body = await _search(client, admin, tok)
    assert body["total"] == 5 and set(ids(body)) == {a_priv, a_pub, b_priv, b_pub, b_assigned}


async def test_search_matches_name_and_description_case_insensitively_only(
    client: AsyncClient, db: Db, member: Member
) -> None:
    tok = unique("zqm")
    by_name = await db.seed(member, name=f"Prefix {tok.upper()} suffix")
    by_desc = await db.seed(member, description=f"talks about {tok.upper()}")
    by_tag = await db.seed(member, tags=[tok])
    by_category = await db.seed(member, category=tok)
    await db.seed(member, name="unrelated", description="nothing")

    code, body = await _search(client, member, tok)
    assert code == 200
    assert set(ids(body)) == {by_name, by_desc} and body["total"] == 2
    assert by_tag not in ids(body) and by_category not in ids(body)

    # lower/mixed case query finds the same rows, and substrings match anywhere
    code, body = await _search(client, member, tok.upper())
    assert set(ids(body)) == {by_name, by_desc}
    code, body = await _search(client, member, tok[2:-1])
    assert {by_name, by_desc} <= set(ids(body))


async def test_search_limit_total_and_offset_in_response(
    client: AsyncClient, db: Db, member: Member
) -> None:
    tok = unique("zql")
    seeded: dict[int, str] = {}
    for minute in (20, 40, 10, 30):
        seeded[minute] = await db.seed(member, name=f"{tok} {minute}", updated_at=at(minute))
    expected = [seeded[m] for m in (40, 30, 20, 10)]

    code, body = await _search(client, member, tok)
    assert ids(body) == expected and (body["total"], body["limit"], body["offset"]) == (4, 50, 0)

    code, body = await _search(client, member, tok, limit=2)
    assert ids(body) == expected[:2]
    assert (body["total"], body["limit"], body["offset"]) == (4, 2, 0)  # total counts all matches

    # search has no offset parameter: it is ignored and the response always says 0
    code, body = await _search(client, member, tok, limit=2, offset=2)
    assert code == 200 and ids(body) == expected[:2] and body["offset"] == 0


@pytest.mark.parametrize(
    "params",
    [{"q": "x", "limit": 0}, {"q": "x", "limit": 101}, {"q": ""}, {}],
)
async def test_search_param_bounds_are_422(
    client: AsyncClient, member: Member, params: dict[str, Any]
) -> None:
    resp = await client.get("/workflows/search", params=params, headers=member.headers)
    assert resp.status_code == 422


async def test_search_limit_bounds_accepted(client: AsyncClient, member: Member, db: Db) -> None:
    tok = unique("zqb")
    await db.seed(member, name=tok)
    for limit in (1, 100):
        code, body = await _search(client, member, tok, limit=limit)
        assert code == 200 and body["limit"] == limit and body["total"] == 1


async def test_search_percent_and_underscore_today(
    client: AsyncClient, db: Db, member: Member
) -> None:
    """PINNED: `%` and `_` in the query. Prisma's `contains` does NOT escape them for this
    search, so they act as LIKE wildcards: `%` matches any run of characters and `_` any single
    character. (Phase 1 saw the same for user search.) The port must match this."""
    tok = unique("zqw")
    ab = await db.seed(member, name=f"{tok}ab")
    cd = await db.seed(member, name=f"{tok}cd")
    pct = await db.seed(member, name=f"{tok}%lit")
    us = await db.seed(member, name=f"{tok}_z")
    xz = await db.seed(member, name=f"{tok}xz")

    # `%` as a wildcard: tok% matches every row (substring semantics), not just the literal one
    _, body = await _search(client, member, f"{tok}%")
    pct_found = set(ids(body))
    # `_` as a single-character wildcard: tok_z matches both the underscore and the x rows
    _, body = await _search(client, member, f"{tok}_z")
    us_found = set(ids(body))
    # a lone `%` matches every visible row (including the ones just seeded)
    _, body = await _search(client, member, "%")
    all_found = set(ids(body))

    assert pct_found == {ab, cd, pct, us, xz}, "% is a wildcard today"
    assert us_found == {us, xz}, "_ is a single-character wildcard today"
    assert {ab, cd, pct, us, xz} <= all_found, "a bare % matches everything"
