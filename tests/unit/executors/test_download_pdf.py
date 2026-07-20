"""Tests for DownloadPdfExecutor."""

from pathlib import Path
from typing import Any

import pytest


def _node(tmp_path: Path, **data_overrides: Any):
    from src.engine.workflow import DownloadPdfNode

    data: dict[str, Any] = {
        "label": "Download PDF",
        "inputFormat": "html",
        "content": "<html><body><h1>Hello {{name}}</h1></body></html>",
        "destinationPath": str(tmp_path),
        "filename": "report",
    }
    data.update(data_overrides)
    return DownloadPdfNode.model_validate(
        {"id": "dp1", "type": "download-pdf", "position": {"x": 0, "y": 0}, "data": data}
    )


async def test_writes_pdf_from_html(tmp_path: Path) -> None:
    from src.engine.state import initial_state
    from src.executors.download_pdf import DownloadPdfExecutor

    state = initial_state()
    state["variables"]["name"] = "Ada"
    node = _node(tmp_path)
    delta = await DownloadPdfExecutor(node).arun(state)

    written = tmp_path / "report.pdf"
    assert written.exists()
    assert written.read_bytes().startswith(b"%PDF-")
    assert delta["variables"]["lastOutput"] == str(written)
    assert delta["node_results"]["dp1"]["status"] == "completed"


async def test_writes_pdf_from_markdown(tmp_path: Path) -> None:
    from src.engine.state import initial_state
    from src.executors.download_pdf import DownloadPdfExecutor

    node = _node(tmp_path, inputFormat="markdown", content="# Title\n\nBody text.")
    await DownloadPdfExecutor(node).arun(initial_state())

    written = tmp_path / "report.pdf"
    assert written.exists()
    assert written.read_bytes().startswith(b"%PDF-")


async def test_filename_and_destination_support_substitution(tmp_path: Path) -> None:
    from src.engine.state import initial_state
    from src.executors.download_pdf import DownloadPdfExecutor

    state = initial_state()
    state["variables"]["project"] = "acme"
    node = _node(tmp_path, filename="{{project}}-report")
    await DownloadPdfExecutor(node).arun(state)
    assert (tmp_path / "acme-report.pdf").exists()


async def test_unknown_provider_raises(tmp_path: Path) -> None:
    from src.engine.state import initial_state
    from src.executors.download_pdf import DownloadPdfExecutor, UnknownStorageProviderError

    node = _node(tmp_path, provider="s3")
    with pytest.raises(UnknownStorageProviderError):
        await DownloadPdfExecutor(node).arun(initial_state())


async def test_rejects_path_traversal_in_filename(tmp_path: Path) -> None:
    from src.engine.state import initial_state
    from src.executors.download_pdf import DownloadPdfExecutor, InvalidFilenameError

    node = _node(tmp_path, filename="../../evil")
    with pytest.raises(InvalidFilenameError):
        await DownloadPdfExecutor(node).arun(initial_state())
    assert list(tmp_path.iterdir()) == []


async def test_rejects_empty_destination_path(tmp_path: Path) -> None:
    from src.engine.state import initial_state
    from src.executors.download_pdf import DownloadPdfExecutor, InvalidDestinationError

    node = _node(tmp_path, destinationPath="")
    with pytest.raises(InvalidDestinationError):
        await DownloadPdfExecutor(node).arun(initial_state())
    assert not (Path.cwd() / "report.pdf").exists()
