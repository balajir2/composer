# Phase 5a — User-approval + interrupt/resume

**Target exit date:** 2026-04-22
**Author:** Composer team
**Depends on:** Phase 1 (PrismaCheckpointSaver), Phase 4b (conditional edges via `add_conditional_edges` + `WorkflowEdge.branch`), Phase 7a (authenticated user context).
**Split parent:** Phase 5 decomposed into 5a (user-approval) + 5b (SSE streaming).

---

## 1. Goal

Ship a `user-approval` node that pauses workflow execution until a human submits a decision. The paused execution is marked `waiting_approval`; a new `POST /executions/{id}/resume` endpoint accepts `{decision, note}` and restarts the graph via a new background task. Conditional edges from the node route to `"approved"` or `"rejected"` branches, consuming Phase 4b's `WorkflowEdge.branch` machinery.

## 2. Non-goals for this phase

- Streaming (`GET /executions/{id}/events`). Phase 5b.
- Deadline-based auto-reject on timeout. Future.
- Multi-approver / quorum approvals. Future.
- Role-based authorization on who may submit `/resume`. Phase 7b/8; 5a accepts any authenticated user.
- Email / Slack / push notifications on waiting_approval. Future.
- Approval UI (list of my pending approvals, etc.). Phase 10.
- Rescinding an approval after it's been submitted. Immutable in 5a; re-running the workflow creates a new execution.

## 3. Success criteria

Phase 5a exits when:

1. All unit tests pass: `ruff`, `pyright --strict`, `pytest -m "not integration"` green on `main`.
2. Two integration suites pass against real Neon:
    - **`test_user_approval_approved`** — `Start → UserApproval → End`; client registers a workflow, starts execution, polls until `waiting_approval`, POSTs `{decision: "approved"}`, polls until `completed`.
    - **`test_user_approval_rejected`** — same shape but the rejected branch routes to a `set-state(result="rejected")` node; client POSTs `{decision: "rejected"}` and observes the rejected branch's output in the final state.
3. One OAB regression port: `test_oab_user_approval_pause_and_resume.py`.
4. `grep -rn 'NotImplementedError' src/ | grep -i "user-approval"` returns no hits (Phase 4b's remaining sentinel is resolved).
5. CHANGELOG, CLAUDE.md phase table, ADR-0016 `Implemented by` all backfilled.

## 4. Architecture in one diagram

```
  POST /workflows  (workflow w/ user-approval node)     ──> Prisma Workflow row
  POST /executions (workflowId)                          ──> Prisma WorkflowExecution row + BackgroundTask
                                                              │
                                                              ▼
      LangGraphExecutor.run(execution_id)          astream() drives the compiled graph
           │                                              │
           │  ... normal nodes ...                        │
           │                                              ▼
           │                          UserApprovalExecutor.arun(state)
           │                              │
           │                              └── calls LangGraph.interrupt(prompt)
           │                                      (persists state via PrismaCheckpointSaver,
           │                                       raises GraphInterrupt exception)
           │
           ▼ exception caught in LangGraphExecutor.run
           WorkflowExecution.status = "waiting_approval"
           variables["_pending_approval_node"] = <node_id>
           variables["_pending_approval_prompt"] = <prompt>
           BackgroundTask exits cleanly


  POST /executions/{id}/resume  {decision, note}
        │
        ▼
  Approval row written
  WorkflowExecution.status = "running"
        │
        ▼
  LangGraphExecutor.resume(execution_id, decision) via BackgroundTask
        │
        ▼
  astream(Command(resume=decision), config=...) — LangGraph picks up
  from checkpoint, UserApprovalExecutor sees resume value, returns
  state delta, execution continues into the matched branch edge
        │
        ▼
  Normal completion path: status = "completed" (via End node)
```

## 5. Prisma schema — `Approval` table

```prisma
enum ApprovalDecision {
  approved
  rejected
}

model Approval {
  id               String            @id @default(cuid())
  executionId      String            @map("execution_id")
  nodeId           String            @map("node_id")
  approverUserId   String            @map("approver_user_id")
  decision         ApprovalDecision
  note             String?
  createdAt        DateTime          @default(now()) @map("created_at")

  execution        WorkflowExecution @relation(fields: [executionId], references: [id], onDelete: Cascade)

  @@map("approvals")
  @@index([executionId])
  @@index([approverUserId])
}
```

`WorkflowExecution` gains a back-relation: `approvals Approval[]`. One execution can have many approvals (one per user-approval node reached).

## 6. `UserApprovalExecutor`

Phase 1 declared `UserApprovalNodeData`:
```python
class UserApprovalNodeData(BaseNodeData):
    prompt: str | None = None
    options: list[str] = []          # 5a ignores — always approved/rejected
    deadline_seconds: int | None = None  # 5a ignores — future
```

Phase 5a uses only `prompt`. The executor:

```python
# src/executors/user_approval.py (new)

from typing import Any

from langgraph.types import interrupt  # LangGraph v0.4+ primitive

from src.engine.state import WorkflowStateDict
from src.engine.workflow import UserApprovalNode
from src.executors.base import register_executor
from src.variable_substitution import substitute


class UserApprovalNodeError(RuntimeError):
    """Raised when resume_value arrives but is neither 'approved' nor 'rejected'."""


@register_executor("user-approval")
class UserApprovalExecutor:
    def __init__(self, node: UserApprovalNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        prompt_template = self.node.data.prompt or "Please approve"
        prompt = substitute(prompt_template, state)

        # LangGraph's interrupt raises GraphInterrupt on first pass
        # (LangGraphExecutor.run catches + persists state).
        # On resume: interrupt() returns the value passed to Command(resume=...).
        decision = interrupt(
            {"node_id": self.node.id, "prompt": prompt}
        )

        # Decision is a string: "approved" or "rejected"
        if decision not in {"approved", "rejected"}:
            raise UserApprovalNodeError(
                f"user-approval node {self.node.id!r} received invalid decision "
                f"{decision!r}; expected 'approved' or 'rejected'"
            )

        # Record the taken branch so graph_builder's router can read it
        return {
            "variables": {f"_approval_{self.node.id}": decision},
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "input": {"prompt": prompt},
                    "output": {"decision": decision},
                }
            },
        }
```

The executor's state delta writes `_approval_<node_id>` into `variables` so the router (built by `graph_builder`) can read it without re-consulting the Approval table.

## 7. `graph_builder` — user-approval as conditional source

Phase 4b already made `if-else` and `while` conditional sources via `_branch_mapping` + `add_conditional_edges`. Phase 5a extends the pattern:

- `CONDITIONAL_SOURCE_TYPES` already includes `"user-approval"`.
- `_branch_mapping` gets called with `required_branches = {"approved", "rejected"}`.
- New router: `_route_user_approval(node)` reads `state["variables"][f"_approval_{node.id}"]` and returns `"approved"` or `"rejected"`. On missing key (shouldn't happen), defaults to `"rejected"` for safety.

The NotImplementedError at `user-approval` in Phase 4b's fallback path is removed.

```python
def _route_user_approval(node: UserApprovalNode) -> Callable[[WorkflowStateDict], str]:
    def _router(state: WorkflowStateDict) -> str:
        variables = state.get("variables") or {}
        decision = variables.get(f"_approval_{node.id}")
        return "approved" if decision == "approved" else "rejected"
    return _router
```

In the conditional-edges pass of `build_graph`:

```python
    for node in workflow.nodes:
        if node.type == "if-else":
            ...
        elif node.type == "while":
            ...
        elif node.type == "user-approval":
            assert isinstance(node, UserApprovalNode)
            mapping = _branch_mapping(node, list(workflow.edges), {"approved", "rejected"})
            builder.add_conditional_edges(
                node.id, _route_user_approval(node), mapping
            )
```

## 8. `LangGraphExecutor.run` — catch interrupt, persist, exit cleanly

Current Phase 1 shape: `run()` calls `compiled.ainvoke(...)`, catches exceptions, marks `completed` or `failed`.

Phase 5a extends: catch `GraphInterrupt` distinctly. Do NOT mark failed — mark `waiting_approval` and persist the pending node info.

```python
# src/engine/langgraph_executor.py (modified)

from langgraph.errors import GraphInterrupt

async def run(self, execution_id: str) -> None:
    execution = await self._load_execution(execution_id)
    if execution is None:
        return
    try:
        workflow = Workflow.model_validate({...})
        compiled = build_graph(workflow, self.checkpointer)
        state = initial_state(...)
        if execution.userId:
            state["user_id"] = execution.userId

        set_current_langsmith(...)
        set_current_db(self.db)

        config: dict[str, Any] = {"configurable": {"thread_id": execution.threadId}}
        try:
            final_state = await compiled.ainvoke(state, config=config)
        except GraphInterrupt as interrupt_exc:
            await self._mark_waiting_approval(
                execution_id, interrupt_exc, config
            )
            return

        # Normal completion
        await self._mark_completed(execution_id, final_state)
    except Exception as exc:
        await self._mark_failed(execution_id, exc)


async def _mark_waiting_approval(
    self, execution_id: str, interrupt_exc: GraphInterrupt, config: dict
) -> None:
    """Persist the pause state.  interrupt_exc.value holds the dict passed to
    `interrupt({...})` in the executor — {"node_id": ..., "prompt": ...}."""
    info = interrupt_exc.value if hasattr(interrupt_exc, "value") else {}
    if isinstance(info, list) and info:  # LangGraph sometimes wraps as list
        info = info[0]
    await self.db.workflowexecution.update(
        where={"id": execution_id},
        data={
            "status": "waiting_approval",
            "variables": Json({
                "_pending_approval_node": info.get("node_id"),
                "_pending_approval_prompt": info.get("prompt"),
            }),
        },
    )


async def resume(self, execution_id: str, decision: str) -> None:
    """Called by the /resume endpoint via BackgroundTasks.

    Feeds `decision` into the paused graph via LangGraph's Command(resume=...).
    """
    from langgraph.types import Command

    execution = await self._load_execution(execution_id)
    if execution is None:
        return

    try:
        workflow = Workflow.model_validate({...})
        compiled = build_graph(workflow, self.checkpointer)

        config: dict[str, Any] = {"configurable": {"thread_id": execution.threadId}}
        set_current_langsmith(...)
        set_current_db(self.db)

        try:
            final_state = await compiled.ainvoke(Command(resume=decision), config=config)
        except GraphInterrupt as interrupt_exc:
            # Another user-approval node hit — chain another pause
            await self._mark_waiting_approval(execution_id, interrupt_exc, config)
            return

        await self._mark_completed(execution_id, final_state)
    except Exception as exc:
        await self._mark_failed(execution_id, exc)
```

Helper methods `_mark_completed` / `_mark_failed` / `_mark_waiting_approval` factor out the Prisma updates. The existing inline DB update logic in Phase 1's `run()` migrates into these helpers.

## 9. REST endpoint — `POST /executions/{id}/resume`

```python
# src/api/executions.py — add handler

from enum import Enum

class ResumeDecision(str, Enum):
    approved = "approved"
    rejected = "rejected"

class ResumeRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    decision: ResumeDecision
    note: str | None = None

@router.post("/executions/{execution_id}/resume", response_model=ExecutionRead)
async def resume_execution(
    execution_id: str,
    payload: ResumeRequest,
    background: BackgroundTasks,
    user_id: str = Depends(get_current_user_id),
    db: Prisma = Depends(get_db),
) -> ExecutionRead:
    execution = await db.workflowexecution.find_unique(where={"id": execution_id})
    if execution is None:
        raise HTTPException(404, detail=f"execution {execution_id!r} not found")
    if execution.status != "waiting_approval":
        raise HTTPException(
            409,
            detail=f"execution is {execution.status!r}, not 'waiting_approval'",
        )

    pending_node_id = (execution.variables or {}).get("_pending_approval_node")
    if pending_node_id is None:
        raise HTTPException(
            500, detail="execution is waiting_approval but has no _pending_approval_node"
        )

    # Write audit row
    await db.approval.create(data={
        "executionId": execution_id,
        "nodeId": pending_node_id,
        "approverUserId": user_id,
        "decision": payload.decision.value,
        "note": payload.note,
    })

    # Flip status + kick off background resume
    await db.workflowexecution.update(
        where={"id": execution_id},
        data={"status": "running"},
    )

    # Spawn resume task
    checkpointer = get_checkpointer_from_request(...)  # or use app.state
    orchestrator = LangGraphExecutor(db, checkpointer)
    background.add_task(orchestrator.resume, execution_id, payload.decision.value)

    # Re-fetch to return the updated row
    updated = await db.workflowexecution.find_unique(where={"id": execution_id})
    return _to_execution_read(updated)
```

The `ExecutionRead` Pydantic model already exists in `src/api/executions.py` (Phase 1); we reuse it.

## 10. Status transitions

```
        running ──(normal flow)──► completed
           │
           │ interrupt()
           ▼
       waiting_approval
           │
           │ /resume {approved}
           ▼
        running ──(approved branch)──► completed
           │
           │ /resume {rejected}
           ▼
        running ──(rejected branch)──► completed
           │
           │ exception in any resume step
           ▼
          failed
```

`rejected` is NOT a top-level status in 5a — the execution completes normally through the rejected-branch edge. Observers inspect the `Approval` table to see decisions. Phase 5b's SSE stream will emit a `rejected` event for UI convenience.

## 11. Error model

- **`UserApprovalNodeError`** — raised inside the executor if `interrupt()` returns a non-`{approved, rejected}` value. Indicates an API-level bug (caller passed wrong decision somehow).
- **HTTP 404** — `/resume` on unknown execution_id.
- **HTTP 409** — `/resume` on an execution whose status isn't `waiting_approval`.
- **HTTP 500** — invariant violation (execution marked `waiting_approval` but no `_pending_approval_node` in variables).

Pydantic `ResumeDecision` enum rejects any string other than `"approved"` or `"rejected"` with 422 before our handler runs.

## 12. Test plan

### 12.1 Unit

- `test_user_approval_executor.py` — ~5 tests
  - Happy path: executor runs, calls `interrupt()`, verifies state delta on resume-approved
  - Resume-rejected → delta records `_approval_<id>` = "rejected"
  - Invalid resume value → `UserApprovalNodeError`
  - Prompt variable substitution applies
  - Executor registered under `"user-approval"` in the registry

- `test_graph_builder.py` extension — 3 tests
  - user-approval with both approved + rejected branches compiles
  - Missing `approved` branch on user-approval source → `WorkflowValidationError`
  - Wrong branch name (e.g., `"ok"` instead of `"approved"`) → `WorkflowValidationError`

- `test_langgraph_executor.py` extension — 3 tests
  - `GraphInterrupt` caught → status `waiting_approval` + `_pending_approval_*` in variables
  - `resume(execution_id, "approved")` reaches completion
  - `resume(execution_id, "rejected")` reaches completion (rejected branch traversed)

- `test_api_executions_resume.py` — ~7 tests
  - Happy path (resume approved → 200)
  - Execution not found → 404
  - Execution status != waiting_approval → 409
  - Missing `_pending_approval_node` in variables → 500
  - Invalid decision value → 422 (Pydantic)
  - Approval row written with correct approver user id
  - BackgroundTask scheduled (not called synchronously)

Total ~18 new unit tests. Target: ~382 tests non-integration after 5a (364 + 18).

### 12.2 Integration (real Neon)

- `test_user_approval_approved.py` — Start → UserApproval → End (approved path).
  Client: register workflow → start execution → poll until `waiting_approval` → `POST /resume {approved}` → poll until `completed`. Assert `Approval` table has 1 row with `decision=approved`.
- `test_user_approval_rejected.py` — Start → UserApproval → [approved → End-A | rejected → End-B] with distinct end node IDs. POST rejected; assert execution completes + the rejected-branch End was reached + approval row has `decision=rejected`.

### 12.3 Regression

- `test_oab_user_approval_pause_and_resume.py` — port of OAB's user-approval spec (find via grep in OAB source). Same pause→resume flow, asserts the behavioural contract: status transitions correctly, approval record exists, rejected branch fires when decision=rejected.

## 13. Phase-exit checklist

- [ ] All unit tests green: `ruff`, `pyright --strict`, `pytest -m "not integration"`.
- [ ] Both integration tests pass against real Neon.
- [ ] OAB regression port passes.
- [ ] `grep -rn "NotImplementedError" src/` shows no `user-approval` hits. Phase 4b's sentinel resolved.
- [ ] `CONDITIONAL_SOURCE_TYPES` in `graph_builder.py` remains unchanged but `user-approval` branch is now implemented.
- [ ] CHANGELOG Phase 5a section + Phase 5a ✅ note.
- [ ] CLAUDE.md phase table: 5a ✅ Complete, 5b ⏭ Next, Phase 6/8/9/10 reordered.
- [ ] ADR-0016 `Implemented by` backfilled.

## 14. Risks and mitigations

**Risk 1.** `GraphInterrupt`'s `.value` attribute shape varies across LangGraph versions. **Mitigation:** defensive `if isinstance(info, list): info = info[0]` + `info.get(...)` with None fallbacks. Pin LangGraph v0.4.x in `pyproject.toml` if not already pinned.

**Risk 2.** BackgroundTask started from `/resume` races with `/executions/{id}` polls showing stale status. **Mitigation:** the endpoint flips status to `running` BEFORE scheduling the BackgroundTask; polling sees consistent state. The resume task updates to `completed` / `failed` / `waiting_approval` only after work finishes.

**Risk 3.** Checkpoint from an interrupted run contains stale user_id / langsmith config. **Mitigation:** `set_current_db` + `set_current_langsmith` fire at the start of `resume()` too — Phase 7a's ContextVars are per-async-task.

**Risk 4.** A /resume request arrives while a prior resume background task is still running (double-submit). **Mitigation:** the status check (`!=waiting_approval` → 409) blocks it. If the first resume's background task crashes after flipping status to `running` but before completion, the execution is stuck in `running` forever. Phase 7b can add a timeout / recovery sweep; 5a accepts this edge case.

**Risk 5.** The `_approval_<node_id>` variable collides with a user-named variable. **Mitigation:** underscore prefix on the reserved key + documented in `user-approval`'s module docstring. Workflows that read `variables._approval_<id>` are reading internal state — their risk.

## 15. New ADR

### ADR-0016: User-approval uses LangGraph `interrupt()` + background-task resume

**Status.** Accepted.
**Date.** 2026-04-21.

**Context.** User-approval nodes pause workflow execution until a human decides approved/rejected. Three mechanisms considered:

1. **Blocking request — /start waits.** The `POST /executions` request holds the HTTP connection open until the workflow completes (or the user submits approval via a side-channel). Simple for clients but pins an API worker to every waiting execution indefinitely.
2. **Poll + re-run.** On pause, return early. Client later submits decision via `/resume`; server re-runs the workflow from the start, replaying all prior state. Wasteful + error-prone for workflows with side effects (HTTP, LLM, MCP tool calls).
3. **Checkpoint + resume.** LangGraph's `interrupt()` primitive pauses and persists state via a `BaseCheckpointSaver`. Resume loads the checkpoint and continues from the paused node with a user-supplied resume value.

**Decision.** Option 3. `UserApprovalExecutor` calls `interrupt({node_id, prompt})`. LangGraph raises `GraphInterrupt`; `LangGraphExecutor.run` catches it, marks `waiting_approval`, and exits cleanly. `POST /executions/{id}/resume` writes an `Approval` row + kicks off `LangGraphExecutor.resume(execution_id, decision)` via FastAPI's `BackgroundTasks`. The resume task calls `compiled.ainvoke(Command(resume=decision), config={"thread_id": ...})`; LangGraph loads the checkpoint and continues.

**Alternatives considered.** See above. Option 1 was rejected on operational grounds (API worker exhaustion). Option 2 was rejected for correctness + cost (side effects replay).

**Consequences.**

- Phase 1's `PrismaCheckpointSaver` is already the right abstraction — no checkpointer changes required. `interrupt()` fits into its existing `put/get_tuple` pattern.
- The background-task model means clients must poll `/executions/{id}` for completion. Phase 5b adds SSE streaming as a UX improvement but doesn't change the underlying model.
- The `Approval` table is the system of record for decisions — observable independent of the workflow's final state. Future audit queries ("who approved X") are simple DB reads.
- A resume on a completed execution is idempotent-rejected: the 409 check catches it.
- A resume while the previous resume task is still running hits the same 409 (status != `waiting_approval`).

**Implemented by.** Phase 5a (commits TBD).

**Related.** ADR-0001 (PrismaCheckpointSaver), ADR-0013 (WorkflowEdge.branch — user-approval is a conditional source with `approved`/`rejected` branches), ADR-0015 (dev-mode auth fallback — any authenticated user can resume in 5a; RBAC is Phase 7b+).

## 16. Execution handoff

After this spec is approved and committed, the plan lands at `docs/superpowers/plans/2026-04-21-phase-5a-user-approval-plan.md` and executes via `superpowers:subagent-driven-development`. ~10 tasks; one commit per task.
