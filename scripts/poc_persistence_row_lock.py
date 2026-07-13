"""P3-1 research POC — see docs/decisions.md ADR-0031.

Validates whether Prisma Python's raw-SQL passthrough (query_raw /
execute_raw) plus its db.tx() transaction context manager can support a
durable-worker claim pattern: SELECT ... FOR UPDATE SKIP LOCKED, needed
by the (deferred, undecided) P1-2 durable-workers backlog item.

Creates two scratch "claimable" WorkflowExecution rows, then runs two
concurrent Prisma client connections that each try to claim one row
inside a transaction — simulating two durable-worker processes racing
for the same work queue. Cleans up its own rows afterward. Safe to
re-run against any dev database; writes and deletes only its own rows.

Usage: uv run python scripts/poc_persistence_row_lock.py
"""

import asyncio

from prisma import Json, Prisma  # pyright: ignore[reportAttributeAccessIssue]


async def main() -> None:
    setup_db = Prisma()
    await setup_db.connect()

    workflow = await setup_db.workflow.create(  # pyright: ignore[reportAttributeAccessIssue]
        data={
            "userId": "poc-user",
            "name": "poc-row-lock-test",
            "nodes": Json([]),
            "edges": Json([]),
        }
    )

    exec_ids: list[str] = []
    for i in range(2):
        row = await setup_db.workflowexecution.create(  # pyright: ignore[reportAttributeAccessIssue]
            data={
                "workflowId": workflow.id,
                "status": "claimable",
                "threadId": f"poc-thread-{i}-{workflow.id}",
                "nodeResults": Json({}),
                "variables": Json({}),
            }
        )
        exec_ids.append(row.id)

    print(f"Created 2 claimable rows: {exec_ids}")

    claimed: list[str | None] = [None, None]

    async def worker_claim(worker_idx: int, hold_seconds: float) -> None:
        worker_db = Prisma()
        await worker_db.connect()
        try:
            async with worker_db.tx(timeout=10000) as tx:
                rows = await tx.query_raw(  # pyright: ignore[reportUnknownMemberType]
                    """
                    SELECT id FROM workflow_executions
                    WHERE status = 'claimable'
                    ORDER BY started_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                    """
                )
                if not rows:
                    print(f"worker {worker_idx}: nothing to claim")
                    return
                claimed_id = rows[0]["id"]
                print(f"worker {worker_idx}: claimed {claimed_id}, holding lock...")
                await asyncio.sleep(hold_seconds)
                await tx.execute_raw(  # pyright: ignore[reportUnknownMemberType]
                    "UPDATE workflow_executions SET status = 'claimed_by_' || $1 WHERE id = $2",
                    str(worker_idx),
                    claimed_id,
                )
                claimed[worker_idx] = claimed_id
                print(f"worker {worker_idx}: committed claim on {claimed_id}")
        finally:
            await worker_db.disconnect()

    # Launch both workers at the same time; worker 0 holds its lock longer
    # so we can observe whether worker 1's SKIP LOCKED actually skips the
    # locked row instead of blocking on it.
    await asyncio.gather(
        worker_claim(0, hold_seconds=2.0),
        worker_claim(1, hold_seconds=0.1),
    )

    print(f"\nResult: worker 0 claimed {claimed[0]!r}, worker 1 claimed {claimed[1]!r}")
    if claimed[0] and claimed[1] and claimed[0] != claimed[1]:
        print("PASS: two concurrent workers claimed two DISTINCT rows, no double-claim.")
    else:
        print("FAIL: workers did not cleanly split the two rows.")

    final_rows = await setup_db.workflowexecution.find_many(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": {"in": exec_ids}}
    )
    for r in final_rows:
        print(f"  final status of {r.id}: {r.status}")

    # Cleanup
    await setup_db.workflowexecution.delete_many(where={"id": {"in": exec_ids}})  # pyright: ignore[reportAttributeAccessIssue]
    await setup_db.workflow.delete(where={"id": workflow.id})  # pyright: ignore[reportAttributeAccessIssue]
    await setup_db.disconnect()
    print("Cleaned up scratch rows.")


if __name__ == "__main__":
    asyncio.run(main())
