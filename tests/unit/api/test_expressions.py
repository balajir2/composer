"""Tests for /expressions test/preview endpoints."""

from fastapi.testclient import TestClient

from src.main import create_app
from src.security.rate_limit import RateLimiter


def _client() -> TestClient:
    app = create_app()
    app.state.rate_limiter = RateLimiter()
    return TestClient(app)


def test_evaluate_transform_success() -> None:
    client = _client()
    resp = client.post(
        "/expressions/evaluate-transform",
        json={"expression": "lastOutput.upper()", "variables": {"lastOutput": "hi"}},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"ok": True, "result": "HI", "error": None}


def test_evaluate_transform_undefined_name_returns_ok_false() -> None:
    client = _client()
    resp = client.post(
        "/expressions/evaluate-transform",
        json={"expression": "not_a_real_name + 1", "variables": {}},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert "not_a_real_name" in body["error"]


def test_evaluate_transform_reads_top_level_variable() -> None:
    client = _client()
    resp = client.post(
        "/expressions/evaluate-transform",
        json={"expression": "counter + 1", "variables": {"counter": 41}},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True, "result": 42, "error": None}


def test_evaluate_transform_defaults_variables_to_empty_dict() -> None:
    client = _client()
    resp = client.post("/expressions/evaluate-transform", json={"expression": "1 + 1"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True, "result": 2, "error": None}


def test_evaluate_transform_last_output_defaults_to_empty_string() -> None:
    """Regression test: with no sample variables at all, lastOutput must
    behave like a fresh workflow run (lastOutput=""), not silently become
    None -- otherwise an expression that's valid at a real Start node
    (e.g. string-concatenating lastOutput) would falsely fail here."""
    client = _client()
    resp = client.post(
        "/expressions/evaluate-transform",
        json={"expression": 'lastOutput + "!"', "variables": {}},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True, "result": "!", "error": None}


def test_evaluate_transform_zero_division_returns_ok_false_not_500() -> None:
    """Regression test: evaluate()'s EvalError wrapping doesn't cover every
    exception simpleeval can raise (ZeroDivisionError isn't in its except
    tuple) -- this endpoint has no engine-level catch-all net beneath it, so
    it must not 500 on an expression a designer would plausibly type while
    testing a draft."""
    client = _client()
    resp = client.post("/expressions/evaluate-transform", json={"expression": "1 / 0"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert "ZeroDivisionError" in body["error"]


def test_evaluate_data_transform_map() -> None:
    client = _client()
    resp = client.post(
        "/expressions/evaluate-data-transform",
        json={
            "operation": "map",
            "collection": "variables['nums']",
            "expression": "item * 2",
            "itemVar": "item",
            "variables": {"nums": [1, 2, 3]},
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["result"] == [2, 4, 6]
    assert body["itemCount"] == 3
    assert body["truncated"] is False


def test_evaluate_data_transform_reduce_with_initial() -> None:
    client = _client()
    resp = client.post(
        "/expressions/evaluate-data-transform",
        json={
            "operation": "reduce",
            "collection": "variables['nums']",
            "expression": "acc + item",
            "itemVar": "item",
            "initial": 0,
            "variables": {"nums": [1, 2, 3, 4]},
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"ok": True, "result": 10, "error": None, "itemCount": 4, "truncated": False}


def test_evaluate_data_transform_non_list_collection() -> None:
    client = _client()
    resp = client.post(
        "/expressions/evaluate-data-transform",
        json={
            "operation": "map",
            "collection": "variables['scalar']",
            "expression": "item",
            "itemVar": "item",
            "variables": {"scalar": 42},
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert "non-iterable" in body["error"] or "int" in body["error"]


def test_evaluate_data_transform_unknown_operation() -> None:
    client = _client()
    resp = client.post(
        "/expressions/evaluate-data-transform",
        json={
            "operation": "sum",
            "collection": "variables['nums']",
            "expression": "item",
            "itemVar": "item",
            "variables": {"nums": [1, 2]},
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert "sum" in body["error"]


def test_evaluate_data_transform_truncates_at_50_items() -> None:
    client = _client()
    resp = client.post(
        "/expressions/evaluate-data-transform",
        json={
            "operation": "map",
            "collection": "variables['nums']",
            "expression": "item",
            "itemVar": "item",
            "variables": {"nums": list(range(60))},
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["itemCount"] == 50
    assert body["truncated"] is True
    assert len(body["result"]) == 50


def test_evaluate_data_transform_bad_per_item_expression() -> None:
    client = _client()
    resp = client.post(
        "/expressions/evaluate-data-transform",
        json={
            "operation": "map",
            "collection": "variables['nums']",
            "expression": "item.nonexistent_attr",
            "itemVar": "item",
            "variables": {"nums": [1, 2]},
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False


def test_evaluate_data_transform_reduce_zero_division_returns_ok_false_not_500() -> None:
    """Regression test, same class of bug fixed in evaluate-transform (see
    commit b5af25f): evaluate()'s EvalError wrapping doesn't cover every
    exception simpleeval can raise. This endpoint has no engine-level
    catch-all beneath it, so a per-item expression that raises
    ZeroDivisionError (or any other exception outside EvalError's tuple)
    must still return {ok: false}, not a raw 500."""
    client = _client()
    resp = client.post(
        "/expressions/evaluate-data-transform",
        json={
            "operation": "reduce",
            "collection": "variables['nums']",
            "expression": "acc + (item / 0)",
            "itemVar": "item",
            "initial": 0,
            "variables": {"nums": [1, 2]},
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False
