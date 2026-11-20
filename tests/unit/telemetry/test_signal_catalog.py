"""Validate the machine-readable telemetry contract and its cardinality guard."""

import json
from pathlib import Path
from typing import Any

import pytest

CATALOG = Path(__file__).resolve().parents[3] / "docs/telemetry/signal-catalog.md"
APPROVED_ATTRIBUTES = {
    "trigger_kind",
    "environment",
    "status",
    "failure_code",
    "result",
    "node_type",
    "provider",
    "operation",
    "decision",
    "expired",
    "eligibility_reason",
    "model_family",
    "token_type",
    "cost_source",
    "component",
}


def validate(contract: dict[str, Any]) -> None:
    vocabularies = contract["vocabularies"]
    names: set[str] = set()
    for metric in contract["metrics"]:
        name = metric["name"]
        assert name not in names, f"duplicate metric: {name}"
        names.add(name)
        for field in ("owner", "type", "unit", "retention", "redaction"):
            assert metric.get(field), f"{name}: missing {field}"
        for attribute in metric["attributes"]:
            assert attribute in APPROVED_ATTRIBUTES, f"forbidden attribute: {attribute}"
            assert attribute in vocabularies, f"unknown vocabulary: {attribute}"
            values = vocabularies[attribute]
            assert (
                isinstance(values, list)
                and values
                and all(isinstance(v, str) and v for v in values)
            ), f"unbounded attribute: {attribute}"
    assert names, "empty catalog"


def test_catalog_is_valid() -> None:
    text = CATALOG.read_text(encoding="utf-8") if CATALOG.exists() else ""
    assert "```json\n" in text, "catalog contract missing"
    validate(json.loads(text.split("```json\n", 1)[1].split("\n```", 1)[0]))


@pytest.mark.parametrize(
    "mutation, message",
    [
        ("duplicate", "duplicate metric"),
        ("unit", "missing unit"),
        ("unbounded", "unbounded attribute"),
        ("identifier", "forbidden attribute"),
        ("unknown", "unknown vocabulary"),
    ],
)
def test_validator_rejects_invalid_contracts(mutation: str, message: str) -> None:
    metric = {
        "name": "example",
        "owner": "team",
        "type": "Counter",
        "unit": "s",
        "retention": "operational",
        "redaction": "metadata",
        "attributes": ["result"],
    }
    contract: dict[str, Any] = {"vocabularies": {"result": ["ok"]}, "metrics": [metric]}
    if mutation == "duplicate":
        contract["metrics"].append(metric.copy())
    elif mutation == "unit":
        metric["unit"] = ""
    elif mutation == "unbounded":
        contract["vocabularies"]["result"] = []
    elif mutation == "identifier":
        metric["attributes"] = ["execution_id"]
    else:
        metric["attributes"] = ["provider"]
    with pytest.raises(AssertionError, match=message):
        validate(contract)


@pytest.mark.parametrize(
    "attribute", ["executionId", "email_address", "exception_text", "prompt_text", "response_body"]
)
def test_validator_rejects_sensitive_attribute_aliases(attribute: str) -> None:
    contract: dict[str, Any] = {
        "vocabularies": {attribute: ["example"]},
        "metrics": [
            {
                "name": "example",
                "owner": "team",
                "type": "Counter",
                "unit": "s",
                "retention": "operational",
                "redaction": "metadata",
                "attributes": [attribute],
            }
        ],
    }
    with pytest.raises(AssertionError, match="forbidden attribute"):
        validate(contract)
