# The logging handler is replaced with a list-backed test double below.
# pyright: reportAttributeAccessIssue=false
import json
import logging
import re

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


def test_approved_events_preserve_classification_and_bounded_counts() -> None:
    from src.telemetry.logging import log_event

    class Capture(logging.Handler):
        def __init__(self) -> None:
            super().__init__()
            self.records: list[logging.LogRecord] = []

        def emit(self, record: logging.LogRecord) -> None:
            self.records.append(record)

    logger = logging.getLogger("test.safe.events")
    handler = Capture()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        log_event(logger, "startup", count=3, payload="private body", url="https://x/?token=secret")
        log_event(logger, "sweeper.scan", count=7, error_count=2)
        startup, sweep = (json.loads(JSONFormatter().format(r)) for r in handler.records)
        assert startup["event"] == "startup"
        assert sweep["event"] == "sweeper.scan"
        assert sweep["count"] == 7 and sweep["error_count"] == 2
        assert "private body" not in json.dumps(startup)
        assert "secret" not in json.dumps(startup)
    finally:
        logger.removeHandler(handler)


def test_existing_sweeper_and_configuration_templates_are_classified() -> None:
    formatter = JSONFormatter()
    startup = logging.LogRecord(
        "src.main",
        logging.INFO,
        "",
        0,
        "Starting Composer v%s in %s mode",
        ("secret", "production"),
        None,
    )
    sweep = logging.LogRecord(
        "src.maintenance.execution_sweeper",
        logging.INFO,
        "",
        0,
        "execution_sweeper: scanned %d orphaned attempts, %d failed, %d errored",
        (10, 3, 1),
        None,
    )
    config = logging.LogRecord(
        "src.main",
        logging.WARNING,
        "",
        0,
        "cors: COMPOSER_FRONTEND_ORIGINS is empty in standalone+production \u2014 every cross-origin request from a browser will be rejected. Set the env var to a comma-separated list of frontend origins.",
        (),
        None,
    )
    assert json.loads(formatter.format(startup))["event"] == "startup"
    assert json.loads(formatter.format(sweep))["event"] == "sweeper.orphaned"
    assert json.loads(formatter.format(sweep))["count"] == 10
    assert json.loads(formatter.format(config))["event"] == "config.cors_empty"
    assert "secret" not in formatter.format(startup)


def test_default_uvicorn_handlers_are_structured_without_payloads() -> None:
    import os
    import subprocess
    import sys

    program = """\nimport logging, logging.config\nfrom uvicorn.config import LOGGING_CONFIG\nfrom src.telemetry.context import bind_context\nfrom src.telemetry.logging import configure_logging\nlogging.config.dictConfig(LOGGING_CONFIG)\nconfigure_logging("INFO")\nwith bind_context(request_id="opaque"):\n    logging.getLogger("uvicorn.access").info("%s - \\\"%s %s HTTP/%s\\\" %d", "client", "GET", "/?token=secret", "1.1", 500)\n    try:\n        raise ValueError("private body secret")\n    except ValueError:\n        logging.getLogger("uvicorn.error").exception("token=secret")\n"""
    result = subprocess.run(
        [sys.executable, "-c", program],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=True,
    )
    output = result.stdout + result.stderr
    assert "secret" not in output and "private body" not in output
    rows = [json.loads(line) for line in output.splitlines() if line.strip()]
    assert len(rows) == 2
    assert all(row["request_id"] == "opaque" for row in rows)
    access = next(row for row in rows if row["logger"] == "uvicorn.access")
    assert access["event"] == "http.access"
    assert access["status_code"] == 500 and access["method"] == "GET"


def test_log_event_fields_are_bounded_and_unknown_events_rejected() -> None:
    import pytest

    from src.telemetry.logging import log_event

    logger = logging.getLogger("test.bounds")
    handler = logging.Handler()
    records = []
    handler.emit = records.append
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        log_event(
            logger,
            "sweeper.scan",
            count=10**12,
            failed_count=-1,
            error_count=True,
            url="https://x/?token=secret",
        )
        data = json.loads(JSONFormatter().format(records[0]))
        assert data["event"] == "sweeper.scan"
        assert not {"count", "failed_count", "error_count", "url"} & data.keys()
        with pytest.raises(ValueError):
            log_event(logger, "attacker-controlled-event", payload="secret")
    finally:
        logger.removeHandler(handler)


def test_malformed_log_arguments_never_trigger_raw_logging_diagnostics() -> None:
    import os
    import subprocess
    import sys

    program = """\nimport logging\nfrom src.telemetry.logging import configure_logging\nconfigure_logging("INFO")\nlogging.getLogger("test").info("status=%d", "secret-token")\n"""
    result = subprocess.run(
        [sys.executable, "-c", program],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=True,
    )
    assert "secret-token" not in result.stdout + result.stderr
    assert "Logging error" not in result.stderr


# --- exception diagnostics: class names and code locations only ---------------------------

_SECRET_MSG = "boom sk-live-SECRET123 for alice@example.com at /home/alice/site-packages/x"


class _Boom(Exception):
    pass


def _deep(n: int) -> None:
    if n == 0:
        raise _Boom(_SECRET_MSG)
    _deep(n - 1)


def _exc_record(
    exc: BaseException, name: str = "src.engine.langgraph_executor"
) -> logging.LogRecord:
    return logging.LogRecord(
        name,
        logging.ERROR,
        "",
        0,
        "Execution %s failed",
        ("abc",),
        (type(exc), exc, exc.__traceback__),
    )


def _capture(n: int = 0) -> BaseException:
    try:
        _deep(n)
    except _Boom as exc:
        return exc
    raise AssertionError


def test_exception_record_has_type_and_frames() -> None:
    out = JSONFormatter().format(_exc_record(_capture(2)))
    data = json.loads(out)
    assert data["exception.type"].endswith("test_logging._Boom")
    frames = data["exception.frames"]
    assert re.fullmatch(r"([A-Za-z0-9_]+[.])*test_logging:_deep:[0-9]+", frames[-1])
    assert frames[-1].rsplit(":", 1)[1].isdigit()
    assert data["level"] == "ERROR"
    assert "event" not in data
    assert "Execution" not in out


def test_exception_chain_lists_class_names_innermost_last() -> None:
    try:
        try:
            raise KeyError("inner-secret")
        except KeyError as inner:
            raise ValueError("middle") from inner
    except ValueError as middle:
        try:
            raise RuntimeError("outer") from middle
        except RuntimeError as outer:
            exc = outer
    data = json.loads(JSONFormatter().format(_exc_record(exc)))
    assert data["exception.type"] == "builtins.RuntimeError"
    assert data["exception.chain"] == ["builtins.ValueError", "builtins.KeyError"]
    assert "inner-secret" not in json.dumps(data)


def test_exception_message_never_emitted() -> None:
    out = JSONFormatter().format(_exc_record(_capture(1)))
    for needle in ("sk-live-SECRET123", "alice", "example.com", "boom"):
        assert needle not in out


def test_exception_output_has_no_paths() -> None:
    import os
    import sys

    out = JSONFormatter().format(_exc_record(_capture(3)))
    bs = chr(92)
    for needle in (os.path.expanduser("~"), "site-packages", bs, "C:", "/home/", "Users"):
        assert needle not in out
    assert sys.prefix not in out
    from src.telemetry.logging import _module_for_path  # pyright: ignore[reportPrivateUsage]

    win = bs.join(["C:", "x", ".venv", "Lib", "site-packages", "httpx", "_client.py"])
    assert _module_for_path(win) == "httpx._client"
    assert (
        _module_for_path("/usr/lib/python3.12/site-packages/langchain_core/runnables/base.py")
        == "langchain_core.runnables.base"
    )
    assert _module_for_path("/home/bob/stuff/other.py") == "other"


def test_frame_cap_is_15_and_keeps_innermost() -> None:
    data = json.loads(JSONFormatter().format(_exc_record(_capture(40))))
    frames = data["exception.frames"]
    assert len(frames) == 15
    assert all(f.endswith(tuple("0123456789")) for f in frames)
    assert frames[-1].split(":")[1] == "_deep"
    assert not any(":_capture:" in f for f in frames)  # outermost frames were dropped


def test_output_stays_under_4096_bytes_with_deep_stack() -> None:
    exc = _capture(40)
    long_name = "m" * 300
    chain_exc: BaseException = exc
    for i in range(8):
        cls = type(f"{long_name}{i}", (Exception,), {})
        new = cls("x")
        new.__cause__ = chain_exc
        chain_exc = new
    out = JSONFormatter().format(_exc_record(chain_exc))
    assert len(out.encode()) <= 4096
    json.loads(out)


def test_uvicorn_error_gets_fields_and_keeps_correlation() -> None:
    with bind_context(request_id="req-1"):
        data = json.loads(JSONFormatter().format(_exc_record(_capture(1), "uvicorn.error")))
    assert data["request_id"] == "req-1"
    assert data["logger"] == "uvicorn.error"
    assert "exception.type" in data and "exception.frames" in data


def test_records_without_exc_info_have_no_exception_fields() -> None:
    record = logging.LogRecord("src.x", logging.ERROR, "", 0, "plain", (), None)
    data = json.loads(JSONFormatter().format(record))
    assert not any(k.startswith("exception.") for k in data)
