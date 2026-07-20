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


async def test_list_new_files_excludes_folders(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    """Without a mimeType exclusion, a subfolder inside the watched Drive
    folder gets listed as a "new file", then fails at read_file() (folders
    don't support alt=media) and gets PERMANENTLY marked composerStatus=error
    by the poller's generic per-file error handling — silently poisoning an
    unrelated Drive folder object forever, since this provider's claim
    marker is permanent. Asserts the outgoing query excludes Drive's folder
    mimeType; the existing per-file error-isolation logic (Task 8) already
    handles whatever non-folder items come back."""
    from src.storage_providers.google_drive import GoogleDriveProvider

    httpx_mock.add_response(
        url=re.compile(r"^https://www\.googleapis\.com/drive/v3/files\?"),
        method="GET",
        json={"files": []},
    )

    provider = GoogleDriveProvider("at-1")
    await provider.list_new_files("folder123")

    req = httpx_mock.get_request()
    assert req is not None
    q = req.url.params["q"]
    assert "mimeType != 'application/vnd.google-apps.folder'" in q


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


async def test_move_file_relocates_to_processed_folder_when_configured(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    """When processed_folder_id is configured, a successful claim both sets
    the appProperties marker (kept as the permanent backup claim mechanism)
    AND visibly relocates the file via Drive's addParents/removeParents --
    mirroring the local provider's dest_path move, unlike the marker-only
    behavior when no folder is configured."""
    from src.storage_providers.base import FileRef
    from src.storage_providers.google_drive import GoogleDriveProvider

    httpx_mock.add_response(
        url="https://www.googleapis.com/drive/v3/files/f1",
        method="PATCH",
        json={"id": "f1"},
    )
    httpx_mock.add_response(
        url="https://www.googleapis.com/drive/v3/files/f1?fields=parents",
        method="GET",
        json={"parents": ["old-parent-1"]},
    )
    httpx_mock.add_response(
        url="https://www.googleapis.com/drive/v3/files/f1?addParents=processed-folder-id&removeParents=old-parent-1",
        method="PATCH",
        json={"id": "f1"},
    )

    provider = GoogleDriveProvider("at-1", processed_folder_id="processed-folder-id")
    ref = FileRef(identifier="f1", name="report.pdf", size_bytes=10, modified_at=datetime.now(UTC))
    await provider.move_file(ref, "processed")

    requests = httpx_mock.get_requests()
    assert len(requests) == 3
    import json as _json

    assert _json.loads(requests[0].content) == {"appProperties": {"composerStatus": "processed"}}
    assert str(requests[1].url) == "https://www.googleapis.com/drive/v3/files/f1?fields=parents"
    assert "addParents=processed-folder-id" in str(requests[2].url)
    assert "removeParents=old-parent-1" in str(requests[2].url)


async def test_move_file_relocates_to_error_folder_when_configured(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    from src.storage_providers.base import FileRef
    from src.storage_providers.google_drive import GoogleDriveProvider

    httpx_mock.add_response(
        url="https://www.googleapis.com/drive/v3/files/f1", method="PATCH", json={"id": "f1"}
    )
    httpx_mock.add_response(
        url="https://www.googleapis.com/drive/v3/files/f1?fields=parents",
        method="GET",
        json={"parents": ["old-parent-1"]},
    )
    httpx_mock.add_response(
        url="https://www.googleapis.com/drive/v3/files/f1?addParents=error-folder-id&removeParents=old-parent-1",
        method="PATCH",
        json={"id": "f1"},
    )

    provider = GoogleDriveProvider("at-1", error_folder_id="error-folder-id")
    ref = FileRef(identifier="f1", name="report.pdf", size_bytes=10, modified_at=datetime.now(UTC))
    await provider.move_file(ref, "error")

    requests = httpx_mock.get_requests()
    assert len(requests) == 3
    assert "addParents=error-folder-id" in str(requests[2].url)


async def test_move_file_does_not_move_wrong_outcome_folder(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    """Only error_folder_id is configured; a successful ("processed") claim
    must not try to move into it -- the marker is set, but no move happens
    since processed_folder_id is unset."""
    from src.storage_providers.base import FileRef
    from src.storage_providers.google_drive import GoogleDriveProvider

    httpx_mock.add_response(
        url="https://www.googleapis.com/drive/v3/files/f1", method="PATCH", json={"id": "f1"}
    )

    provider = GoogleDriveProvider("at-1", error_folder_id="error-folder-id")
    ref = FileRef(identifier="f1", name="report.pdf", size_bytes=10, modified_at=datetime.now(UTC))
    await provider.move_file(ref, "processed")

    assert len(httpx_mock.get_requests()) == 1


async def test_write_file_creates_new_file_when_not_found(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    """No existing file matches the search query -> a metadata-only create
    (regular API) followed by a content upload (upload API) against the
    newly-created file id. No update/PATCH against a pre-existing id."""
    from src.storage_providers.google_drive import GoogleDriveProvider

    httpx_mock.add_response(
        url=re.compile(r"^https://www\.googleapis\.com/drive/v3/files\?"),
        method="GET",
        json={"files": []},
    )
    httpx_mock.add_response(
        url="https://www.googleapis.com/drive/v3/files",
        method="POST",
        json={"id": "new456"},
    )
    httpx_mock.add_response(
        url="https://www.googleapis.com/upload/drive/v3/files/new456?uploadType=media",
        method="PATCH",
        json={"id": "new456"},
    )

    provider = GoogleDriveProvider("at-1")
    await provider.write_file("folder123", "report.pdf", b"%PDF-fake-bytes")

    requests = httpx_mock.get_requests()
    assert len(requests) == 3

    import json as _json

    create_req = requests[1]
    assert create_req.method == "POST"
    assert _json.loads(create_req.content) == {"name": "report.pdf", "parents": ["folder123"]}

    upload_req = requests[2]
    assert upload_req.method == "PATCH"
    assert (
        str(upload_req.url)
        == "https://www.googleapis.com/upload/drive/v3/files/new456?uploadType=media"
    )
    assert upload_req.content == b"%PDF-fake-bytes"
    assert upload_req.headers.get("content-type") == "application/octet-stream"
    assert upload_req.headers.get("authorization") == "Bearer at-1"


async def test_write_file_updates_existing_file_in_place(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    """A file with the same name already exists in the destination folder ->
    its content is replaced (same file id), no new file is created, and no
    POST /files create call happens at all."""
    from src.storage_providers.google_drive import GoogleDriveProvider

    httpx_mock.add_response(
        url=re.compile(r"^https://www\.googleapis\.com/drive/v3/files\?"),
        method="GET",
        json={"files": [{"id": "existing123"}]},
    )
    httpx_mock.add_response(
        url="https://www.googleapis.com/upload/drive/v3/files/existing123?uploadType=media",
        method="PATCH",
        json={"id": "existing123"},
    )

    provider = GoogleDriveProvider("at-1")
    await provider.write_file("folder123", "report.pdf", b"%PDF-new-bytes")

    requests = httpx_mock.get_requests()
    assert len(requests) == 2
    assert requests[0].method == "GET"
    assert requests[1].method == "PATCH"
    assert (
        str(requests[1].url)
        == "https://www.googleapis.com/upload/drive/v3/files/existing123?uploadType=media"
    )
    assert requests[1].content == b"%PDF-new-bytes"


async def test_write_file_search_query_matches_name_and_parent_folder(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    from src.storage_providers.google_drive import GoogleDriveProvider

    httpx_mock.add_response(
        url=re.compile(r"^https://www\.googleapis\.com/drive/v3/files\?"),
        method="GET",
        json={"files": []},
    )
    httpx_mock.add_response(
        url="https://www.googleapis.com/drive/v3/files", method="POST", json={"id": "n1"}
    )
    httpx_mock.add_response(
        url="https://www.googleapis.com/upload/drive/v3/files/n1?uploadType=media",
        method="PATCH",
        json={"id": "n1"},
    )

    provider = GoogleDriveProvider("at-1")
    await provider.write_file("folder123", "weekly-report.pdf", b"content")

    search_req = httpx_mock.get_requests()[0]
    q = search_req.url.params["q"]
    assert q == (
        "name = 'weekly-report.pdf' and 'folder123' in parents "
        "and trashed = false and mimeType != 'application/vnd.google-apps.folder'"
    )


async def test_write_file_escapes_quotes_and_backslashes(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    """Mirrors list_new_files's existing escaping test -- filename and
    destination folder id are both attacker-reachable (substituted from
    workflow state), so both must be escaped the same way before being
    interpolated into the Drive query-language string."""
    from src.storage_providers.google_drive import GoogleDriveProvider

    httpx_mock.add_response(
        url=re.compile(r"^https://www\.googleapis\.com/drive/v3/files\?"),
        method="GET",
        json={"files": []},
    )
    httpx_mock.add_response(
        url="https://www.googleapis.com/drive/v3/files", method="POST", json={"id": "n1"}
    )
    httpx_mock.add_response(
        url="https://www.googleapis.com/upload/drive/v3/files/n1?uploadType=media",
        method="PATCH",
        json={"id": "n1"},
    )

    provider = GoogleDriveProvider("at-1")
    await provider.write_file("folder'with\\special", "report'name.pdf", b"content")

    search_req = httpx_mock.get_requests()[0]
    q = search_req.url.params["q"]
    assert "name = 'report\\'name.pdf'" in q
    assert "'folder\\'with\\\\special' in parents" in q


async def test_write_file_raises_on_search_http_error(
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
        await provider.write_file("folder123", "report.pdf", b"content")


async def test_write_file_raises_on_create_http_error(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    from src.storage_providers.google_drive import GoogleDriveProvider, GoogleDriveProviderError

    httpx_mock.add_response(
        url=re.compile(r"^https://www\.googleapis\.com/drive/v3/files\?"),
        method="GET",
        json={"files": []},
    )
    httpx_mock.add_response(
        url="https://www.googleapis.com/drive/v3/files",
        method="POST",
        status_code=500,
        text="server error",
    )

    provider = GoogleDriveProvider("at-1")
    with pytest.raises(GoogleDriveProviderError):
        await provider.write_file("folder123", "report.pdf", b"content")


async def test_write_file_raises_on_upload_http_error(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    from src.storage_providers.google_drive import GoogleDriveProvider, GoogleDriveProviderError

    httpx_mock.add_response(
        url=re.compile(r"^https://www\.googleapis\.com/drive/v3/files\?"),
        method="GET",
        json={"files": [{"id": "existing123"}]},
    )
    httpx_mock.add_response(
        url="https://www.googleapis.com/upload/drive/v3/files/existing123?uploadType=media",
        method="PATCH",
        status_code=403,
        text="permission denied",
    )

    provider = GoogleDriveProvider("at-1")
    with pytest.raises(GoogleDriveProviderError):
        await provider.write_file("folder123", "report.pdf", b"content")


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


async def test_list_new_files_escapes_quotes_and_backslashes_in_source(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    """source = "folder'with\\special" contains both a quote and a
    trailing-adjacent backslash. Backslash must be escaped before quote
    (classic ordering requirement — escaping quote first can't tell an
    original backslash from one just inserted), otherwise a source ending
    in an odd number of backslashes neutralizes the quote-escaping and
    corrupts the query's trailing `not appProperties has {...}` exclusion
    clauses that implement never-re-claim. Asserts the actual escaped
    query string sent to Drive, so a future edit that drops the
    .replace() calls entirely fails this test."""
    from src.storage_providers.google_drive import GoogleDriveProvider

    httpx_mock.add_response(
        url=re.compile(r"^https://www\.googleapis\.com/drive/v3/files\?"),
        method="GET",
        json={"files": []},
    )

    provider = GoogleDriveProvider("at-1")
    await provider.list_new_files("folder'with\\special")

    req = httpx_mock.get_request()
    assert req is not None
    q = req.url.params["q"]

    # Escaped form: original `'` becomes `\'`, original single `\` becomes
    # `\\` — written as a raw literal here (not derived via the same
    # .replace() calls under test) so the assertion is a real oracle, not
    # tautological with the implementation.
    assert "'folder\\'with\\\\special' in parents" in q
    # The raw, unescaped source must never appear on its own — if it does,
    # escaping was skipped or applied in the wrong order.
    assert "'folder'with\\special' in parents" not in q
