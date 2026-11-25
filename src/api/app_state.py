"""Request dependencies for the objects the app lifespan attaches to ``app.state``."""

from typing import Any

from fastapi import Request
from langgraph.checkpoint.base import BaseCheckpointSaver

from src.engine.events_pg import PostgresEventStore


def get_checkpointer(request: Request) -> BaseCheckpointSaver[Any]:
    """The app-wide LangGraph checkpointer."""
    cp = getattr(request.app.state, "checkpointer", None)
    if cp is None:
        raise RuntimeError("Checkpointer not attached to app.state — did lifespan run?")
    return cp


def get_event_bus(request: Request) -> PostgresEventStore:
    """The app-wide event store."""
    bus = getattr(request.app.state, "event_bus", None)
    if bus is None:
        raise RuntimeError("PostgresEventStore not attached to app.state — did lifespan run?")
    return bus
