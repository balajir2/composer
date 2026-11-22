"""Best-effort metrics for outbound providers and event delivery."""

import asyncio
import contextlib
import time
from collections.abc import Callable, Generator
from typing import Any, TypeVar

import httpx
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider

from src.telemetry.attributes import safe_attributes

_meter_provider: MeterProvider | None = None
_T = TypeVar("_T")
_LLM_PROVIDERS = frozenset({"anthropic", "openai", "google", "groq", "deepseek", "qwen"})
_STANDARD_TOOL_PROVIDER_BY_MODULE = {
    "src.tools.providers.browserless": "browserless",
    "src.tools.providers.firecrawl": "firecrawl",
    "src.tools.providers.jira": "other",
    "src.tools.providers.serper": "serper",
    "src.tools.providers.tavily": "tavily",
}


def configure_provider_metrics(provider: MeterProvider | None) -> None:
    """Bind app-owned metrics without changing OpenTelemetry's global provider."""
    global _meter_provider
    _meter_provider = provider


def _meter() -> Any:
    if _meter_provider is not None:
        return _meter_provider.get_meter("composer.telemetry.providers")
    return metrics.get_meter("composer.telemetry.providers")


def _record(action: Callable[[], None]) -> None:
    with contextlib.suppress(Exception):
        action()


def _outcome_for_exception(exc: BaseException) -> str:
    if isinstance(exc, asyncio.CancelledError):
        return "cancellation"
    if (
        isinstance(exc, (TimeoutError, httpx.TimeoutException))
        or type(exc).__name__ == "MCPTimeoutError"
    ):
        return "timeout"
    if isinstance(exc, httpx.HTTPStatusError):
        return _outcome_for_status(exc.response.status_code)
    if isinstance(exc, httpx.HTTPError):
        return "provider_error"
    if type(exc).__name__ in {"MCPHTTPError", "MCPRpcError"}:
        return "provider_error"
    return "other"


def _outcome_for_status(status_code: int) -> str:
    if 200 <= status_code < 300:
        return "success"
    if status_code in (408, 504):
        return "timeout"
    if status_code == 429:
        return "rate_limited"
    if status_code in (401, 403):
        return "authentication_error"
    if status_code >= 500:
        return "provider_error"
    return "other"


class ProviderCall:
    """Record one actual outbound request while preserving its outcome."""

    def __init__(self, provider: str, operation: str) -> None:
        safe = safe_attributes(
            "composer.provider.request.duration",
            {"provider": provider, "operation": operation, "result": "success"},
        )
        self._provider = str(safe.get("provider", "other"))
        self._operation = str(safe.get("operation", "other"))
        self._started = 0.0
        self._result = "success"

    def record_status_code(self, status_code: int) -> None:
        """Set a bounded outcome from an HTTP response status class."""
        self._result = _outcome_for_status(status_code)

    def __enter__(self) -> "ProviderCall":
        self._started = time.monotonic()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        _traceback: object,
    ) -> bool:
        if self._result != "success":
            result = self._result
        elif exc is not None:
            result = _outcome_for_exception(exc)
        else:
            result = self._result
        attributes = safe_attributes(
            "composer.provider.request",
            {"provider": self._provider, "operation": self._operation, "result": result},
        )
        elapsed = max(0.0, time.monotonic() - self._started)
        _record(
            lambda: (
                _meter()
                .create_counter("composer.provider.request", unit="{request}")
                .add(1, attributes)
            )
        )
        _record(
            lambda: (
                _meter()
                .create_histogram("composer.provider.request.duration", unit="s")
                .record(elapsed, attributes)
            )
        )
        return False

    async def __aenter__(self) -> "ProviderCall":
        return self.__enter__()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> bool:
        return self.__exit__(exc_type, exc, traceback)


def provider_call(provider: str, operation: str) -> ProviderCall:
    """Start one outbound call measurement with bounded provider dimensions."""
    return ProviderCall(provider, operation)


def provider_name_for_model(model_string: str) -> str:
    """Map a configured model string to a catalog provider without its model name."""
    provider = model_string.partition("/")[0] if "/" in model_string else "openai"
    return provider if provider in _LLM_PROVIDERS else "other"


def provider_name_for_tool(tool: object) -> str | None:
    """Return a mapped standard-tool provider; skip MCP and unknown tool classes."""
    tool_type = type(tool)
    if tool_type.__module__ == "src.mcp.base" and tool_type.__name__ == "_McpBoundTool":
        return None
    return _STANDARD_TOOL_PROVIDER_BY_MODULE.get(tool_type.__module__)


@contextlib.contextmanager
def websocket_connection(*, reconnected: bool) -> Generator[None, None, None]:
    """Measure an accepted WebSocket connection and aggregate reconnect count."""
    started = time.monotonic()
    if reconnected:
        _record(
            lambda: (
                _meter().create_counter("composer.websocket.reconnect", unit="{connection}").add(1)
            )
        )
    try:
        yield
    finally:
        elapsed = max(0.0, time.monotonic() - started)
        _record(
            lambda: (
                _meter()
                .create_histogram("composer.websocket.connection.duration", unit="s")
                .record(elapsed)
            )
        )


def websocket_replay(count: int) -> None:
    """Record the number of persisted events replayed on an accepted socket."""
    _record(
        lambda: (
            _meter()
            .create_histogram("composer.websocket.replay.count", unit="{event}")
            .record(max(0, count))
        )
    )


def websocket_send_failed() -> None:
    """Count a failed WebSocket send without recording its error or payload."""
    _record(
        lambda: _meter().create_counter("composer.websocket.send.failure", unit="{failure}").add(1)
    )


def instrument_runnable(runnable: _T, provider: str, operation: str) -> _T:
    """Return an opt-in LangChain wrapper for a later actual invocation seam.

    This helper is intentionally opt-in: model/tool construction sites must
    wrap the object before execution. It does not monkey-patch the object.
    """
    try:
        from langchain_core.runnables import RunnableLambda

        async def invoke_async(value: Any, config: Any = None) -> Any:
            async with provider_call(provider, operation):
                return await runnable.ainvoke(value, config=config)  # type: ignore[attr-defined]

        def invoke_sync(value: Any, config: Any = None) -> Any:
            with provider_call(provider, operation):
                return runnable.invoke(value, config=config)  # type: ignore[attr-defined]

        return RunnableLambda(func=invoke_sync, afunc=invoke_async)  # type: ignore[return-value]
    except Exception:
        return runnable


def instrument_llm(model: _T, provider: str) -> _T:
    """Opt-in actual invocation wrapper for a LangChain chat model."""
    return instrument_runnable(model, provider, "invoke")


def instrument_tool(tool: _T, provider: str = "other") -> _T:
    """Opt-in actual invocation wrapper for a LangChain tool."""
    return instrument_runnable(tool, provider, "tool")


__all__ = [
    "ProviderCall",
    "configure_provider_metrics",
    "instrument_llm",
    "instrument_runnable",
    "instrument_tool",
    "provider_call",
    "provider_name_for_model",
    "provider_name_for_tool",
    "websocket_connection",
    "websocket_replay",
    "websocket_send_failed",
]
