from src.telemetry.attributes import safe_attributes


def test_metrics_allow_only_catalog_attributes_and_vocabulary() -> None:
    assert safe_attributes(
        "composer.execution.terminal",
        {
            "status": "failed",
            "failure_code": None,
            "trigger_kind": "unknown",
            "execution_id": "private",
            "provider": "openai",
        },
    ) == {"status": "failed", "failure_code": "none", "trigger_kind": "other"}


def test_unknown_signal_drops_every_attribute() -> None:
    assert safe_attributes("unknown", {"status": "failed"}) == {}


def test_catalog_matches_compiled_attribute_policy() -> None:
    import json
    from pathlib import Path
    from typing import cast

    from src.telemetry.attributes import SIGNALS, VOCABULARIES

    source = Path("docs/telemetry/signal-catalog.md").read_text(encoding="utf-8")
    contract = cast(
        "dict[str, object]", json.loads(source.split("```json\n", 1)[1].split("\n```", 1)[0])
    )
    vocab = cast("dict[str, list[str]]", contract["vocabularies"])
    metrics = cast("list[dict[str, object]]", contract["metrics"])
    assert {key: frozenset(values) for key, values in vocab.items()} == VOCABULARIES
    assert {
        str(metric["name"]): tuple(cast("list[str]", metric["attributes"])) for metric in metrics
    } == SIGNALS
    assert all(values and len(values) == len(set(values)) for values in vocab.values())
