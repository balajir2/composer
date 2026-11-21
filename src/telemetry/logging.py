"""Structured logging with bounded, scrubbed fields."""

import json
import logging
from dataclasses import asdict

from src.telemetry.context import current_correlation
from src.telemetry.redaction import redact


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()
        fields: dict[str, object] = {
            "level": record.levelname,
            "logger": record.name,
        }
        fields.update({k: v for k, v in asdict(current_correlation()).items() if v is not None})
        return json.dumps(redact(fields, max_bytes=4096), ensure_ascii=False)


def configure_logging(level: str) -> None:
    root = logging.getLogger()
    handler = next((h for h in root.handlers if getattr(h, "_composer_json", False)), None)
    if handler is None:
        handler = logging.StreamHandler()
        handler._composer_json = True  # type: ignore[attr-defined]
        root.addHandler(handler)
    for root_handler in root.handlers:
        root_handler.setFormatter(JSONFormatter())
    root.setLevel(level)
