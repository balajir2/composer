"""Privacy-preserving structured logs with approved operational events."""

import json
import logging
from dataclasses import asdict
from typing import cast

from src.telemetry.context import current_correlation, exception_correlation
from src.telemetry.redaction import redact

# Only fixed, application-owned templates can supply legacy event names and counts.
_TEMPLATES = {
    ("src.main", "Starting Composer v%s in %s mode"): ("startup", ()),
    ("src.main", "Shutting down Composer"): ("shutdown", ()),
    (
        "src.main",
        "cors: COMPOSER_FRONTEND_ORIGINS is empty in standalone+production — "
        "every cross-origin request from a browser will be rejected. "
        "Set the env var to a comma-separated list of frontend origins.",
    ): ("config.cors_empty", ()),
    (
        "src.maintenance.execution_sweeper",
        "execution_sweeper: scanned %d orphaned attempts, %d failed, %d errored",
    ): ("sweeper.orphaned", ("count", "failed_count", "error_count")),
    (
        "src.maintenance.execution_sweeper",
        "execution_sweeper: scanned %d approval pauses, %d expired, %d errored",
    ): ("sweeper.approvals", ("count", "expired_count", "error_count")),
    (
        "src.maintenance.execution_sweeper",
        "execution_sweeper: scanned %d expired leases, %d failed, %d errored",
    ): ("sweeper.leases", ("count", "failed_count", "error_count")),
    (
        "src.maintenance.execution_sweeper",
        "execution_sweeper: deleted %d execution_events past retention",
    ): ("sweeper.events_deleted", ("count",)),
    (
        "src.maintenance.execution_sweeper",
        "execution_sweeper: disabled (interval_seconds=%d)",
    ): ("sweeper.disabled", ("interval_seconds",)),
    (
        "src.maintenance.execution_sweeper",
        "execution_sweeper: started (interval=%ds, orphan_grace=%ds, approval_timeout=%dh)",
    ): ("sweeper.started", ("interval_seconds", "orphan_grace_seconds", "approval_timeout_hours")),
    ("src.maintenance.execution_sweeper", "execution_sweeper: unexpected error in loop"): (
        "sweeper.error",
        (),
    ),
}
_EVENT_FIELDS: dict[str, frozenset[str]] = {
    "startup": frozenset(),
    "shutdown": frozenset(),
    "config.cors_empty": frozenset(),
    "sweeper.scan": frozenset({"count", "failed_count", "error_count", "expired_count"}),
    "sweeper.orphaned": frozenset({"count", "failed_count", "error_count"}),
    "sweeper.approvals": frozenset({"count", "expired_count", "error_count"}),
    "sweeper.leases": frozenset({"count", "failed_count", "error_count"}),
    "sweeper.events_deleted": frozenset({"count"}),
    "sweeper.disabled": frozenset({"interval_seconds"}),
    "sweeper.started": frozenset(
        {"interval_seconds", "orphan_grace_seconds", "approval_timeout_hours"}
    ),
    "sweeper.error": frozenset(),
}


def _approved_fields(event: str, fields: dict[str, object]) -> dict[str, int]:
    allowed = _EVENT_FIELDS.get(event, frozenset())
    return {
        key: value
        for key, value in fields.items()
        if key in allowed and type(value) is int and 0 <= value <= 1_000_000_000
    }


def log_event(logger: logging.Logger, event: str, **fields: object) -> None:
    """Emit an approved event and bounded integer fields; ignore other data."""
    if event not in _EVENT_FIELDS:
        raise ValueError("Unknown operational event")
    logger.info(
        "operational event",
        extra={"composer_event": event, "composer_fields": _approved_fields(event, fields)},
    )


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # Never interpolate untrusted arguments; logging error diagnostics can expose them.
        record.message = record.msg if type(record.msg) is str else ""
        fields: dict[str, object] = {"level": record.levelname, "logger": record.name}
        if (
            record.name == "uvicorn.access"
            and record.msg == '%s - "%s %s HTTP/%s" %d'
            and isinstance(record.args, tuple)
            and len(record.args) == 5
        ):
            fields["event"] = "http.access"
            method, status = record.args[1], record.args[4]
            if type(method) is str:
                known = {
                    "GET",
                    "HEAD",
                    "POST",
                    "PUT",
                    "DELETE",
                    "CONNECT",
                    "OPTIONS",
                    "TRACE",
                    "PATCH",
                }
                fields["method"] = method if method in known else "_OTHER"
            if type(status) is int and 100 <= status <= 599:
                fields["status_code"] = status
        template = (
            _TEMPLATES.get((record.name, record.msg)) if isinstance(record.msg, str) else None
        )
        if template is not None:
            event, keys = template
            args = record.args if isinstance(record.args, tuple) else ()
            fields["event"] = event
            fields.update(_approved_fields(event, dict(zip(keys, args, strict=False))))
        else:
            event = getattr(record, "composer_event", None)
            supplied = getattr(record, "composer_fields", None)
            if isinstance(event, str) and event in _EVENT_FIELDS and isinstance(supplied, dict):
                fields["event"] = event
                fields.update(_approved_fields(event, cast("dict[str, object]", supplied)))
        correlation = current_correlation()
        if correlation.request_id is None and record.name == "uvicorn.error" and record.exc_info:
            exc = record.exc_info[1]
            if exc is not None:
                correlation = exception_correlation(exc) or correlation
        fields.update({k: v for k, v in asdict(correlation).items() if v is not None})
        return json.dumps(redact(fields, max_bytes=4096), ensure_ascii=False)


def configure_logging(level: str) -> None:
    root = logging.getLogger()
    handler = next((h for h in root.handlers if getattr(h, "_composer_json", False)), None)
    if handler is None:
        handler = logging.StreamHandler()
        handler._composer_json = True  # type: ignore[attr-defined]
        root.addHandler(handler)
    # Uvicorn installs non-propagating handlers before application startup.
    # Format them directly, including existing child loggers with their own handlers.
    loggers = [
        root,
        logging.getLogger("uvicorn"),
        logging.getLogger("uvicorn.access"),
        logging.getLogger("uvicorn.error"),
    ]
    loggers.extend(
        logger
        for name, logger in logging.Logger.manager.loggerDict.items()
        if name.startswith("uvicorn.") and isinstance(logger, logging.Logger)
    )
    for logger in loggers:
        for root_handler in logger.handlers:
            root_handler.setFormatter(JSONFormatter())
    root.setLevel(level)
