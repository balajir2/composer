"""Transaction-scoped Postgres advisory locks.

Transaction-scoped (``pg_advisory_xact_lock``), never session-scoped: poolers in transaction
mode (Neon's) do not support session-level advisory locks. The lock is released when the
transaction ends, by commit or rollback.
"""

from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

LOCK_NOT_AVAILABLE_SQLSTATE = "55P03"
DEFAULT_LOCK_TIMEOUT = "15s"


class LockTimeoutError(RuntimeError):
    """The advisory lock was not granted within the lock timeout."""


async def acquire_xact_lock(
    session: AsyncSession, key: str, *, timeout: str = DEFAULT_LOCK_TIMEOUT
) -> None:
    """Take the advisory lock named ``key`` for the rest of the session's transaction.

    ``timeout`` (a Postgres interval string) bounds the wait and also applies to the other
    lock waits of the same transaction. Raises ``LockTimeoutError`` when it expires; the
    transaction is then aborted, so the caller must let the session roll back.
    """
    try:
        await session.execute(text(f"SET LOCAL lock_timeout = '{timeout}'"))
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key}
        )
    except OperationalError as exc:
        if getattr(exc.orig, "sqlstate", None) == LOCK_NOT_AVAILABLE_SQLSTATE:
            raise LockTimeoutError(f"timed out after {timeout} waiting for lock {key!r}") from exc
        raise


__all__ = ["LockTimeoutError", "acquire_xact_lock"]
