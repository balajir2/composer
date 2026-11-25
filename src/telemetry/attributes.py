"""Bounded attribute policy from the telemetry signal catalog."""

from collections.abc import Mapping
from typing import TypeAlias

from opentelemetry.util.types import AttributeValue

SignalKind: TypeAlias = str
VOCABULARIES = {
    "trigger_kind": frozenset({"resume", "run", "other"}),
    "environment": frozenset({"development", "production", "test", "other"}),
    "status": frozenset(
        {"canceled", "completed", "other", "running", "waiting_approval", "queued", "failed"}
    ),
    "failure_code": frozenset(
        {
            "other",
            "execution_timeout",
            "none",
            "orphaned_running",
            "execution_error",
            "lease_expired",
            "checkpoint_missing",
        }
    ),
    "result": frozenset(
        {
            "other",
            "failure",
            "errored",
            "scanned",
            "deleted",
            "success",
            "marked_failed",
            "timeout",
            "rate_limited",
            "authentication_error",
            "provider_error",
            "cancellation",
        }
    ),
    "node_type": frozenset(
        {
            "arcade",
            "if-else",
            "confluence",
            "guardrails",
            "mcp",
            "file-write",
            "download-pdf",
            "note",
            "join-chunks",
            "data-transform",
            "decision",
            "vector-db",
            "extract",
            "user-approval",
            "gamma-ai",
            "transform",
            "end",
            "jira",
            "start",
            "http",
            "while",
            "file-trigger",
            "other",
            "set-state",
            "agent",
            "email",
            "join",
        }
    ),
    "provider": frozenset(
        {
            "openai",
            "deepseek",
            "browserless",
            "groq",
            "resend",
            "google",
            "serper",
            "gamma",
            "typesafe",
            "other",
            "firecrawl",
            "anthropic",
            "tavily",
            "langsmith",
            "qwen",
        }
    ),
    "operation": frozenset({"invoke", "tool", "other"}),
    "decision": frozenset({"approve", "reject", "other"}),
    "expired": frozenset({"true", "false"}),
    "eligibility_reason": frozenset({"eligible", "other"}),
    "model_family": frozenset({"other"}),
    "token_type": frozenset({"output", "cached", "input", "other"}),
    "cost_source": frozenset({"other", "estimated", "provider"}),
    "component": frozenset(
        {"other", "events_deleted", "leases_expired", "orphaned_running", "approvals_expired"}
    ),
}
SIGNALS = {
    "composer.execution.started": ("trigger_kind", "environment"),
    "composer.execution.terminal": ("status", "failure_code", "trigger_kind"),
    "composer.execution.attempt.duration": ("result", "failure_code"),
    "composer.execution.queue_wait": ("trigger_kind", "result"),
    "composer.execution.node.duration": ("node_type", "result"),
    "composer.execution.approval_wait": ("decision", "expired"),
    "composer.execution.lease_expired": ("failure_code",),
    "composer.execution.orphaned": ("failure_code",),
    "composer.execution.recovery": ("result", "eligibility_reason"),
    "composer.provider.request.duration": ("provider", "operation", "result"),
    "composer.provider.request": ("provider", "operation", "result"),
    "composer.websocket.connection.duration": (),
    "composer.websocket.reconnect": (),
    "composer.websocket.replay.count": (),
    "composer.websocket.send.failure": (),
    "composer.llm.tokens": ("provider", "model_family", "token_type"),
    "composer.llm.cost": ("provider", "model_family", "cost_source"),
    "composer.maintenance.sweep": ("component", "result"),
}


def safe_attributes(kind: SignalKind, values: Mapping[str, object]) -> dict[str, AttributeValue]:
    """Drop unknown attributes and map unrecognized values to bounded categories."""
    result: dict[str, AttributeValue] = {}
    for key in SIGNALS.get(kind, ()):
        if key not in values and key != "failure_code":
            continue
        value = values.get(key)
        if key == "failure_code" and value is None:
            value = "none"
        if isinstance(value, bool):
            value = str(value).lower()
        if not isinstance(value, str) or value not in VOCABULARIES[key]:
            if "other" not in VOCABULARIES[key]:
                continue
            value = "other"
        result[key] = value
    return result
