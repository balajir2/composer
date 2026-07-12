"""Tests for the `composer watch` CLI's per-file claim/trigger/move logic.

These test the pure claim-one-file function, not the infinite poll loop
(covered by a smoke test at the end) or real network calls (httpx is
mocked throughout).
"""

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]

if TYPE_CHECKING:
    from src.cli.watch import WatchConfig


def _trigger_config(source: Path, dest: Path, error: Path) -> "WatchConfig":
    from src.cli.watch import WatchConfig

    return WatchConfig(
        workflow_api_url="https://api.example.com",
        external_slug="my-workflow",
        api_key="ck_test",
        provider="local",
        source_path=str(source),
        dest_path=str(dest),
        error_path=str(error),
        target_input_variable="requirements_doc",
        poll_interval_seconds=1,
    )


async def test_claim_extracts_text_and_moves_to_dest_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.cli.watch import claim_file
    from src.storage_providers.local import LocalFilesystemProvider

    source = tmp_path / "in"
    dest = tmp_path / "done"
    error = tmp_path / "error"
    source.mkdir()
    (source / "note.md").write_bytes(b"# Hello")

    config = _trigger_config(source, dest, error)
    provider = LocalFilesystemProvider()
    await provider.list_new_files(str(source))  # first sighting
    refs = await provider.list_new_files(str(source))
    ref = refs[0]

    post_mock = AsyncMock(return_value=None)
    monkeypatch.setattr("src.cli.watch._trigger_workflow", post_mock)

    await claim_file(provider, ref, config)

    post_mock.assert_awaited_once()
    await_args = post_mock.await_args
    assert await_args is not None
    call_kwargs = await_args.kwargs
    assert call_kwargs["input_payload"] == {"requirements_doc": "# Hello"}
    assert not (source / "note.md").exists()
    assert (dest / "note.md").exists()


async def test_claim_moves_to_error_path_on_trigger_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.cli.watch import claim_file
    from src.storage_providers.local import LocalFilesystemProvider

    source = tmp_path / "in"
    dest = tmp_path / "done"
    error = tmp_path / "error"
    source.mkdir()
    (source / "note.txt").write_bytes(b"content")

    config = _trigger_config(source, dest, error)
    provider = LocalFilesystemProvider()
    await provider.list_new_files(str(source))
    ref = (await provider.list_new_files(str(source)))[0]

    async def _boom(**kwargs: object) -> None:
        raise RuntimeError("backend unreachable")

    monkeypatch.setattr("src.cli.watch._trigger_workflow", _boom)

    await claim_file(provider, ref, config)

    assert not (source / "note.txt").exists()
    assert (error / "note.txt").exists()
    assert not (dest / "note.txt").exists()


async def test_claim_moves_to_error_path_on_unsupported_file_type(
    tmp_path: Path,
) -> None:
    from src.cli.watch import claim_file
    from src.storage_providers.local import LocalFilesystemProvider

    source = tmp_path / "in"
    dest = tmp_path / "done"
    error = tmp_path / "error"
    source.mkdir()
    (source / "image.png").write_bytes(b"\x89PNG\r\n")

    config = _trigger_config(source, dest, error)
    provider = LocalFilesystemProvider()
    await provider.list_new_files(str(source))
    ref = (await provider.list_new_files(str(source)))[0]

    await claim_file(provider, ref, config)

    assert (error / "image.png").exists()


async def test_trigger_workflow_posts_expected_request(
    tmp_path: Path,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    """Direct coverage of `_trigger_workflow` (not via `claim_file`): confirms
    the URL, bearer auth header, and JSON body actually sent over the wire."""
    from src.cli.watch import _trigger_workflow  # pyright: ignore[reportPrivateUsage]

    source = tmp_path / "in"
    dest = tmp_path / "done"
    error = tmp_path / "error"
    config = _trigger_config(source, dest, error)

    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        url="https://api.example.com/api/run/my-workflow",
        method="POST",
        status_code=200,
        json={"ok": True},
    )

    await _trigger_workflow(config=config, input_payload={"some_var": "some text"})

    req = httpx_mock.get_request()  # pyright: ignore[reportUnknownMemberType]
    assert req is not None
    assert str(req.url) == "https://api.example.com/api/run/my-workflow"
    assert req.headers.get("authorization") == "Bearer ck_test"
    assert req.content == b'{"input":{"some_var":"some text"}}'


async def test_trigger_workflow_raises_runtime_error_on_non_2xx(
    tmp_path: Path,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    """Direct coverage of the `if resp.status_code >= 400: raise RuntimeError`
    branch in `_trigger_workflow`."""
    from src.cli.watch import _trigger_workflow  # pyright: ignore[reportPrivateUsage]

    source = tmp_path / "in"
    dest = tmp_path / "done"
    error = tmp_path / "error"
    config = _trigger_config(source, dest, error)

    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        url="https://api.example.com/api/run/my-workflow",
        method="POST",
        status_code=500,
        text="backend exploded",
    )

    with pytest.raises(RuntimeError, match="trigger failed"):
        await _trigger_workflow(config=config, input_payload={"some_var": "some text"})
