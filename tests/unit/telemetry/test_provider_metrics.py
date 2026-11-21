"""Tests for bounded outbound provider and WebSocket metrics."""

import asyncio
from collections.abc import Iterator
from typing import Any, cast

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from src.telemetry.providers import configure_provider_metrics, provider_call


@pytest.fixture
def metric_reader() -> Iterator[InMemoryMetricReader]:
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    configure_provider_metrics(provider)
    yield reader
    configure_provider_metrics(None)
    provider.shutdown()


def _points(reader: InMemoryMetricReader) -> dict[str, list[dict[str, Any]]]:
    data = reader.get_metrics_data()
    assert data is not None
    return {
        metric.name: [
            {
                "attributes": dict(cast("dict[str, Any]", point.attributes)),
                "value": getattr(point, "value", None),
            }
            for point in metric.data.data_points
        ]
        for resource_metrics in data.resource_metrics
        for scope in resource_metrics.scope_metrics
        for metric in scope.metrics
    }


@pytest.mark.parametrize(
    ("status_code", "result"),
    [
        (200, "success"),
        (408, "timeout"),
        (429, "rate_limited"),
        (401, "authentication_error"),
        (503, "provider_error"),
        (302, "other"),
    ],
)
def test_provider_call_records_bounded_outcome(
    metric_reader: InMemoryMetricReader, status_code: int, result: str
) -> None:
    with provider_call("openai", "invoke") as call:
        call.record_status_code(status_code)

    points = _points(metric_reader)
    assert set(points) == {"composer.provider.request", "composer.provider.request.duration"}
    for name in points:
        assert points[name][0]["attributes"] == {
            "provider": "openai",
            "operation": "invoke",
            "result": result,
        }


async def test_provider_call_preserves_cancellation_and_records_it(
    metric_reader: InMemoryMetricReader,
) -> None:
    cancellation = asyncio.CancelledError("secret prompt")
    with pytest.raises(asyncio.CancelledError) as raised:
        async with provider_call("openai", "invoke"):
            raise cancellation
    assert raised.value is cancellation
    points = _points(metric_reader)
    assert points["composer.provider.request"][0]["attributes"] == {
        "provider": "openai",
        "operation": "invoke",
        "result": "cancellation",
    }
    assert "secret prompt" not in repr(points)


def test_mcp_timeout_is_classified_without_changing_exception_identity(
    metric_reader: InMemoryMetricReader,
) -> None:
    from src.mcp.client import MCPTimeoutError

    timeout = MCPTimeoutError("sensitive request detail")
    with pytest.raises(MCPTimeoutError) as raised, provider_call("other", "invoke"):
        raise timeout
    assert raised.value is timeout
    assert _points(metric_reader)["composer.provider.request"][0]["attributes"] == {
        "provider": "other",
        "operation": "invoke",
        "result": "timeout",
    }


def test_provider_call_maps_unknown_labels_and_ignores_metric_failure(
    metric_reader: InMemoryMetricReader, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "src.telemetry.providers._meter",
        lambda: (_ for _ in ()).throw(RuntimeError("meter unavailable")),
    )
    original = ValueError("private payload")
    with pytest.raises(ValueError) as raised, provider_call("customer-host.example", "tools/call"):
        raise original
    assert raised.value is original


def test_websocket_metrics_record_only_bounded_aggregate_values(
    metric_reader: InMemoryMetricReader,
) -> None:
    from src.telemetry.providers import (
        websocket_connection,
        websocket_replay,
        websocket_send_failed,
    )

    with websocket_connection(reconnected=True):
        websocket_replay(4)
        websocket_send_failed()
    points = _points(metric_reader)
    assert set(points) == {
        "composer.websocket.connection.duration",
        "composer.websocket.reconnect",
        "composer.websocket.replay.count",
        "composer.websocket.send.failure",
    }
    assert all(not point["attributes"] for samples in points.values() for point in samples)


def test_opt_in_runnable_wrapper_preserves_input_and_langsmith_config(
    metric_reader: InMemoryMetricReader, monkeypatch: pytest.MonkeyPatch
) -> None:
    from langchain_core.runnables import RunnableLambda

    from src.telemetry.providers import instrument_llm

    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    wrapped = instrument_llm(RunnableLambda[str, str](lambda value: value.upper()), "openai")
    config = {"metadata": {"langsmith_project": "private-project"}}
    assert wrapped.invoke("secret prompt", config=config) == "SECRET PROMPT"  # type: ignore[attr-defined]
    points = _points(metric_reader)
    assert points["composer.provider.request"][0]["attributes"] == {
        "provider": "openai",
        "operation": "invoke",
        "result": "success",
    }
    assert "secret prompt" not in repr(points)


def test_opt_in_tool_wrapper_measures_only_its_actual_invoke(
    metric_reader: InMemoryMetricReader,
) -> None:
    from langchain_core.runnables import RunnableLambda

    from src.telemetry.providers import instrument_tool

    wrapped = instrument_tool(
        RunnableLambda[dict[str, str], dict[str, str]](lambda value: value), "tavily"
    )
    assert wrapped.invoke({"query": "private"}) == {"query": "private"}  # type: ignore[attr-defined]
    points = _points(metric_reader)
    assert points["composer.provider.request"][0]["attributes"] == {
        "provider": "tavily",
        "operation": "tool",
        "result": "success",
    }
