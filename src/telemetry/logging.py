"""Privacy-preserving structured logs with approved operational events."""

import json
import logging
import re
from dataclasses import asdict
from typing import TYPE_CHECKING, cast

from src.telemetry.context import current_correlation, exception_correlation
from src.telemetry.redaction import redact

if TYPE_CHECKING:
    from types import TracebackType

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


_MAX_LINE_BYTES = 4096
_MAX_FRAMES = 15
_MAX_ROOT_FRAMES = 10
_MAX_CHAIN = 5
_NAME_CAP = 200
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.<>-]")


def _safe_name(name: str) -> str:
    return _SAFE_NAME.sub("_", name)[:_NAME_CAP]


def _class_name(exc_type: type[BaseException]) -> str:
    return _safe_name(f"{exc_type.__module__}.{exc_type.__qualname__}")


def _module_for_path(path: str) -> str:
    """Reduce a source path to a dotted module name with no directories, drive or user."""
    parts = [p for p in re.split(r"[\\/]+", path) if p and not p.endswith(":")]
    for marker in ("site-packages", "dist-packages"):
        if marker in parts:
            parts = parts[len(parts) - parts[::-1].index(marker) :]
            break
    else:
        for root in ("src", "tests"):
            if root in parts:
                parts = parts[parts.index(root) :]
                break
        else:
            parts = parts[-1:]
    if parts and parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    if parts and parts[-1] == "__init__" and len(parts) > 1:
        parts.pop()
    return _safe_name(".".join(parts))


def _frames_of(exc: BaseException) -> list[str]:
    frames: list[str] = []
    tb: TracebackType | None = exc.__traceback__
    while tb is not None:
        frame = tb.tb_frame
        module = frame.f_globals.get("__name__")
        name = (
            _safe_name(module)
            if isinstance(module, str) and module != "__main__"
            else _module_for_path(frame.f_code.co_filename)
        )
        frames.append(f"{name}:{_safe_name(frame.f_code.co_name)}:{tb.tb_lineno}")
        tb = tb.tb_next
    return frames


def _root_cause(exc: BaseException) -> BaseException:
    """The innermost exception of the `__cause__`/`__context__` chain (cycle-safe, bounded)."""
    seen = {id(exc)}
    current = exc
    for _ in range(50):
        nxt = current.__cause__ or current.__context__
        if nxt is None or id(nxt) in seen:
            break
        seen.add(id(nxt))
        current = nxt
    return current


def _exception_fields(exc_info: tuple[object, ...]) -> dict[str, object]:
    """Class names and code locations only: never the message, values or file paths."""
    exc = exc_info[1] if len(exc_info) > 1 else None
    if not isinstance(exc, BaseException):
        return {}
    fields: dict[str, object] = {"exception.type": _class_name(type(exc))}
    chain: list[str] = []
    seen = {id(exc)}
    nxt = exc.__cause__ or exc.__context__
    while nxt is not None and id(nxt) not in seen and len(chain) < _MAX_CHAIN:
        seen.add(id(nxt))
        chain.append(_class_name(type(nxt)))
        nxt = nxt.__cause__ or nxt.__context__
    if chain:
        fields["exception.chain"] = chain
    frames = _frames_of(exc)
    if frames:
        fields["exception.frames"] = frames[-_MAX_FRAMES:]
    root = _root_cause(exc)
    if root is not exc:
        root_frames = _frames_of(root)
        if root_frames:
            fields["exception.root_frames"] = root_frames[-_MAX_ROOT_FRAMES:]
    return fields


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
        if record.exc_info:
            fields.update(_exception_fields(record.exc_info))
        return self._dump(fields)

    @staticmethod
    def _dump(fields: dict[str, object]) -> str:
        """Serialize under the byte cap: outer frames first, then root frames, then the chain."""
        while True:
            line = json.dumps(redact(fields, max_bytes=_MAX_LINE_BYTES), ensure_ascii=False)
            if len(line.encode("utf-8")) <= _MAX_LINE_BYTES:
                return line
            frames = fields.get("exception.frames")
            root_frames = fields.get("exception.root_frames")
            chain = fields.get("exception.chain")
            if isinstance(frames, list) and frames:
                fields["exception.frames"] = frames[1:]
            elif isinstance(root_frames, list) and root_frames:
                fields["exception.root_frames"] = root_frames[1:]
            elif isinstance(chain, list) and chain:
                fields["exception.chain"] = chain[:-1]
            else:
                return line


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
