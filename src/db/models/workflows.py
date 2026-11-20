"""Workflow definitions and their per-user assignments."""

from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.models.base import Base, bool_column, created_at_column, id_column, updated_at_column


class Workflow(Base):
    """A workflow definition: its graph (nodes, edges) and publishing flags."""

    __tablename__ = "workflows"
    __table_args__ = (
        Index("workflows_user_id_idx", "user_id"),
        Index("workflows_external_slug_idx", "external_slug"),
        Index("workflows_external_slug_key", "external_slug", unique=True),
    )

    id: Mapped[str] = id_column()
    user_id: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    difficulty: Mapped[str | None] = mapped_column(Text)
    estimated_time: Mapped[str | None] = mapped_column(Text)
    nodes: Mapped[Any] = mapped_column(JSONB(none_as_null=True), nullable=False)
    edges: Mapped[Any] = mapped_column(JSONB(none_as_null=True), nullable=False)
    version: Mapped[str | None] = mapped_column(Text)
    is_template: Mapped[bool] = bool_column(False)
    is_public: Mapped[bool] = bool_column(False)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()
    original_owner_email: Mapped[str | None] = mapped_column(Text)
    is_production: Mapped[bool] = bool_column(False)
    external_slug: Mapped[str | None] = mapped_column(Text)


class WorkflowAssignment(Base):
    """Grants a user access to a workflow, recording who assigned it and when."""

    __tablename__ = "workflow_assignments"
    __table_args__ = (
        Index("workflow_assignments_workflow_id_idx", "workflow_id"),
        Index("workflow_assignments_user_id_idx", "user_id"),
        Index(
            "workflow_assignments_workflow_id_user_id_key", "workflow_id", "user_id", unique=True
        ),
    )

    id: Mapped[str] = id_column()
    workflow_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey(
            "workflows.id",
            name="workflow_assignments_workflow_id_fkey",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
    )
    user_id: Mapped[str] = mapped_column(Text)
    assigned_by_id: Mapped[str] = mapped_column(Text)
    assigned_at: Mapped[datetime] = created_at_column()  # the row's creation time
