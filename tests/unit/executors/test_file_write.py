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


async def test_rejects_path_traversal_in_filename(tmp_path: Path) -> None:
    """A filename with `..` segments must not be able to escape destinationPath.

    Simulates the attack in the security review: a substituted filename
    (e.g. from an upstream http/agent/extract node output) tries to climb
    out of the configured destination directory.
    """
    from src.engine.state import initial_state
    from src.executors.file_write import FileWriteExecutor, InvalidFilenameError

    node = _node(tmp_path, filename="../../evil")
    with pytest.raises(InvalidFilenameError):
        await FileWriteExecutor(node).arun(initial_state())

    # Nothing should have been written anywhere, in particular not at the
    # traversal target outside tmp_path.
    escaped_target = (tmp_path / "../../evil.md").resolve()
    assert not escaped_target.exists()
    assert list(tmp_path.iterdir()) == []


async def test_rejects_forward_slash_in_filename(tmp_path: Path) -> None:
    """A filename containing a forward-slash path separator is rejected.

    Portable across POSIX and Windows: a leading `/` makes the joined path
    "rooted" on both platforms (on Windows it is drive-relative rather than
    fully absolute, but it still is not a bare filename) -- the separator
    check alone is what must catch it, without relying on OS-specific
    is_absolute() semantics.
    """
    from src.engine.state import initial_state
    from src.executors.file_write import FileWriteExecutor, InvalidFilenameError

    node = _node(tmp_path, filename="/etc/evil")
    with pytest.raises(InvalidFilenameError):
        await FileWriteExecutor(node).arun(initial_state())
    assert list(tmp_path.iterdir()) == []


async def test_rejects_backslash_in_filename(tmp_path: Path) -> None:
    """A filename containing a backslash (Windows path separator, or a
    Windows absolute path like C:\\Windows\\evil) is rejected -- the check
    must not assume POSIX-only separators."""
    from src.engine.state import initial_state
    from src.executors.file_write import FileWriteExecutor, InvalidFilenameError

    node = _node(tmp_path, filename="C:\\Windows\\evil")
    with pytest.raises(InvalidFilenameError):
        await FileWriteExecutor(node).arun(initial_state())
    assert list(tmp_path.iterdir()) == []


async def test_rejects_empty_destination_path(tmp_path: Path) -> None:
    """An empty/blank destinationPath (after substitution) must be rejected
    rather than silently resolving to the process's current working
    directory (Path("") == Path(".))."""
    from src.engine.state import initial_state
    from src.executors.file_write import FileWriteExecutor, InvalidDestinationError

    node = _node(tmp_path, destinationPath="")
    with pytest.raises(InvalidDestinationError):
        await FileWriteExecutor(node).arun(initial_state())

    # Confirm nothing landed in the actual process cwd either.
    assert not (Path.cwd() / "report.md").exists()


async def test_rejects_missing_destination_path(tmp_path: Path) -> None:
    """destinationPath substituting to a template that resolves to an empty
    string (e.g. an unset variable rendering blank) is likewise rejected."""
    from src.engine.state import initial_state
    from src.executors.file_write import FileWriteExecutor, InvalidDestinationError

    node = _node(tmp_path, destinationPath="   ")
    with pytest.raises(InvalidDestinationError):
        await FileWriteExecutor(node).arun(initial_state())


async def test_writes_docx_file(tmp_path: Path) -> None:
    from src.engine.state import initial_state
    from src.executors.file_write import FileWriteExecutor

    node = _node(tmp_path, format="docx", content="# Title\n\nBody text.")
    await FileWriteExecutor(node).arun(initial_state())

    written = tmp_path / "report.docx"
    assert written.exists()
    from docx import Document

    doc = Document(str(written))
    assert any(p.text == "Title" for p in doc.paragraphs)


async def test_writes_pdf_file(tmp_path: Path) -> None:
    from src.engine.state import initial_state
    from src.executors.file_write import FileWriteExecutor

    node = _node(tmp_path, format="pdf", content="# Title\n\nBody.")
    await FileWriteExecutor(node).arun(initial_state())

    written = tmp_path / "report.pdf"
    assert written.exists()
    assert written.read_bytes().startswith(b"%PDF-")
