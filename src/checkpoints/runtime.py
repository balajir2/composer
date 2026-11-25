"""The checkpointer for code that runs outside a request (the dev-mode in-process fallback).

The app lifespan registers its saver here, next to ``app.state.checkpointer``, the way
``src/db/runtime.py`` registers the sessionmaker. Routes read ``app.state``; out-of-request code
reads the registry.

Lifespans can overlap in one process (tests that start a second app, a restart inside one
process). The older lifespan's exit must not clear the newer one's registration, so
``unregister_checkpointer`` clears the registry only when the saver it is given is still the
registered one.
"""

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver

_checkpointer: BaseCheckpointSaver[Any] | None = None


def register_checkpointer(cp: BaseCheckpointSaver[Any] | None) -> None:
    global _checkpointer
    _checkpointer = cp


def unregister_checkpointer(cp: BaseCheckpointSaver[Any]) -> None:
    """Clear the registration only if ``cp`` is still the registered saver."""
    global _checkpointer
    if _checkpointer is cp:
        _checkpointer = None


def get_registered_checkpointer() -> BaseCheckpointSaver[Any] | None:
    return _checkpointer
