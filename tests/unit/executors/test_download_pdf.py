"""Tests for DownloadPdfExecutor."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from src.db.runtime import register_sessionmaker, unregister_sessionmaker
from tests.unit.db_fakes import RecordingSessionmaker, assert_no_network_inside_session
from tests.unit.mcp._oauth_fakes import script_lock


@pytest.fixture(autouse=True)
def _reset_db_ctx() -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
    from src.engine.context import _current_db  # pyright: ignore[reportPrivateUsage]

    token = _current_db.set(None)
    yield
    _current_db.reset(token)


@pytest.fixture
def registered_sessionmaker() -> Iterator[RecordingSessionmaker]:
    """The app-lifespan sessionmaker the executor reads Drive connections with."""
    events: list[str] = []
    sm = RecordingSessionmaker(events)
    register_sessionmaker(sm)  # pyright: ignore[reportArgumentType]
    yield sm
    unregister_sessionmaker(sm)  # pyright: ignore[reportArgumentType]


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


async def test_writes_pdf_to_google_drive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registered_sessionmaker: RecordingSessionmaker
) -> None:
    import src.executors.download_pdf as download_pdf_mod
    from src.engine.state import initial_state
    from src.executors.download_pdf import DownloadPdfExecutor

    events = registered_sessionmaker.events

    async def _fake_token(connection_id: str, sessionmaker: Any) -> str:
        assert connection_id == "conn-1"
        assert sessionmaker is registered_sessionmaker
        # The helper opens (and closes) its own short sessions around its database reads.
        async with sessionmaker():
            events.append("db:get")
        return "fake-access-token"

    monkeypatch.setattr(download_pdf_mod, "get_valid_drive_access_token", _fake_token)

    write_calls: list[tuple[str, str, bytes]] = []

    class _FakeDriveProvider:
        def __init__(self, access_token: str) -> None:
            assert access_token == "fake-access-token"

        async def write_file(self, dest: str, filename: str, content: bytes) -> None:
            events.append("net:drive-write")
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
    # Short-session rule: the Drive upload happens with no session open.
    assert events == ["session:enter", "db:get", "session:exit", "net:drive-write"]
    assert_no_network_inside_session(events)


async def test_drive_upload_with_real_token_helper_holds_no_session_across_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    registered_sessionmaker: RecordingSessionmaker,
) -> None:
    """End to end through the real get_valid_drive_access_token with an expired token: the
    fast-path read session closes first; the refresh runs in one session under the advisory
    lock (the Google refresh is the only network call inside it); the Drive upload runs with
    no session open."""
    import base64
    import os
    from datetime import UTC, datetime, timedelta

    import src.executors.download_pdf as download_pdf_mod
    import src.integrations.google_drive.oauth as drive_oauth
    from src.config import get_settings
    from src.db.repos import cloud_storage as cloud_storage_repo
    from src.db.repos.cloud_storage import CloudStorageConnectionRecord
    from src.engine.state import initial_state
    from src.executors.download_pdf import DownloadPdfExecutor
    from src.security.encryption import encrypt

    monkeypatch.setenv("ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    get_settings.cache_clear()
    events = registered_sessionmaker.events
    now = datetime(2026, 1, 1, tzinfo=UTC)

    def _record(access: str, expires_at: datetime) -> CloudStorageConnectionRecord:
        return CloudStorageConnectionRecord(
            id="conn-1",
            user_id="u1",
            provider="google-drive",
            account_email="a@example.com",
            encrypted_access_token=encrypt(access),
            encrypted_refresh_token=encrypt("rt-1"),
            expires_at=expires_at,
            scope=None,
            created_at=now,
            updated_at=now,
        )

    async def _get(_s: Any, _id: str) -> CloudStorageConnectionRecord:
        events.append("db:get")
        return _record("old-at", datetime.now(UTC) - timedelta(minutes=1))

    async def _update(_s: Any, _id: str, _changes: Any) -> CloudStorageConnectionRecord:
        events.append("db:update")
        return _record("new-at", datetime.now(UTC) + timedelta(hours=1))

    async def _refresh(_rt: str) -> dict[str, Any]:
        events.append("net:refresh")
        return {"access_token": "new-at", "expires_in": 3600}

    class _FakeDriveProvider:
        def __init__(self, access_token: str) -> None:
            assert access_token == "new-at"

        async def write_file(self, dest: str, filename: str, content: bytes) -> None:
            events.append("net:drive-write")

    script_lock(monkeypatch, events)
    monkeypatch.setattr(cloud_storage_repo, "get", _get)
    monkeypatch.setattr(cloud_storage_repo, "update_tokens", _update)
    monkeypatch.setattr(drive_oauth, "refresh_access_token", _refresh)
    monkeypatch.setattr(download_pdf_mod, "GoogleDriveProvider", _FakeDriveProvider)

    node = _node(tmp_path, provider="google-drive", connectionId="conn-1", driveFolderId="f1")
    await DownloadPdfExecutor(node).arun(initial_state())

    assert events == [
        "session:enter",
        "db:get",
        "session:exit",
        "session:enter",
        "db:lock",
        "db:get",
        "net:refresh",
        "db:update",
        "session:exit",
        "net:drive-write",
    ]
    assert_no_network_inside_session(events, allowed_inside=frozenset({"net:refresh"}))


async def test_google_drive_without_a_registered_sessionmaker_raises_pinned_error(
    tmp_path: Path,
) -> None:
    """The message predates the SQLAlchemy move and is pinned; what it asks for is now the
    registered sessionmaker (set by the app lifespan)."""
    from src.engine.state import initial_state
    from src.executors.download_pdf import DownloadPdfExecutor

    node = _node(tmp_path, provider="google-drive", connectionId="conn-1", driveFolderId="f")
    with pytest.raises(RuntimeError, match=r"requires src\.engine\.context\.set_current_db"):
        await DownloadPdfExecutor(node).arun(initial_state())


async def test_google_drive_missing_connection_id_raises(tmp_path: Path) -> None:
    from src.engine.state import initial_state
    from src.executors.download_pdf import DownloadPdfExecutor, MissingDriveConfigError

    node = _node(tmp_path, provider="google-drive", driveFolderId="folder-1")
    with pytest.raises(MissingDriveConfigError):
        await DownloadPdfExecutor(node).arun(initial_state())


async def test_google_drive_missing_folder_id_raises(tmp_path: Path) -> None:
    from src.engine.state import initial_state
    from src.executors.download_pdf import DownloadPdfExecutor, MissingDriveConfigError

    node = _node(tmp_path, provider="google-drive", connectionId="conn-1")
    with pytest.raises(MissingDriveConfigError):
        await DownloadPdfExecutor(node).arun(initial_state())
