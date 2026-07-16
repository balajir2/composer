"""Tests for GoogleDriveProvider against a mocked Drive v3 REST API."""

import re
from datetime import UTC, datetime

import httpx
import pytest
from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]


async def test_list_new_files_excludes_marked_files(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    from src.storage_providers.google_drive import GoogleDriveProvider

    httpx_mock.add_response(
        # The installed pytest-httpx (0.36.2) only accepts str / re.Pattern /
        # httpx.URL for `url` — not a callable — so a prefix regex replaces
        # the lambda-based startswith check the task spec used.
        url=re.compile(r"^https://www\.googleapis\.com/drive/v3/files\?"),
        method="GET",
        json={
            "files": [
                {
                    "id": "f1",
                    "name": "report.pdf",
                    "size": "1024",
                    "modifiedTime": "2026-07-16T00:00:00.000Z",
                }
            ]
        },
    )

    provider = GoogleDriveProvider("at-1")
    refs = await provider.list_new_files("folder123")

    assert len(refs) == 1
    assert refs[0].identifier == "f1"
    assert refs[0].name == "report.pdf"
    assert refs[0].size_bytes == 1024
    assert refs[0].modified_at == datetime(2026, 7, 16, tzinfo=UTC)

    req = httpx_mock.get_request()
    assert req is not None
    assert "not+appProperties" in str(req.url) or "not%20appProperties" in str(req.url)
    assert req.headers.get("authorization") == "Bearer at-1"


async def test_read_file_downloads_media(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    from src.storage_providers.base import FileRef
    from src.storage_providers.google_drive import GoogleDriveProvider

    httpx_mock.add_response(
        url="https://www.googleapis.com/drive/v3/files/f1?alt=media",
        method="GET",
        content=b"file bytes",
    )

    provider = GoogleDriveProvider("at-1")
    ref = FileRef(identifier="f1", name="report.pdf", size_bytes=10, modified_at=datetime.now(UTC))
    content = await provider.read_file(ref)

    assert content == b"file bytes"


async def test_move_file_sets_app_properties(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    from src.storage_providers.base import FileRef
    from src.storage_providers.google_drive import GoogleDriveProvider

    httpx_mock.add_response(
        url="https://www.googleapis.com/drive/v3/files/f1",
        method="PATCH",
        json={"id": "f1"},
    )

    provider = GoogleDriveProvider("at-1")
    ref = FileRef(identifier="f1", name="report.pdf", size_bytes=10, modified_at=datetime.now(UTC))
    await provider.move_file(ref, "processed")

    req = httpx_mock.get_request()
    assert req is not None
    import json as _json

    body = _json.loads(req.content)
    assert body == {"appProperties": {"composerStatus": "processed"}}


async def test_write_file_raises_not_implemented() -> None:
    from src.storage_providers.google_drive import GoogleDriveProvider

    provider = GoogleDriveProvider("at-1")
    with pytest.raises(NotImplementedError):
        await provider.write_file("dest", "name.md", b"content")


async def test_list_new_files_raises_on_http_error(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    from src.storage_providers.google_drive import GoogleDriveProvider, GoogleDriveProviderError

    httpx_mock.add_response(
        url=re.compile(r"^https://www\.googleapis\.com/drive/v3/files\?"),
        method="GET",
        status_code=403,
        text="permission denied",
    )

    provider = GoogleDriveProvider("at-1")
    with pytest.raises(GoogleDriveProviderError):
        await provider.list_new_files("folder123")


async def test_read_file_raises_on_http_error(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    from src.storage_providers.base import FileRef
    from src.storage_providers.google_drive import GoogleDriveProvider, GoogleDriveProviderError

    httpx_mock.add_response(
        url="https://www.googleapis.com/drive/v3/files/f1?alt=media",
        method="GET",
        status_code=403,
        text="permission denied",
    )

    provider = GoogleDriveProvider("at-1")
    ref = FileRef(identifier="f1", name="report.pdf", size_bytes=10, modified_at=datetime.now(UTC))
    with pytest.raises(GoogleDriveProviderError):
        await provider.read_file(ref)


async def test_move_file_raises_on_http_error(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    from src.storage_providers.base import FileRef
    from src.storage_providers.google_drive import GoogleDriveProvider, GoogleDriveProviderError

    httpx_mock.add_response(
        url="https://www.googleapis.com/drive/v3/files/f1",
        method="PATCH",
        status_code=403,
        text="permission denied",
    )

    provider = GoogleDriveProvider("at-1")
    ref = FileRef(identifier="f1", name="report.pdf", size_bytes=10, modified_at=datetime.now(UTC))
    with pytest.raises(GoogleDriveProviderError):
        await provider.move_file(ref, "processed")


async def test_list_new_files_wraps_transport_failure(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    """A genuine transport failure (connection refused, DNS, timeout — not
    an HTTP error status) must surface as GoogleDriveProviderError, not a
    raw httpx.ConnectError, so callers catching GoogleDriveProviderError
    don't miss it. Mirrors the pattern in
    tests/unit/integrations/test_google_drive_oauth.py and
    tests/unit/executors/test_arcade.py."""
    from src.storage_providers.google_drive import GoogleDriveProvider, GoogleDriveProviderError

    httpx_mock.add_exception(
        method="GET",
        url=re.compile(r"^https://www\.googleapis\.com/drive/v3/files\?"),
        exception=httpx.ConnectError("connection refused"),
    )

    provider = GoogleDriveProvider("at-1")
    with pytest.raises(GoogleDriveProviderError):
        await provider.list_new_files("folder123")
