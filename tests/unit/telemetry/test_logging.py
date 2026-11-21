import json
import logging

from src.telemetry.context import bind_context
from src.telemetry.logging import JSONFormatter


def test_json_formatter_redacts_and_adds_context() -> None:
    record = logging.LogRecord("test", logging.INFO, "", 0, "token=secret", (), None)
    with bind_context(request_id="opaque", execution_id="token=secret"):
        data = json.loads(JSONFormatter().format(record))
    assert data["request_id"] == "opaque"
    assert data["execution_id"] == "[REDACTED]"
    assert "secret" not in json.dumps(data)


def test_formatter_drops_untrusted_messages_and_extra_payload() -> None:
    record = logging.LogRecord("test", logging.INFO, "", 0, "private body contents", (), None)
    record.body = "private body contents"
    assert "private body contents" not in JSONFormatter().format(record)


def test_logging_setup_does_not_add_handlers_on_repeat() -> None:
    from src.telemetry.logging import configure_logging

    configure_logging("INFO")
    before = list(logging.getLogger().handlers)
    configure_logging("INFO")
    assert logging.getLogger().handlers == before


def test_existing_root_handler_is_structured_and_redacted() -> None:
    import io

    from src.telemetry.logging import configure_logging

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        configure_logging("INFO")
        root.info("private body contents")
        assert "private body contents" not in stream.getvalue()
        assert json.loads(stream.getvalue())["level"] == "INFO"
    finally:
        root.removeHandler(handler)
