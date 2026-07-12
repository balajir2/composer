"""Tests for FileWriteExecutor."""

from pathlib import Path
from typing import Any

import pytest


def _node(tmp_path: Path, **data_overrides: Any):
    from src.engine.workflow import FileWriteNode

    data: dict[str, Any] = {
        "label": "File Write",
        "destinationPath": str(tmp_path),
        "filename": "report",
        "format": "md",
        "content": "# Hello {{name}}",
    }
    data.update(data_overrides)
    return FileWriteNode.model_validate(
        {"id": "fw1", "type": "file-write", "position": {"x": 0, "y": 0}, "data": data}
    )


async def test_writes_markdown_file_with_substitution(tmp_path: Path) -> None:
    from src.engine.state import initial_state
    from src.executors.file_write import FileWriteExecutor

    state = initial_state()
    state["variables"]["name"] = "Ada"
    node = _node(tmp_path)
    delta = await FileWriteExecutor(node).arun(state)

    written = tmp_path / "report.md"
    assert written.read_text(encoding="utf-8") == "# Hello Ada"
    assert delta["variables"]["lastOutput"] == str(written)
    assert delta["node_results"]["fw1"]["status"] == "completed"


async def test_filename_and_destination_support_substitution(tmp_path: Path) -> None:
    from src.engine.state import initial_state
    from src.executors.file_write import FileWriteExecutor

    state = initial_state()
    state["variables"]["project"] = "acme"
    node = _node(tmp_path, filename="{{project}}-report")
    await FileWriteExecutor(node).arun(state)
    assert (tmp_path / "acme-report.md").exists()


async def test_unknown_provider_raises(tmp_path: Path) -> None:
    from src.engine.state import initial_state
    from src.executors.file_write import FileWriteExecutor, UnknownStorageProviderError

    node = _node(tmp_path, provider="s3")
    with pytest.raises(UnknownStorageProviderError):
        await FileWriteExecutor(node).arun(initial_state())
