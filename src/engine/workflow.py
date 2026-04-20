"""Pydantic models for Composer's workflow JSON.

Implements ADR-0002 (full OAB fidelity for the workflow envelope). This
module defines the Phase 1 models (Workflow, WorkflowEdge, Position,
BaseNodeData) plus the Start and End node discriminators. The remaining
16 node-type classes land in Task 4 and plug into the same discriminated
union.

See docs/superpowers/specs/2026-04-20-phase-1-execution-engine-design.md §5.
OAB reference: lib/workflow/types.ts.
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Position(BaseModel):
    x: float
    y: float


class BaseNodeData(BaseModel):
    """Fields shared by every node-type data class."""

    # extra="allow" preserves per-type fields not yet modeled (e.g. Agent.tools,
    # Mcp.mcpServers) so workflows authored against later-phase executors still
    # round-trip through Phase 1 CRUD without data loss.
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    label: str
    node_type: str | None = Field(default=None, alias="nodeType")
    node_name: str | None = Field(default=None, alias="nodeName")


# ─── start ───────────────────────────────────────────────────────────────


class StartInputVariable(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    type: str
    required: bool
    description: str
    default_value: Any | None = Field(default=None, alias="defaultValue")


class StartNodeData(BaseNodeData):
    input_variables: list[StartInputVariable] = Field(default_factory=list, alias="inputVariables")


class StartNode(BaseModel):
    id: str
    type: Literal["start"]
    position: Position
    data: StartNodeData


# ─── end ─────────────────────────────────────────────────────────────────


class EndNodeData(BaseNodeData):
    pass


class EndNode(BaseModel):
    id: str
    type: Literal["end"]
    position: Position
    data: EndNodeData


# ─── edges ───────────────────────────────────────────────────────────────


class WorkflowEdge(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    source: str
    target: str
    type: str | None = None
    label: str | None = None
    source_handle: str | None = Field(default=None, alias="sourceHandle")


# ─── discriminated union ─────────────────────────────────────────────────
# Task 4 adds the remaining 16 classes to this union. Keep the ordering
# stable — Pydantic validates in declaration order when the discriminator
# tag is absent (should never happen, but defensive).

WorkflowNode = Annotated[
    StartNode | EndNode,
    Field(discriminator="type"),
]


# ─── workflow ────────────────────────────────────────────────────────────


class Workflow(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str | None = None
    user_id: str | None = Field(default=None, alias="userId")
    name: str
    description: str | None = None
    category: str | None = None
    tags: list[str] = Field(default_factory=list)
    difficulty: str | None = None
    estimated_time: str | None = Field(default=None, alias="estimatedTime")
    nodes: list[WorkflowNode]
    edges: list[WorkflowEdge]
    version: str | None = None
    is_template: bool = Field(default=False, alias="isTemplate")
    is_public: bool = Field(default=False, alias="isPublic")
    created_at: str | None = Field(default=None, alias="createdAt")
    updated_at: str | None = Field(default=None, alias="updatedAt")


__all__ = [
    "BaseNodeData",
    "EndNode",
    "EndNodeData",
    "Position",
    "StartInputVariable",
    "StartNode",
    "StartNodeData",
    "Workflow",
    "WorkflowEdge",
    "WorkflowNode",
]
