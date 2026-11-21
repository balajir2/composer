from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

from opentelemetry import baggage, context, trace
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags, TraceState
from starlette.datastructures import Headers

from src.telemetry.propagation import extract_task_context, inject_task_headers

if TYPE_CHECKING:
    from collections.abc import MutableMapping


def _current_trace_context() -> SpanContext:
    return SpanContext(
        trace_id=0x0123456789ABCDEF0123456789ABCDEF,
        span_id=0x0123456789ABCDEF,
        is_remote=False,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
        trace_state=TraceState([("vendor", "state")]),
    )


def test_injection_emits_w3c_context_without_baggage_and_preserves_headers() -> None:
    span_context = _current_trace_context()
    current = trace.set_span_in_context(NonRecordingSpan(span_context))
    current = baggage.set_baggage("private", "must-not-propagate", context=current)
    headers = {"Content-Type": "application/json", "X-Request-Mode": "run"}
    token = context.attach(current)
    try:
        inject_task_headers(headers)
    finally:
        context.detach(token)

    assert headers["Content-Type"] == "application/json"
    assert headers["X-Request-Mode"] == "run"
    assert headers["traceparent"] == "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"
    assert headers["tracestate"] == "vendor=state"
    assert "baggage" not in {name.lower() for name in headers}
    assert "must-not-propagate" not in repr(headers)


def test_extraction_creates_remote_parent_case_insensitively() -> None:
    extracted = extract_task_context(
        {
            "TraceParent": "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01",
            "TrAcEsTaTe": "vendor=state",
        }
    )
    span_context = trace.get_current_span(extracted).get_span_context()

    assert span_context.is_valid
    assert span_context.is_remote
    assert span_context.trace_id == 0x0123456789ABCDEF0123456789ABCDEF
    assert span_context.span_id == 0x0123456789ABCDEF
    assert span_context.trace_state["vendor"] == "state"


def test_extraction_rejects_malformed_oversized_duplicate_and_conflicting_headers() -> None:
    valid = "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"
    invalid_headers = [
        {"traceparent": "not-a-traceparent"},
        {"traceparent": "00-" + "a" * 300},
        {"traceparent": f"{valid}, {valid}"},
        {
            "traceparent": valid,
            "TraceParent": "00-11111111111111111111111111111111-2222222222222222-01",
        },
        {"traceparent": valid, "tracestate": "x=" + "a" * 513},
        {"traceparent": valid, "tracestate": "not-a-member"},
        {"traceparent": valid, "tracestate": "vendor=one,vendor=two"},
    ]

    for headers in invalid_headers:
        extracted = extract_task_context(headers)
        assert not trace.get_current_span(extracted).get_span_context().is_valid


def test_extraction_never_extracts_baggage() -> None:
    extracted = extract_task_context(
        {
            "traceparent": "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01",
            "baggage": "secret=value",
        }
    )

    assert baggage.get_baggage("secret", context=extracted) is None


def test_extraction_rejects_duplicate_asgi_headers() -> None:
    headers = Headers(
        raw=[
            (b"traceparent", b"00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"),
            (b"TraceParent", b"00-11111111111111111111111111111111-2222222222222222-01"),
        ]
    )

    extracted = extract_task_context(headers)

    assert not trace.get_current_span(extracted).get_span_context().is_valid


def test_injection_is_noop_when_propagator_fails() -> None:
    headers: MutableMapping[str, str] = {"Content-Type": "application/json"}

    with patch(
        "src.telemetry.propagation._TRACE_CONTEXT_PROPAGATOR.inject", side_effect=RuntimeError
    ):
        inject_task_headers(headers)

    assert dict(headers) == {"Content-Type": "application/json"}
