"""Tests for the join-chunks executor."""

from typing import Any

import pytest

from src.engine.state import initial_state
from src.engine.workflow import JoinChunksNode
from src.executors.join_chunks import JoinChunksExecutor, JoinChunksNodeError


def _node(**overrides: Any) -> JoinChunksNode:
    data: dict[str, Any] = {"label": "JC", "joinChunksVariable": "chunks"}
    data.update(overrides)
    return JoinChunksNode.model_validate(
        {
            "id": "jc",
            "type": "join-chunks",
            "position": {"x": 0, "y": 0},
            "data": data,
        }
    )


async def test_string_chunks_default_separator() -> None:
    state = initial_state()
    state["variables"]["chunks"] = ["alpha", "beta", "gamma"]
    delta = await JoinChunksExecutor(_node()).arun(state)
    assert delta["variables"]["lastOutput"] == "alpha\n\nbeta\n\ngamma"


async def test_string_chunks_custom_separator_prefix_suffix() -> None:
    state = initial_state()
    state["variables"]["chunks"] = ["a", "b"]
    node = _node(
        joinChunksSeparator="\n---\n",
        joinChunksPrefix="> ",
        joinChunksSuffix=" <",
    )
    delta = await JoinChunksExecutor(node).arun(state)
    assert delta["variables"]["lastOutput"] == "> a <\n---\n> b <"


async def test_dict_chunks_with_content_key() -> None:
    state = initial_state()
    state["variables"]["chunks"] = [
        {"content": "first", "metadata": {"id": 1}},
        {"content": "second", "metadata": {"id": 2}},
    ]
    delta = await JoinChunksExecutor(_node()).arun(state)
    assert delta["variables"]["lastOutput"] == "first\n\nsecond"


async def test_dict_chunks_without_content_json_serialized() -> None:
    state = initial_state()
    state["variables"]["chunks"] = [{"foo": "bar"}, {"x": 1}]
    delta = await JoinChunksExecutor(_node()).arun(state)
    joined = delta["variables"]["lastOutput"]
    assert '{"foo": "bar"}' in joined
    assert '{"x": 1}' in joined


async def test_include_metadata_appends_metadata_line() -> None:
    state = initial_state()
    state["variables"]["chunks"] = [
        {"content": "hello", "metadata": {"source": "a.txt"}},
    ]
    delta = await JoinChunksExecutor(_node(joinChunksIncludeMetadata=True)).arun(state)
    out = delta["variables"]["lastOutput"]
    assert "hello" in out
    assert '[metadata: {"source": "a.txt"}]' in out


async def test_include_metadata_false_omits_metadata() -> None:
    state = initial_state()
    state["variables"]["chunks"] = [
        {"content": "hello", "metadata": {"source": "a.txt"}},
    ]
    delta = await JoinChunksExecutor(_node(joinChunksIncludeMetadata=False)).arun(state)
    assert "metadata" not in delta["variables"]["lastOutput"]


async def test_mixed_string_and_dict_chunks() -> None:
    state = initial_state()
    state["variables"]["chunks"] = ["plain", {"content": "wrapped"}, {"no_content": True}]
    delta = await JoinChunksExecutor(_node()).arun(state)
    out = delta["variables"]["lastOutput"]
    parts = out.split("\n\n")
    assert parts[0] == "plain"
    assert parts[1] == "wrapped"
    assert '"no_content"' in parts[2]


async def test_empty_list_returns_empty_string() -> None:
    state = initial_state()
    state["variables"]["chunks"] = []
    delta = await JoinChunksExecutor(_node()).arun(state)
    assert delta["variables"]["lastOutput"] == ""


async def test_missing_variable_raises() -> None:
    state = initial_state()
    with pytest.raises(JoinChunksNodeError, match="chunks"):
        await JoinChunksExecutor(_node()).arun(state)


async def test_non_list_value_raises() -> None:
    state = initial_state()
    state["variables"]["chunks"] = "not a list"
    with pytest.raises(JoinChunksNodeError, match="not a list"):
        await JoinChunksExecutor(_node()).arun(state)


async def test_executor_is_registered() -> None:
    import src.executors.join_chunks  # noqa: F401  # pyright: ignore[reportUnusedImport]
    from src.executors.base import build_executor

    node = _node()
    executor = build_executor(node)
    assert isinstance(executor, JoinChunksExecutor)


async def test_node_result_shape() -> None:
    state = initial_state()
    state["variables"]["chunks"] = ["a", "b"]
    delta = await JoinChunksExecutor(_node()).arun(state)
    result = delta["node_results"]["jc"]
    assert result["status"] == "completed"
    assert result["input"]["variable"] == "chunks"
    assert result["input"]["chunk_count"] == 2
    assert result["output"] == "a\n\nb"


# Vector-DB compatibility: vector-db results carry chunks under `text`,
# not `content`.  Without the fallback, designers had to insert an
# extra data-transform between vector-db and join-chunks to rename
# the field — making the RAG pattern needlessly verbose.


async def test_dict_chunks_with_text_key() -> None:
    """vector-db result shape: each chunk has `text`, not `content`."""
    from src.engine.state import initial_state
    from src.engine.workflow import JoinChunksNode
    from src.executors.join_chunks import JoinChunksExecutor

    node = JoinChunksNode.model_validate(
        {
            "id": "j",
            "type": "join-chunks",
            "position": {"x": 0, "y": 0},
            "data": {
                "label": "J",
                "joinChunksVariable": "chunks",
                "joinChunksSeparator": " | ",
            },
        }
    )
    state = initial_state()
    state["variables"]["chunks"] = [
        {"id": "doc1", "score": 0.9, "text": "alpha"},
        {"id": "doc2", "score": 0.8, "text": "beta"},
    ]
    delta = await JoinChunksExecutor(node).arun(state)
    assert delta["variables"]["lastOutput"] == "alpha | beta"


async def test_dict_chunks_with_page_content_key() -> None:
    """LangChain Document shape: `page_content` is the canonical text field."""
    from src.engine.state import initial_state
    from src.engine.workflow import JoinChunksNode
    from src.executors.join_chunks import JoinChunksExecutor

    node = JoinChunksNode.model_validate(
        {
            "id": "j",
            "type": "join-chunks",
            "position": {"x": 0, "y": 0},
            "data": {
                "label": "J",
                "joinChunksVariable": "chunks",
                "joinChunksSeparator": "\n",
            },
        }
    )
    state = initial_state()
    state["variables"]["chunks"] = [
        {"page_content": "first", "metadata": {"source": "a.txt"}},
        {"page_content": "second", "metadata": {"source": "b.txt"}},
    ]
    delta = await JoinChunksExecutor(node).arun(state)
    assert delta["variables"]["lastOutput"] == "first\nsecond"


async def test_content_key_takes_priority_over_text() -> None:
    """If both `content` and `text` are present, `content` wins —
    `content` is the more explicit name and matches the legacy
    behaviour."""
    from src.engine.state import initial_state
    from src.engine.workflow import JoinChunksNode
    from src.executors.join_chunks import JoinChunksExecutor

    node = JoinChunksNode.model_validate(
        {
            "id": "j",
            "type": "join-chunks",
            "position": {"x": 0, "y": 0},
            "data": {
                "label": "J",
                "joinChunksVariable": "chunks",
                "joinChunksSeparator": ",",
            },
        }
    )
    state = initial_state()
    state["variables"]["chunks"] = [
        {"content": "explicit", "text": "fallback"},
    ]
    delta = await JoinChunksExecutor(node).arun(state)
    assert delta["variables"]["lastOutput"] == "explicit"
