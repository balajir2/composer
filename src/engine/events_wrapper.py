"""Executor wrapper that emits node_started / node_completed / node_failed.

Applied inside graph_builder.build_graph so every executor participates
automatically — no per-executor changes needed.

See Phase 5b spec §6.2 (event types updated to DES-007 in Phase 9a).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langgraph.errors import GraphBubbleUp

from src.engine.context import get_current_event_bus, get_current_execution_id
from src.engine.events import ExecutionEvent

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from src.engine.state import WorkflowStateDict
    from src.engine.workflow import WorkflowNode
    from src.executors.base import Executor


def _extract_output(node_id: str, result: dict[str, Any]) -> Any:
    """Pull the node's output from an executor's return value.

    Executors write `node_results[node.id]["output"]` for their visible
    product; many also write `variables.lastOutput` for downstream refs.
    Prefer the per-node record so each node's panel entry shows exactly
    what it produced, not whatever the preceding node left behind.
    """
    node_results = result.get("node_results") or {}
    node_rec = node_results.get(node_id) or {}
    if "output" in node_rec:
        return node_rec["output"]
    variables = result.get("variables") or {}
    return variables.get("lastOutput")


def _extract_input(node_id: str, result: dict[str, Any]) -> Any:
    """Pull the post-substitution input the executor saw.  Most executors
    write `node_results[node.id]["input"]` with the exact prompt / args /
    URL the node actually consumed.  Useful in the execution panel so
    designers can spot unresolved `{{var}}` placeholders immediately."""
    node_results = result.get("node_results") or {}
    node_rec = node_results.get(node_id) or {}
    return node_rec.get("input")


def _sanitize_node_id(node_id: str) -> str:
    """React Flow auto-generated IDs contain hyphens (e.g. "agent-1").

    The variable picker exposes them with underscores (`agent_1`) since
    dots are reserved as path separators in `{{a.b}}` substitution.
    Keep this mapping in lock-step with
    `frontend/components/composer/canvas/variable-reference-picker.tsx`.
    """
    return node_id.replace("-", "_")


def _sanitize_node_name(name: str) -> str | None:
    """Mirror the frontend PropertyPanel's sanitizer — snake_case the
    user-given name so `{{place_extractor.city}}` resolves in prompts.
    Returns None when the name is empty or sanitizes to nothing.
    """
    import re

    cleaned = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return cleaned or None


def _parse_output_for_substitution(output: Any) -> Any:
    """When an executor's output is a JSON string, parse it so downstream
    nodes can reference individual fields via `{{nodeVar.field}}`.

    LLMs often wrap JSON in a ```…``` code fence or add a short preamble,
    so we try three strategies: raw stripped text, the contents of a
    fenced block, and the largest `{…}` / `[…]` substring.  First parse
    that succeeds wins.  Non-JSON strings and non-string values pass
    through unchanged.
    """
    if not isinstance(output, str):
        return output

    import json
    import re

    stripped = output.strip()
    candidates: list[str] = [stripped]

    fence_match = re.search(r"```(?:json|JSON)?\s*\n(.*?)\n```", stripped, flags=re.DOTALL)
    if fence_match:
        candidates.append(fence_match.group(1).strip())

    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        first = stripped.find(open_ch)
        last = stripped.rfind(close_ch)
        if first != -1 and last > first:
            candidates.append(stripped[first : last + 1])

    for candidate in candidates:
        if not candidate or candidate[0] not in "{[":
            continue
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue

    return output


def wrap_executor_with_events(
    executor: Executor,
    node: WorkflowNode,
) -> Callable[[WorkflowStateDict], Awaitable[dict[str, Any]]]:
    """Return an `arun(state)` callable that emits events around the executor."""

    # nodeName uses the user-given label (from canvas panel) if present,
    # otherwise falls back to the node type so the UI never shows a raw
    # auto-generated id like "dropped-177…" as the only identifier.
    node_data: Any = getattr(node, "data", None)
    raw_node_name = getattr(node_data, "node_name", None)
    label = getattr(node_data, "label", None)
    user_label = raw_node_name or label
    node_info: dict[str, Any] = {
        "nodeId": node.id,
        "nodeName": user_label or node.type,
        "nodeType": node.type,
    }
    # User-assigned alias for prompt substitution:
    #   1st preference: snake_case of the Name field
    #   2nd preference: node.id with hyphens → underscores
    name_alias = _sanitize_node_name(raw_node_name) if isinstance(raw_node_name, str) else None
    id_alias = _sanitize_node_id(node.id)

    async def _arun(state: WorkflowStateDict) -> dict[str, Any]:
        execution_id = get_current_execution_id()
        bus = get_current_event_bus()

        if bus is not None and execution_id is not None:
            await bus.emit(
                ExecutionEvent(
                    type="node_started",
                    execution_id=execution_id,
                    payload=dict(node_info),
                )
            )

        try:
            result = await executor.arun(state)
        except GraphBubbleUp:
            # LangGraph's own control-flow signals (interrupt/resume via
            # user-approval, subgraph hand-off) — not a node failure. Must
            # propagate unmolested so LangGraph's runtime can handle the
            # pause; emitting node_failed here would misreport an
            # intentional approval-gate pause as a crash.
            raise
        except Exception as exc:
            if bus is not None and execution_id is not None:
                await bus.emit(
                    ExecutionEvent(
                        type="node_failed",
                        execution_id=execution_id,
                        payload={
                            **node_info,
                            "error": f"{type(exc).__name__}: {exc}",
                        },
                    )
                )
            raise

        output = _extract_output(node.id, result)
        node_input = _extract_input(node.id, result)

        # Mirror the output under every handle the user might type in a
        # prompt: the sanitized node ID (`agent_1`) AND the sanitized
        # user-given name alias (`place_extractor`) when set.  JSON
        # strings are parsed so per-field references (`{{name.city}}`)
        # work cleanly for JSON-mode agents.
        parsed = _parse_output_for_substitution(output)
        existing_vars: dict[str, Any] = {}
        if isinstance(result.get("variables"), dict):
            existing_vars = result["variables"]
        injected: dict[str, Any] = {id_alias: parsed}
        if name_alias and name_alias != id_alias:
            injected[name_alias] = parsed
        result = {
            **result,
            "variables": {**existing_vars, **injected},
        }

        if bus is not None and execution_id is not None:
            await bus.emit(
                ExecutionEvent(
                    type="node_completed",
                    execution_id=execution_id,
                    payload={
                        **node_info,
                        "input": node_input,
                        "output": output,
                    },
                )
            )

        return result

    return _arun


__all__ = ["wrap_executor_with_events"]
