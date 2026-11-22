"""Transaction-scoped Postgres advisory locks, taken without ever waiting on a connection.

Transaction-scoped (``pg_advisory_xact_lock`` family), never session-scoped: poolers in
transaction mode (Neon's) do not support session-level advisory locks. The lock is released
when the transaction ends, by commit or rollback.

Callers that do not get the lock must not wait while holding a pooled connection (a burst of
waiters would starve the pool), so ``run_under_lock`` uses the non-blocking
``pg_try_advisory_xact_lock``: a caller that is refused rolls back and closes its session, sleeps
a jittered moment, optionally checks whether the winner already did the work, and tries again
until a deadline. Only the winner holds a connection.
"""

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

T = TypeVar("T")

# Jitter between attempts of a caller that was refused the lock (seconds). Module-level so
# tests can shrink them.
POLL_MIN_S = 0.1
POLL_MAX_S = 0.4


class LockTimeoutError(RuntimeError):
    """The advisory lock was not granted before the deadline."""


async def try_acquire_xact_lock(session: AsyncSession, key: str) -> bool:
    """Try to take the advisory lock named ``key`` for the rest of the session's transaction.

    Never blocks. True when granted (held until the transaction ends); False when another
    transaction holds it, in which case the caller should roll back and release its connection.
    """
    granted = await session.scalar(
        text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key}
    )
    return bool(granted)


async def run_under_lock(
    sessionmaker: async_sessionmaker[AsyncSession],
    key: str,
    work: Callable[[AsyncSession], Awaitable[T]],
    *,
    wait_s: float,
    check: Callable[[], Awaitable[T | None]] | None = None,
) -> T:
    """Run ``work(session)`` while holding the advisory lock ``key``; it must commit.

    ``work`` runs in the session that holds the lock, so everything it does (including a
    network call) keeps one connection busy: use it only where that is the point of the lock.
    A caller refused the lock holds NO connection while it waits: it sleeps, then ``check``
    (run in its own short session) may return the finished result, ending the wait; ``None``
    means keep trying. After ``wait_s`` seconds raises ``LockTimeoutError``.
    """
    deadline = time.monotonic() + wait_s
    while True:
        async with sessionmaker() as session:
            if await try_acquire_xact_lock(session, key):
                return await work(session)
            await session.rollback()  # refused: end the transaction, free the connection
        if time.monotonic() >= deadline:
            raise LockTimeoutError(f"lock {key!r} was not granted within {wait_s:g}s")
        await asyncio.sleep(random.uniform(POLL_MIN_S, POLL_MAX_S))
        if check is not None:
            done = await check()
            if done is not None:
                return done


__all__ = ["LockTimeoutError", "run_under_lock", "try_acquire_xact_lock"]
