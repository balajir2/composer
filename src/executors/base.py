"""Executor protocol and registry.

Each node type maps to exactly one Executor class. The registry keeps that
mapping and produces phase-aware NotImplementedError messages for node
types whose executors haven't been built yet.
"""

from typing import Any, Protocol, runtime_checkable

from src.engine.state import WorkflowStateDict
from src.engine.workflow import WorkflowNode


@runtime_checkable
class Executor(Protocol):
    """Each node-type Executor constructs from its node and exposes arun()."""

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]: ...


_REGISTRY: dict[str, type[Any]] = {}


# Authoritative map of node type → owning phase number. Referenced in error
# messages so the caller learns *when* an executor will exist, not just that
# it doesn't. Keep in sync with docs/design/2026-04-20-composer-python-port-design.md §6.
_PHASE_FOR_TYPE: dict[str, int] = {
    # Phase 1
    "start": 1,
    "end": 1,
    # Phase 2
    "agent": 2,
    # Phase 3
    "mcp": 3,
    # Phase 4
    "http": 4,
    "transform": 4,
    "data-transform": 4,
    "extract": 4,
    "if-else": 4,
    "while": 4,
    "set-state": 4,
    # Phase 5
    "user-approval": 5,
    # Phase 6
    "guardrails": 6,
    "note": 6,
    "vector-db": 6,
    "gamma-ai": 6,
    "arcade": 6,
    "join-chunks": 6,
}


def register_executor(node_type: str):
    """Decorator that registers an Executor class by its node `type` string."""

    def _wrap(cls: type[Any]) -> type[Any]:
        _REGISTRY[node_type] = cls
        return cls

    return _wrap


def build_executor(node: WorkflowNode) -> Executor:
    """Instantiate the Executor for `node`, or raise a phase-hinted NotImplementedError."""
    cls = _REGISTRY.get(node.type)
    if cls is None:
        phase = _PHASE_FOR_TYPE.get(node.type)
        if phase is None:
            raise NotImplementedError(
                f"Executor for node type {node.type!r} is not registered and "
                f"has no known owning phase — lands in a later phase."
            )
        raise NotImplementedError(f"Executor for node type {node.type!r} lands in Phase {phase}.")
    return cls(node)


__all__ = [
    "Executor",
    "build_executor",
    "register_executor",
]
