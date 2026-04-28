"""join-chunks node executor.

Concatenates an array of chunks from state.variables[variable] with
configurable separator/prefix/suffix, optionally appending metadata.

Chunk shapes supported:
  - str: used as-is
  - dict with 'content' key: use the content string
  - dict with 'text' key (e.g. vector-db results): use the text string
  - dict without either: JSON-serialize the whole dict (compact)
  - other: str(chunk)

Output lands on lastOutput (consistent with http/transform/extract).

See Phase 6a spec §5.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from src.executors.base import register_executor

if TYPE_CHECKING:
    from src.engine.state import WorkflowStateDict
    from src.engine.workflow import JoinChunksNode


class JoinChunksNodeError(RuntimeError):
    """Raised when the join-chunks executor can't resolve its input."""


@register_executor("join-chunks")
class JoinChunksExecutor:
    def __init__(self, node: JoinChunksNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        variables = state.get("variables") or {}
        variable_name = self.node.data.variable
        chunks_raw: Any = variables.get(variable_name)

        if chunks_raw is None:
            raise JoinChunksNodeError(
                f"join-chunks node {self.node.id!r}: variable {variable_name!r} not found in state"
            )
        if not isinstance(chunks_raw, list):
            raise JoinChunksNodeError(
                f"join-chunks node {self.node.id!r}: variable {variable_name!r} "
                f"is not a list; got {type(chunks_raw).__name__}"
            )

        rendered_items: list[str] = []
        for chunk in chunks_raw:
            content = self._render_content(chunk)
            rendered = f"{self.node.data.prefix}{content}{self.node.data.suffix}"
            if (
                self.node.data.include_metadata
                and isinstance(chunk, dict)
                and chunk.get("metadata")
            ):
                rendered += f"\n[metadata: {json.dumps(chunk['metadata'])}]"
            rendered_items.append(rendered)

        joined = self.node.data.separator.join(rendered_items)

        return {
            "variables": {"lastOutput": joined},
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "input": {
                        "variable": variable_name,
                        "chunk_count": len(chunks_raw),
                    },
                    "output": joined,
                }
            },
        }

    @staticmethod
    def _render_content(chunk: Any) -> str:
        if isinstance(chunk, str):
            return chunk
        if isinstance(chunk, dict):
            # Prefer `content`, fall back to `text` (the field vector-db
            # results emit) so a vector-db node's output can flow into
            # join-chunks without an intermediate reshape step.  Both
            # names are common in chunk representations across the
            # ecosystem (LangChain Documents use `page_content`,
            # OpenAI/Cohere embeddings use `text`).
            for key in ("content", "text", "page_content"):
                value = chunk.get(key)
                if isinstance(value, str):
                    return value
            return json.dumps(chunk)
        return str(chunk)


__all__ = ["JoinChunksExecutor", "JoinChunksNodeError"]
