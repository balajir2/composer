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
