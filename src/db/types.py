"""Column types shared by Composer's SQLAlchemy models."""

from datetime import UTC, datetime, timedelta

from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    """Current time as a timezone-aware UTC datetime, in whole milliseconds.

    Columns store milliseconds, so truncating here makes in-memory values
    equal the stored ones.
    """
    now = datetime.now(UTC)
    return now.replace(microsecond=now.microsecond // 1000 * 1000)


class UTCDateTime(TypeDecorator[datetime]):
    """A ``timestamp(3) without time zone`` column holding UTC wall-clock time.

    Prisma created every timestamp column this way and wrote UTC values, so
    this type keeps rows written by either ORM comparable. Python code only
    ever sees timezone-aware UTC datetimes. A naive datetime is rejected
    instead of being guessed at, because silently misreading one shifts it by
    the server's offset.
    """

    impl = TIMESTAMP
    cache_ok = True

    def __init__(self) -> None:
        super().__init__(precision=3, timezone=False)

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("UTCDateTime requires a timezone-aware datetime")
        value = value.astimezone(UTC).replace(tzinfo=None)
        us = value.microsecond
        rounded = (us + 500) // 1000 * 1000
        return value + timedelta(microseconds=rounded - us)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC)
