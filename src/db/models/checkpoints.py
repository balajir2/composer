"""The official LangGraph saver's tables (Alembic 0004; the library writes them). Mapped for schema
parity and deletes."""

from typing import Any

from sqlalchemy import Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import BYTEA, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.models.base import Base


class SaverCheckpointMigration(Base):
    """The official saver's migration ledger (Alembic 0004 records all of its migrations)."""

    __tablename__ = "checkpoint_migrations"

    v: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)


class SaverCheckpoint(Base):
    """A checkpoint written by the official saver (the library writes it; see Alembic 0004)."""

    __tablename__ = "checkpoints"
    __table_args__ = (Index("checkpoints_thread_id_idx", "thread_id"),)

    thread_id: Mapped[str] = mapped_column(Text, primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(
        Text, primary_key=True, default="", server_default=text("''::text")
    )
    checkpoint_id: Mapped[str] = mapped_column(Text, primary_key=True)
    parent_checkpoint_id: Mapped[str | None] = mapped_column(Text)
    type: Mapped[str | None] = mapped_column(Text)
    checkpoint: Mapped[Any] = mapped_column(JSONB(none_as_null=True), nullable=False)
    metadata_: Mapped[Any] = mapped_column(
        "metadata", JSONB(none_as_null=True), nullable=False, server_default=text("'{}'::jsonb")
    )


class SaverCheckpointBlob(Base):
    """A channel value blob written by the official saver, keyed by channel and version."""

    __tablename__ = "checkpoint_blobs"
    __table_args__ = (Index("checkpoint_blobs_thread_id_idx", "thread_id"),)

    thread_id: Mapped[str] = mapped_column(Text, primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(
        Text, primary_key=True, default="", server_default=text("''::text")
    )
    channel: Mapped[str] = mapped_column(Text, primary_key=True)
    version: Mapped[str] = mapped_column(Text, primary_key=True)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    blob: Mapped[bytes | None] = mapped_column(BYTEA)


class SaverCheckpointWrite(Base):
    """A pending write written by the official saver, keyed by task and index."""

    __tablename__ = "checkpoint_writes"
    __table_args__ = (Index("checkpoint_writes_thread_id_idx", "thread_id"),)

    thread_id: Mapped[str] = mapped_column(Text, primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(
        Text, primary_key=True, default="", server_default=text("''::text")
    )
    checkpoint_id: Mapped[str] = mapped_column(Text, primary_key=True)
    task_id: Mapped[str] = mapped_column(Text, primary_key=True)
    idx: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str | None] = mapped_column(Text)
    blob: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    task_path: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=text("''::text")
    )
