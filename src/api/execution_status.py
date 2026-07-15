"""Shared "active execution status" vocabulary (P1-2 followup).

A `WorkflowExecution` row is "active" — not yet at a terminal status — while
it is `queued`, `running`, or `waiting_approval`. Three independent call
sites all need this exact set and MUST agree on it, or the class of bug this
module exists to close recurs:

  - `src/api/executions.py`'s `delete_execution`/`delete_executions_bulk`
    (P1-6): refuse to delete a row (and its LangGraph checkpoints) out from
    under a worker that might still be driving it.
  - `src/api/executions.py`'s `cancel_execution` (P1-6, extended
    P1-2-followup): only these statuses are cancellable; the atomic
    `update_many` guard is sourced from `ACTIVE_EXECUTION_STATUSES_SORTED`
    so the guard and the transition can never silently drift apart.
  - `src/api/internal.py`'s `claim_and_run` claim query: only these
    statuses are claimable by a Cloud-Tasks-pushed
    `POST /internal/claim-and-run` delivery. `queued` MUST be included —
    that's precisely the status a freshly-created, not-yet-claimed row has.

Previously each of the first two lived in `executions.py` as a single
source of truth for each other, but `claim_and_run`'s claim query hardcoded
its own independent `status IN ('queued', 'running', 'waiting_approval')`
SQL literal — a change to the active-status set anywhere would silently
desync the claim query from the delete/cancel guards. This module is the
one place the set is defined; every call site imports it rather than
re-deriving or re-hardcoding it.
"""

from __future__ import annotations

ACTIVE_EXECUTION_STATUSES: frozenset[str] = frozenset({"queued", "running", "waiting_approval"})

# Sorted, deterministic ordering for use in `{"in": [...]}` where-clauses
# (dict/list equality in tests + stable SQL param ordering) and as the bind
# parameter for `internal.py`'s `status = ANY($N::text[])` claim-query
# predicate — derived from the frozenset above so the two can never drift
# apart.
ACTIVE_EXECUTION_STATUSES_SORTED: list[str] = sorted(ACTIVE_EXECUTION_STATUSES)

__all__ = ["ACTIVE_EXECUTION_STATUSES", "ACTIVE_EXECUTION_STATUSES_SORTED"]
