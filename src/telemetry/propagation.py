"""W3C trace-context propagation for durable Cloud Tasks delivery."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from opentelemetry import context
from opentelemetry.context import Context
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

if TYPE_CHECKING:
    from collections.abc import Mapping, MutableMapping

_TRACE_CONTEXT_PROPAGATOR = TraceContextTextMapPropagator()
_TRACEPARENT_PATTERN = re.compile(
    r"^[0-9a-f]{2}-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$", re.IGNORECASE
)
_TRACESTATE_KEY_PATTERN = re.compile(
    r"^(?:[a-z][a-z0-9_*/-]*|[a-z0-9][a-z0-9_*/-]*@[a-z][a-z0-9_*/-]*)$"
)
_TRACESTATE_VALUE_PATTERN = re.compile(
    r"^[\x20-\x2b\x2d-\x3c\x3e-\x7e]{0,255}[\x21-\x2b\x2d-\x3c\x3e-\x7e]$"
)
_TRACEPARENT_MAX_BYTES = 55
_TRACESTATE_MAX_BYTES = 512


def _header_values(headers: Mapping[str, str], name: str) -> list[str]:
    """Read a header case-insensitively, preserving duplicate evidence."""
    return [value for key, value in headers.items() if key.lower() == name]


def _single_header(headers: Mapping[str, str], name: str) -> str | None:
    values = _header_values(headers, name)
    if len(values) > 1:
        raise ValueError(f"duplicate {name} header")
    return values[0] if values else None


def _valid_tracestate(value: str) -> bool:
    """Check W3C tracestate syntax without logging untrusted header values."""
    if len(value.encode("utf-8")) > _TRACESTATE_MAX_BYTES:
        return False
    if not value:
        return True
    members = value.split(",")
    if len(members) > 32:
        return False
    keys: set[str] = set()
    for member in members:
        key, separator, item_value = member.strip(" \t").partition("=")
        if (
            not separator
            or len(key) > 256
            or len(item_value) > 256
            or not _TRACESTATE_KEY_PATTERN.fullmatch(key)
            or not _TRACESTATE_VALUE_PATTERN.fullmatch(item_value)
            or key in keys
        ):
            return False
        keys.add(key)
    return True


def inject_task_headers(headers: MutableMapping[str, str]) -> None:
    """Inject only W3C trace context, leaving task and OIDC data untouched."""
    try:
        propagated: dict[str, str] = {}
        _TRACE_CONTEXT_PROPAGATOR.inject(propagated, context=context.get_current())
        allowed = {
            name: value
            for name, value in propagated.items()
            if name.lower() in {"traceparent", "tracestate"}
        }
        if "traceparent" in allowed:
            traceparent = allowed["traceparent"]
            if len(
                traceparent.encode("ascii")
            ) > _TRACEPARENT_MAX_BYTES or not _TRACEPARENT_PATTERN.fullmatch(traceparent):
                return
        if (
            "tracestate" in allowed
            and len(allowed["tracestate"].encode("utf-8")) > _TRACESTATE_MAX_BYTES
        ):
            return
        if not allowed:
            return

        for existing in list(headers):
            if existing.lower() in {"traceparent", "tracestate"}:
                del headers[existing]
        headers.update(allowed)
    except Exception:
        # Telemetry must never prevent durable task creation.
        return


def extract_task_context(headers: Mapping[str, str]) -> Context:
    """Extract a validated remote W3C parent, or return a clean context."""
    try:
        traceparent = _single_header(headers, "traceparent")
        tracestate = _single_header(headers, "tracestate")
        if traceparent is None:
            return Context()
        if len(
            traceparent.encode("ascii")
        ) > _TRACEPARENT_MAX_BYTES or not _TRACEPARENT_PATTERN.fullmatch(traceparent):
            return Context()
        if tracestate is not None and not _valid_tracestate(tracestate):
            return Context()
        carrier = {"traceparent": traceparent}
        if tracestate is not None:
            carrier["tracestate"] = tracestate
        return _TRACE_CONTEXT_PROPAGATOR.extract(carrier, context=Context())
    except Exception:
        return Context()


__all__ = ["extract_task_context", "inject_task_headers"]
