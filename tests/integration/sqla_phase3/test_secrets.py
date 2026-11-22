"""Group 6: per-node secrets (Jira, vector-DB, Confluence, HTTP sensitive headers).

Highest-risk behaviour of the phase: create/update encrypt at rest, every read path redacts,
an update echoing the redacted marker keeps the stored ciphertext byte for byte (matched by node
id against the previous row), and a marker echoed for a node id that is not in the previous row
stores nothing useful (None for field-based kinds; the literal marker for HTTP headers).
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, cast

import pytest
from httpx import AsyncClient

from src.security.encryption import decrypt_marked
from tests.integration.sqla_phase3.conftest import (
    HTTPX_MARK,
    REDACTED,
    Db,
    Member,
    WorkflowApi,
    chain,
    confluence,
    decrypts_to,
    http_node,
    jbody,
    jira,
    node_of,
    payload,
    unique,
    vector_db,
)

pytestmark = [pytest.mark.integration, HTTPX_MARK]


@dataclass(frozen=True)
class Kind:
    name: str
    node_type: str
    # builds the node for (node id, {field: plaintext}); fields not given keep their defaults
    make: Callable[[str, dict[str, str]], dict[str, Any]]
    secrets: dict[str, str]  # field -> default plaintext
    in_headers: bool = False
    stores_marker_for_unknown_node: bool = False
    extra: dict[str, str] = field(default_factory=lambda: dict[str, str]())

    def read(self, workflow_node: dict[str, Any], fld: str) -> Any:
        data = workflow_node["data"]
        return data["httpHeaders"].get(fld) if self.in_headers else data.get(fld)


def _jira(nid: str, v: dict[str, str]) -> dict[str, Any]:
    return jira(nid, v["apiToken"])


def _vector(nid: str, v: dict[str, str]) -> dict[str, Any]:
    return vector_db(nid, v["vectorDbApiKey"], v["vectorDbEmbeddingApiKey"])


def _confluence(nid: str, v: dict[str, str]) -> dict[str, Any]:
    return confluence(nid, v["apiToken"])


def _http(nid: str, v: dict[str, str]) -> dict[str, Any]:
    return http_node(nid, v["Authorization"])


KINDS = [
    Kind("jira", "jira", _jira, {"apiToken": "jira-secret-aaa"}),
    Kind(
        "vector-db",
        "vector-db",
        _vector,
        {"vectorDbApiKey": "vdb-secret-bbb", "vectorDbEmbeddingApiKey": "emb-secret-ccc"},
    ),
    Kind("confluence", "confluence", _confluence, {"apiToken": "conf-secret-ddd"}),
    Kind(
        "http",
        "http",
        _http,
        {"Authorization": "Bearer http-secret-eee"},
        in_headers=True,
        stores_marker_for_unknown_node=True,
    ),
]
KIND_IDS = [k.name for k in KINDS]
NID = "sec1"


def values(kind: Kind, suffix: str = "") -> dict[str, str]:
    return {k: v + suffix for k, v in kind.secrets.items()}


def graph(kind: Kind, vals: dict[str, str], nid: str = NID) -> tuple[list[Any], list[Any]]:
    return chain(kind.make(nid, vals))


def echo_nodes(kind: Kind, nid: str = NID) -> tuple[list[Any], list[Any]]:
    """The node as a UI would send it back: every secret field replaced by the marker."""
    return graph(kind, dict.fromkeys(kind.secrets, REDACTED), nid)


def stored(kind: Kind, row_nodes: Any, fld: str, nid: str = NID) -> Any:
    return kind.read(node_of(row_nodes, nid), fld)


def assert_redacted(kind: Kind, wf: dict[str, Any], label: str) -> None:
    n = node_of(wf["nodes"], NID)
    for fld in kind.secrets:
        assert kind.read(n, fld) == REDACTED, (label, fld)
    if kind.in_headers:
        assert n["data"]["httpHeaders"]["Content-Type"] == "application/json"


def assert_no_secret_text(text: str, kind: Kind, *plain: str, stored_values: list[str]) -> None:
    for s in [*plain, *kind.secrets.values(), *stored_values]:
        assert s not in text, f"secret material leaked: {s[:8]}..."


# --------------------------------------------------------------------------- at rest + redaction


@pytest.mark.parametrize("kind", KINDS, ids=KIND_IDS)
async def test_create_encrypts_at_rest_and_response_is_redacted(
    client: AsyncClient, member: Member, db: Db, kind: Kind
) -> None:
    nodes, edges = graph(kind, values(kind))
    resp = await client.post("/workflows", json=payload(nodes, edges), headers=member.headers)
    assert resp.status_code == 201, resp.text
    wf = jbody(resp)
    assert_redacted(kind, wf, "create")

    row = await db.must(wf["id"])
    cipher: list[str] = []
    for fld, plain in kind.secrets.items():
        value = stored(kind, row.nodes, fld)
        assert decrypts_to(value, plain), (fld, value)
        cipher.append(value)
    assert_no_secret_text(resp.text, kind, stored_values=cipher)
    # nothing else in the stored graph carries the plaintext either
    raw = str(row.nodes) + str(row.edges)
    for plain in kind.secrets.values():
        assert plain not in raw


@pytest.mark.parametrize("kind", KINDS, ids=KIND_IDS)
async def test_every_read_path_redacts_and_never_leaks(
    client: AsyncClient,
    member: Member,
    second_member: Member,
    admin: Member,
    wf_api: WorkflowApi,
    db: Db,
    kind: Kind,
) -> None:
    cat = unique("cat")
    nodes, edges = graph(kind, values(kind))
    created = await wf_api.create(member, nodes=nodes, edges=edges, category=cat)
    wid = created["id"]
    row = await db.must(wid)
    cipher = [stored(kind, row.nodes, f) for f in kind.secrets]
    # share it so a second user's read paths exercise the same code
    await db.assign(wid, second_member.id, member.id)

    views: list[tuple[str, str, dict[str, Any]]] = []

    def add(label: str, resp: Any, pick: Callable[[Any], dict[str, Any]]) -> None:
        assert resp.status_code == 200, (label, resp.text)
        views.append((label, resp.text, pick(resp.json())))

    ident: Callable[[Any], dict[str, Any]] = lambda b: cast("dict[str, Any]", b)  # noqa: E731
    first: Callable[[Any], dict[str, Any]] = lambda b: cast("dict[str, Any]", b["items"][0])  # noqa: E731

    add("get/owner", await client.get(f"/workflows/{wid}", headers=member.headers), ident)
    add("get/assignee", await client.get(f"/workflows/{wid}", headers=second_member.headers), ident)
    add("get/admin", await client.get(f"/workflows/{wid}", headers=admin.headers), ident)
    add(
        "list",
        await client.get("/workflows", params={"category": cat}, headers=member.headers),
        first,
    )
    add(
        "list/mine-assignee",
        await client.get(
            "/workflows", params={"category": cat, "mine": "true"}, headers=second_member.headers
        ),
        first,
    )
    add(
        "list/admin",
        await client.get("/workflows", params={"category": cat}, headers=admin.headers),
        first,
    )
    add(
        "search",
        await client.get(
            "/workflows/search", params={"q": created["name"]}, headers=member.headers
        ),
        first,
    )
    add(
        "search/admin",
        await client.get("/workflows/search", params={"q": created["name"]}, headers=admin.headers),
        first,
    )
    add(
        "admin-flags",
        await client.patch(
            f"/workflows/{wid}/admin-flags", json={"isPublic": True}, headers=admin.headers
        ),
        ident,
    )
    add(
        "owner-assign",
        await client.patch(
            f"/workflows/{wid}/owner", json={"userId": second_member.id}, headers=admin.headers
        ),
        ident,
    )
    echo_n, echo_e = echo_nodes(kind)
    add(
        "update",
        await wf_api.put(
            second_member, wid, payload(echo_n, echo_e, name=created["name"], category=cat)
        ),
        ident,
    )
    for label, text, wf in views:
        assert wf["id"] == wid, label
        assert_redacted(kind, wf, label)
        assert_no_secret_text(text, kind, stored_values=cipher)
    # ciphertext values were never rewritten by the redacted-echo update
    row = await db.must(wid)
    assert [stored(kind, row.nodes, f) for f in kind.secrets] == cipher


# --------------------------------------------------------------------------- preserve / re-encrypt


@pytest.mark.parametrize("kind", KINDS, ids=KIND_IDS)
async def test_update_echoing_marker_preserves_ciphertext_byte_for_byte(
    member: Member, second_member: Member, wf_api: WorkflowApi, db: Db, kind: Kind
) -> None:
    nodes, edges = graph(kind, values(kind))
    created = await wf_api.create(member, nodes=nodes, edges=edges)
    wid = created["id"]
    before = [stored(kind, (await db.must(wid)).nodes, f) for f in kind.secrets]
    bystander = await db.seed(member, nodes=nodes, edges=edges)
    before_bystander = db.snapshot(await db.must(bystander))

    for who in (member, second_member):
        if who is second_member:
            await db.assign(wid, second_member.id, member.id)
        echo_n, echo_e = echo_nodes(kind)
        r = await wf_api.put(who, wid, payload(echo_n, echo_e, name=created["name"]))
        assert r.status_code == 200, r.text
        assert_redacted(kind, jbody(r), "update")
        after = [stored(kind, (await db.must(wid)).nodes, f) for f in kind.secrets]
        assert after == before  # not re-encrypted, not replaced by the marker
        for fld, plain in kind.secrets.items():
            assert decrypts_to(stored(kind, (await db.must(wid)).nodes, fld), plain)
    assert db.snapshot(await db.must(bystander)) == before_bystander


@pytest.mark.parametrize("kind", KINDS, ids=KIND_IDS)
async def test_update_with_changed_value_re_encrypts(
    member: Member, wf_api: WorkflowApi, db: Db, kind: Kind
) -> None:
    nodes, edges = graph(kind, values(kind))
    created = await wf_api.create(member, nodes=nodes, edges=edges)
    wid = created["id"]
    before = {f: stored(kind, (await db.must(wid)).nodes, f) for f in kind.secrets}

    new_vals = values(kind, "-changed")
    n2, e2 = graph(kind, new_vals)
    r = await wf_api.put(member, wid, payload(n2, e2, name=created["name"]))
    assert r.status_code == 200, r.text
    assert_redacted(kind, jbody(r), "update")
    row = await db.must(wid)
    for fld, plain in new_vals.items():
        value = stored(kind, row.nodes, fld)
        assert decrypts_to(value, plain) and value != before[fld]
        assert plain not in r.text


async def test_vector_db_fields_are_handled_independently(
    member: Member, wf_api: WorkflowApi, db: Db
) -> None:
    kind = KINDS[1]
    nodes, edges = graph(kind, values(kind))
    created = await wf_api.create(member, nodes=nodes, edges=edges)
    wid = created["id"]
    before = {f: stored(kind, (await db.must(wid)).nodes, f) for f in kind.secrets}

    # change only the API key, echo the embedding key
    n2, e2 = graph(kind, {"vectorDbApiKey": "brand-new-key", "vectorDbEmbeddingApiKey": REDACTED})
    r = await wf_api.put(member, wid, payload(n2, e2, name=created["name"]))
    assert r.status_code == 200
    row = await db.must(wid)
    assert decrypts_to(stored(kind, row.nodes, "vectorDbApiKey"), "brand-new-key")
    assert stored(kind, row.nodes, "vectorDbEmbeddingApiKey") == before["vectorDbEmbeddingApiKey"]


async def test_http_headers_only_sensitive_names_are_encrypted_case_insensitively(
    member: Member, wf_api: WorkflowApi, db: Db
) -> None:
    headers = {
        "x-api-key": "key-plain-111",
        "COOKIE": "session=abc",
        "X-Custom": "visible-value",
        "Content-Type": "application/json",
        "Authorization": "",
    }
    n = http_node("h")
    n["data"]["httpHeaders"] = headers
    nodes, edges = chain(n)
    created = await wf_api.create(member, nodes=nodes, edges=edges)
    stored_h = (await db.stored_node(created["id"], "h"))["data"]["httpHeaders"]
    assert decrypts_to(stored_h["x-api-key"], "key-plain-111")
    assert decrypts_to(stored_h["COOKIE"], "session=abc")
    assert (
        stored_h["X-Custom"] == "visible-value" and stored_h["Content-Type"] == "application/json"
    )
    assert stored_h["Authorization"] == ""  # empty values are left alone
    shown = node_of(created["nodes"], "h")["data"]["httpHeaders"]
    assert shown == {
        "x-api-key": REDACTED,
        "COOKIE": REDACTED,
        "X-Custom": "visible-value",
        "Content-Type": "application/json",
        "Authorization": "",
    }


@pytest.mark.parametrize("kind", KINDS, ids=KIND_IDS)
async def test_already_encrypted_value_is_stored_as_is(
    member: Member, wf_api: WorkflowApi, db: Db, kind: Kind
) -> None:
    nodes, edges = graph(kind, values(kind))
    created = await wf_api.create(member, nodes=nodes, edges=edges)
    wid = created["id"]
    before = {f: stored(kind, (await db.must(wid)).nodes, f) for f in kind.secrets}
    n2, e2 = graph(kind, before)  # a client that sends the ciphertext back verbatim
    r = await wf_api.put(member, wid, payload(n2, e2, name=created["name"]))
    assert r.status_code == 200
    after = {f: stored(kind, (await db.must(wid)).nodes, f) for f in kind.secrets}
    assert after == before


@pytest.mark.parametrize("kind", KINDS, ids=KIND_IDS)
async def test_empty_secret_is_not_redacted_or_encrypted(
    member: Member, wf_api: WorkflowApi, db: Db, kind: Kind
) -> None:
    blank = dict.fromkeys(kind.secrets, "")
    nodes, edges = graph(kind, blank)
    created = await wf_api.create(member, nodes=nodes, edges=edges)
    row = await db.must(created["id"])
    shown = node_of(created["nodes"], NID)
    for fld in kind.secrets:
        assert stored(kind, row.nodes, fld) == ""
        assert kind.read(shown, fld) == ""


# --------------------------------------------------------------------------- the pinned quirk


@pytest.mark.parametrize("kind", KINDS, ids=KIND_IDS)
async def test_marker_echoed_for_node_id_not_in_previous_row_today(
    member: Member, wf_api: WorkflowApi, db: Db, kind: Kind
) -> None:
    """PINNED QUIRK: a redacted echo for a node id that the previous row does not contain
    cannot be resolved. Field-based kinds (Jira, vector-DB, Confluence) store None; the HTTP
    header path stores the literal marker string (its lookup defaults to the incoming value)."""
    nodes, edges = graph(kind, values(kind))
    created = await wf_api.create(member, nodes=nodes, edges=edges)
    wid = created["id"]

    echo_n, echo_e = echo_nodes(kind, nid="brand-new-node")
    r = await wf_api.put(member, wid, payload(echo_n, echo_e, name=created["name"]))
    assert r.status_code == 200, r.text
    row = await db.must(wid)
    for fld in kind.secrets:
        value = stored(kind, row.nodes, fld, nid="brand-new-node")
        if kind.stores_marker_for_unknown_node:
            assert value == REDACTED
        else:
            assert value is None
    shown = node_of(jbody(r)["nodes"], "brand-new-node")
    for fld in kind.secrets:
        got = kind.read(shown, fld)
        assert got == (REDACTED if kind.stores_marker_for_unknown_node else None)
    # and the original node's ciphertext is gone with that node (it was removed by the PUT)
    assert all(n["id"] != NID for n in row.nodes)


@pytest.mark.parametrize("kind", KINDS[:3], ids=KIND_IDS[:3])
async def test_marker_echo_for_known_node_takes_whatever_the_previous_row_held(
    member: Member, wf_api: WorkflowApi, db: Db, kind: Kind
) -> None:
    """Same node id, previous row held an empty string for the field: the empty string is what
    is carried over (the lookup returns the previous value as is, falsy or not)."""
    blank = dict.fromkeys(kind.secrets, "")
    nodes, edges = graph(kind, blank)
    created = await wf_api.create(member, nodes=nodes, edges=edges)
    echo_n, echo_e = echo_nodes(kind)
    r = await wf_api.put(member, created["id"], payload(echo_n, echo_e, name=created["name"]))
    assert r.status_code == 200
    row = await db.must(created["id"])
    for fld in kind.secrets:
        assert stored(kind, row.nodes, fld) == ""


# --------------------------------------------------------------------------- logs


async def test_no_secret_reaches_the_logs(
    client: AsyncClient,
    member: Member,
    wf_api: WorkflowApi,
    db: Db,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    plain: list[str] = []
    all_nodes: list[dict[str, Any]] = []
    for i, kind in enumerate(KINDS):
        vals = values(kind, f"-log{i}")
        plain.extend(vals.values())
        all_nodes.append(kind.make(f"n{i}", vals))
    nodes, edges = chain(*all_nodes)
    created = await wf_api.create(member, nodes=nodes, edges=edges)
    wid = created["id"]
    cipher: list[str] = []
    for i, kind in enumerate(KINDS):
        for fld in kind.secrets:
            cipher.append(stored(kind, (await db.must(wid)).nodes, fld, nid=f"n{i}"))
    await client.get(f"/workflows/{wid}", headers=member.headers)
    await client.get("/workflows", params={"mine": "true"}, headers=member.headers)
    await client.get("/workflows/search", params={"q": created["name"]}, headers=member.headers)
    echo = [k.make(f"n{i}", dict.fromkeys(k.secrets, REDACTED)) for i, k in enumerate(KINDS)]
    n2, e2 = chain(*echo)
    await wf_api.put(member, wid, payload(n2, e2, name=created["name"]))

    text = "\n".join(
        r.getMessage() + (f"\n{r.exc_text}" if r.exc_text else "") for r in caplog.records
    )
    for secret in [*plain, *cipher, *(c.removeprefix("enc:v1:") for c in cipher)]:
        assert secret and secret not in text, f"secret leaked into logs: {secret[:8]}..."
    # decrypting what we stored still works (the key was never rotated by the test)
    assert decrypt_marked(cipher[0]).startswith("jira-secret")
