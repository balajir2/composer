"""Tests for DownloadPdfExecutor."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _reset_db_ctx() -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
    from src.engine.context import _current_db  # pyright: ignore[reportPrivateUsage]

    token = _current_db.set(None)
    yield
    _current_db.reset(token)


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


async def test_writes_pdf_to_google_drive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import src.engine.context as ctx_mod
    import src.executors.download_pdf as download_pdf_mod
    from src.engine.state import initial_state
    from src.executors.download_pdf import DownloadPdfExecutor

    fake_db = MagicMock()
    ctx_mod.set_current_db(fake_db)

    async def _fake_token(connection_id: str, db: Any) -> str:
        assert connection_id == "conn-1"
        assert db is fake_db
        return "fake-access-token"

    monkeypatch.setattr(download_pdf_mod, "get_valid_drive_access_token", _fake_token)

    write_calls: list[tuple[str, str, bytes]] = []

    class _FakeDriveProvider:
        def __init__(self, access_token: str) -> None:
            assert access_token == "fake-access-token"

        async def write_file(self, dest: str, filename: str, content: bytes) -> None:
            write_calls.append((dest, filename, content))

    monkeypatch.setattr(download_pdf_mod, "GoogleDriveProvider", _FakeDriveProvider)

    node = _node(
        tmp_path,
        provider="google-drive",
        connectionId="conn-1",
        driveFolderId="folder-1",
    )
    delta = await DownloadPdfExecutor(node).arun(initial_state())

    assert len(write_calls) == 1
    dest, filename, content = write_calls[0]
    assert dest == "folder-1"
    assert filename == "report.pdf"
    assert content.startswith(b"%PDF-")
    assert delta["variables"]["lastOutput"] == "drive://folder-1/report.pdf"
    assert delta["node_results"]["dp1"]["status"] == "completed"


async def test_google_drive_missing_connection_id_raises(tmp_path: Path) -> None:
    import src.engine.context as ctx_mod
    from src.engine.state import initial_state
    from src.executors.download_pdf import DownloadPdfExecutor, MissingDriveConfigError

    ctx_mod.set_current_db(MagicMock())
    node = _node(tmp_path, provider="google-drive", driveFolderId="folder-1")
    with pytest.raises(MissingDriveConfigError):
        await DownloadPdfExecutor(node).arun(initial_state())


async def test_google_drive_missing_folder_id_raises(tmp_path: Path) -> None:
    import src.engine.context as ctx_mod
    from src.engine.state import initial_state
    from src.executors.download_pdf import DownloadPdfExecutor, MissingDriveConfigError

    ctx_mod.set_current_db(MagicMock())
    node = _node(tmp_path, provider="google-drive", connectionId="conn-1")
    with pytest.raises(MissingDriveConfigError):
        await DownloadPdfExecutor(node).arun(initial_state())
