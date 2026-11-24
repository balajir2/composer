"""Group 13: builder diagnosis of an execution (``build_diagnosis``).

Pins the FIXED behaviour shipped in commit 86f6bbe (security hotfix PR #37): only an execution of
the workflow being built is diagnosed, and only the caller's own run of it; admins may diagnose
any user's run of that workflow, but never another workflow's. Everything else is
indistinguishable from "no such execution" (None). There is deliberately no test that another
user's execution is diagnosed.
"""

from typing import Any

import pytest

from tests.integration.sqla_phase4a.conftest import HTTPX_MARK, Db, Member, Phase4aAdapter

pytestmark = [pytest.mark.integration, HTTPX_MARK]

REDACTED = "•" * 8


async def test_diagnosis_exact_shape_status_and_error(
    adapter: Phase4aAdapter, member: Member, db: Db
) -> None:
    wid = await db.seed_workflow(member)
    eid = await db.seed_execution(
        wid,
        member,
        status="failed",
        error="HttpNodeError: it broke",
        node_results={"n1": {"status": "failed", "output": "o"}},
        variables={"alpha": 1, "beta": [1, 2]},
    )
    before = db.snapshot(await db.must(eid))
    result = await adapter.diagnose(eid, workflow_id=wid, user_id=member.id, role="member")
    assert result == {
        "status": "failed",
        "error": "HttpNodeError: it broke",
        "node_results": {"n1": {"status": "failed", "output": "o"}},
        "variables_tail": {"alpha": 1, "beta": [1, 2]},
    }
    assert db.snapshot(await db.must(eid)) == before  # read-only

    ok = await db.seed_execution(wid, member, status="completed")
    result = await adapter.diagnose(ok, workflow_id=wid, user_id=member.id, role="member")
    assert result == {
        "status": "completed",
        "error": None,
        "node_results": {},
        "variables_tail": {},
    }


async def test_diagnosis_redacts_secret_hint_keys_and_truncates_long_strings(
    adapter: Phase4aAdapter, member: Member, db: Db
) -> None:
    wid = await db.seed_workflow(member)
    long_text = "x" * 600
    eid = await db.seed_execution(
        wid,
        member,
        status="failed",
        node_results={
            "api_key": {"anything": "visible-but-redacted-as-a-whole"},
            "OpenAI_Token": "t",
            "plain": long_text,
            "nested": {"apiKey": "stays-visible-today", "note": long_text},
        },
        variables={
            "password": "p",
            "Authorization": "Bearer abc",
            "client_secret": "s",
            "credentialBlob": "c",
            "monkey": "banana",  # substring match: contains "key"
            "exact500": "y" * 500,
            "over500": "z" * 501,
            "longlist": ["w" * 600],
            "short": "ok",
        },
    )
    result = await adapter.diagnose(eid, workflow_id=wid, user_id=member.id, role="member")
    assert result is not None
    nr = result["node_results"]
    assert nr["api_key"] == REDACTED and nr["OpenAI_Token"] == REDACTED
    assert nr["plain"] == "x" * 500 + "…"
    # redaction and truncation apply to top-level keys only
    assert nr["nested"] == {"apiKey": "stays-visible-today", "note": long_text}
    v: dict[str, Any] = result["variables_tail"]
    for key in ("password", "Authorization", "client_secret", "credentialBlob", "monkey"):
        assert v[key] == REDACTED, key
    assert v["exact500"] == "y" * 500
    assert v["over500"] == "z" * 500 + "…"
    assert v["longlist"] == ["w" * 600] and v["short"] == "ok"


async def test_diagnosis_keeps_the_last_30_variables_in_stored_order(
    adapter: Phase4aAdapter, member: Member, db: Db
) -> None:
    wid = await db.seed_workflow(member)
    variables = {f"v{i:02d}": i for i in range(40)}
    eid = await db.seed_execution(wid, member, status="failed", variables=variables)
    result = await adapter.diagnose(eid, workflow_id=wid, user_id=member.id, role="member")
    assert result is not None
    tail = result["variables_tail"]
    assert list(tail) == [f"v{i:02d}" for i in range(10, 40)]
    assert len(tail) == 30 and tail["v39"] == 39 and "v09" not in tail

    exactly = await db.seed_execution(
        wid, member, status="failed", variables={f"v{i:02d}": i for i in range(30)}
    )
    result = await adapter.diagnose(exactly, workflow_id=wid, user_id=member.id, role="member")
    assert result is not None and len(result["variables_tail"]) == 30


async def test_diagnosis_of_malformed_stored_json_degrades_to_empty(
    adapter: Phase4aAdapter, member: Member, db: Db
) -> None:
    wid = await db.seed_workflow(member)
    eid = await db.seed_execution(
        wid, member, status="failed", node_results=["not", "a", "dict"], variables={}
    )
    result = await adapter.diagnose(eid, workflow_id=wid, user_id=member.id, role="member")
    assert result == {"status": "failed", "error": None, "node_results": {}, "variables_tail": {}}


async def test_diagnosis_only_covers_the_callers_own_run_of_this_workflow(
    adapter: Phase4aAdapter, member: Member, second_member: Member, db: Db
) -> None:
    wid = await db.seed_workflow(member, is_public=True)
    other_wid = await db.seed_workflow(member)
    mine = await db.seed_execution(wid, member, status="failed", error="mine")
    theirs = await db.seed_execution(
        wid, second_member, status="failed", error="theirs", variables={"leak": "secret-ish"}
    )
    other_workflow = await db.seed_execution(other_wid, member, status="failed", error="elsewhere")
    anonymous = await db.seed_execution(wid, None, status="failed", error="anon")

    own = await adapter.diagnose(mine, workflow_id=wid, user_id=member.id, role="member")
    assert own is not None and own["error"] == "mine"
    # another user's run of the same workflow
    assert await adapter.diagnose(theirs, workflow_id=wid, user_id=member.id, role="member") is None
    # my own run, but of another workflow than the one being built
    assert (
        await adapter.diagnose(other_workflow, workflow_id=wid, user_id=member.id, role="member")
        is None
    )
    # an anonymous (no user) run is nobody's
    assert (
        await adapter.diagnose(anonymous, workflow_id=wid, user_id=member.id, role="member") is None
    )
    # a missing execution
    assert (
        await adapter.diagnose(
            "no-such-execution", workflow_id=wid, user_id=member.id, role="member"
        )
        is None
    )
    # and the other user sees theirs, not mine
    assert (
        await adapter.diagnose(theirs, workflow_id=wid, user_id=second_member.id, role="member")
    ) is not None
    assert (
        await adapter.diagnose(mine, workflow_id=wid, user_id=second_member.id, role="member")
        is None
    )


async def test_admin_diagnoses_any_users_run_of_this_workflow_only(
    adapter: Phase4aAdapter, member: Member, second_member: Member, admin: Member, db: Db
) -> None:
    wid = await db.seed_workflow(member, is_public=True)
    other_wid = await db.seed_workflow(second_member)
    theirs = await db.seed_execution(wid, second_member, status="failed", error="theirs")
    anonymous = await db.seed_execution(wid, None, status="failed", error="anon")
    elsewhere = await db.seed_execution(
        other_wid, second_member, status="failed", error="elsewhere"
    )

    got = await adapter.diagnose(theirs, workflow_id=wid, user_id=admin.id, role="admin")
    assert got is not None and got["error"] == "theirs"
    anon = await adapter.diagnose(anonymous, workflow_id=wid, user_id=admin.id, role="admin")
    assert anon is not None and anon["error"] == "anon"
    # never another workflow's run, even for an admin
    assert (
        await adapter.diagnose(elsewhere, workflow_id=wid, user_id=admin.id, role="admin") is None
    )
    assert (
        await adapter.diagnose("no-such-execution", workflow_id=wid, user_id=admin.id, role="admin")
        is None
    )
