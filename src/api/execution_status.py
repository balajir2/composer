"""Shared active-status vocabulary for cancellation and deletion guards.

Queued, running, and approval-paused rows retain live workflow state and
cannot be deleted. Worker claim eligibility is narrower and depends on the
delivery kind; an active status alone never authorizes replay.
"""

from __future__ import annotations

ACTIVE_EXECUTION_STATUSES: frozenset[str] = frozenset({"queued", "running", "waiting_approval"})

# Deterministic ordering for cancellation/deletion `{"in": [...]}` predicates.
ACTIVE_EXECUTION_STATUSES_SORTED: list[str] = sorted(ACTIVE_EXECUTION_STATUSES)

__all__ = ["ACTIVE_EXECUTION_STATUSES", "ACTIVE_EXECUTION_STATUSES_SORTED"]
