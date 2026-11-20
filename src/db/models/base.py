"""Declarative base and column helpers shared by every model.

Models map the tables Prisma created. Names, types, nullability, defaults
and index names must match the database exactly; the schema-parity test
(tests/integration/db/test_schema_parity.py) enforces this. When it fails,
change the model, never the database.

JSONB and ARRAY values are replaced, never mutated in place: assign a new
object (SQLAlchemy does not track in-place changes such as
``row.tags.append(...)``).
"""

from datetime import datetime

from sqlalchemy import Boolean, MetaData, Text, text
from sqlalchemy.orm import DeclarativeBase, MappedColumn, mapped_column

from src.db.ids import new_id
from src.db.types import UTCDateTime, utcnow


class Base(DeclarativeBase):
    metadata = MetaData()


def id_column() -> MappedColumn[str]:
    """Text primary key. Prisma rows keep their cuid ids; new rows get UUIDv7."""
    return mapped_column(Text, primary_key=True, default=new_id)


def created_at_column() -> MappedColumn[datetime]:
    """``NOT NULL DEFAULT CURRENT_TIMESTAMP``; Python sets it too, so the value is known after insert.

    ORM inserts take the time from the application clock; rows written by
    Prisma or plain SQL get the database's ``CURRENT_TIMESTAMP``.
    """
    return mapped_column(
        UTCDateTime(), nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP")
    )


def updated_at_column() -> MappedColumn[datetime]:
    """No database default (Prisma set it in code); set on insert and on every update."""
    return mapped_column(UTCDateTime(), nullable=False, default=utcnow, onupdate=utcnow)


def bool_column(default: bool) -> MappedColumn[bool]:
    """``boolean NOT NULL DEFAULT true|false``."""
    return mapped_column(
        Boolean,
        nullable=False,
        default=default,
        server_default=text("true" if default else "false"),
    )
