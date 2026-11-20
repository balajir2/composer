"""LangGraph checkpoint tables (retired in a later migration phase; mapped for schema parity)."""

from datetime import datetime

from sqlalchemy import Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import BYTEA
from sqlalchemy.orm import Mapped, mapped_column

from src.db.models.base import Base, created_at_column


class LangGraphCheckpoint(Base):
    """A serialized LangGraph checkpoint for a thread and namespace."""

    __tablename__ = "langgraph_checkpoints"
    __table_args__ = (Index("langgraph_checkpoints_thread_id_idx", "thread_id"),)

    thread_id: Mapped[str] = mapped_column(Text, primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(
        Text, primary_key=True, default="", server_default=text("''::text")
    )
    checkpoint_id: Mapped[str] = mapped_column(Text, primary_key=True)
    parent_checkpoint_id: Mapped[str | None] = mapped_column(Text)
    checkpoint: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    metadata_: Mapped[bytes] = mapped_column("metadata", BYTEA, nullable=False)
    created_at: Mapped[datetime] = created_at_column()


class LangGraphCheckpointWrite(Base):
    """A pending write attached to a LangGraph checkpoint, keyed by task and index."""

    __tablename__ = "langgraph_checkpoint_writes"
    __table_args__ = (
        Index(
            "langgraph_checkpoint_writes_thread_id_checkpoint_id_idx", "thread_id", "checkpoint_id"
        ),
    )

    thread_id: Mapped[str] = mapped_column(Text, primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(
        Text, primary_key=True, default="", server_default=text("''::text")
    )
    checkpoint_id: Mapped[str] = mapped_column(Text, primary_key=True)
    task_id: Mapped[str] = mapped_column(Text, primary_key=True)
    idx: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    channel: Mapped[str] = mapped_column(Text)
    value: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    created_at: Mapped[datetime] = created_at_column()
