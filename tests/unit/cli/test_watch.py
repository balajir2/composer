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


async def test_claim_leaves_the_file_in_place_when_the_workflow_cannot_run_yet(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """HTTP 422 means the workflow is unfinished, not the file: no move, one warning."""
    import logging

    from src.cli import watch
    from src.cli.watch import WorkflowNotRunnableError, claim_file
    from src.storage_providers.local import LocalFilesystemProvider
    from src.warn_throttle import WarnThrottle

    source = tmp_path / "in"
    dest = tmp_path / "done"
    error = tmp_path / "error"
    source.mkdir()
    (source / "note.txt").write_bytes(b"content")
    config = _trigger_config(source, dest, error)
    provider = LocalFilesystemProvider()
    await provider.list_new_files(str(source))
    ref = (await provider.list_new_files(str(source)))[0]

    async def _unrunnable(**kwargs: object) -> None:
        raise WorkflowNotRunnableError('step "x" has no "rejected" path')

    monkeypatch.setattr("src.cli.watch._trigger_workflow", _unrunnable)
    monkeypatch.setattr(watch, "_NOT_RUNNABLE_WARNINGS", WarnThrottle(900))

    with caplog.at_level(logging.WARNING, logger="src.cli.watch"):
        for _ in range(3):
            await claim_file(provider, ref, config)

    assert (source / "note.txt").exists()
    assert not (error / "note.txt").exists()
    assert not (dest / "note.txt").exists()
    assert len([r for r in caplog.records if "cannot run yet" in r.getMessage()]) == 1


async def test_trigger_workflow_maps_http_422_to_not_runnable(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
    tmp_path: Path,
) -> None:
    from src.cli.watch import WorkflowNotRunnableError, _trigger_workflow  # pyright: ignore[reportPrivateUsage]

    httpx_mock.add_response(status_code=422, text="not runnable")  # pyright: ignore[reportUnknownMemberType]
    config = _trigger_config(tmp_path, tmp_path, tmp_path)
    with pytest.raises(WorkflowNotRunnableError):
        await _trigger_workflow(config=config, input_payload={"a": "b"})


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def _poll_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    statuses: list[str],
    slug: str = "my-workflow",
) -> tuple[list[str], list[str]]:
    """Run one `poll_once` pass over a folder holding one file; return (posted slugs, reads)."""
    from dataclasses import replace

    from src.cli import watch
    from src.storage_providers.local import LocalFilesystemProvider

    source = tmp_path / "in"
    source.mkdir(parents=True, exist_ok=True)
    (source / "note.txt").write_bytes(b"content")
    config = replace(
        _trigger_config(source, tmp_path / "done", tmp_path / "error"), external_slug=slug
    )
    provider = LocalFilesystemProvider()
    await provider.list_new_files(str(source))  # first sighting

    posted: list[str] = []
    reads: list[str] = []
    orig_read = provider.read_file

    async def _read(ref: object) -> bytes:
        reads.append(slug)
        return await orig_read(ref)  # pyright: ignore[reportArgumentType]

    async def _post(**kwargs: object) -> None:
        posted.append(slug)
        outcome = statuses.pop(0)
        if outcome == "422":
            raise watch.WorkflowNotRunnableError("nope")
        if outcome == "500":
            raise watch.TriggerFailedError("trigger failed (HTTP 500)")

    monkeypatch.setattr(provider, "read_file", _read)
    monkeypatch.setattr("src.cli.watch._trigger_workflow", _post)
    await watch.poll_once(provider, config)
    return posted, reads


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    from src.cli import watch

    c = _Clock()
    monkeypatch.setattr(watch, "_BACKOFF", watch.SlugBackoff(clock=c))
    return c


async def test_422_backs_off_the_slug_so_the_next_poll_does_not_read_or_post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: _Clock
) -> None:
    posted, reads = await _poll_once(monkeypatch, tmp_path, statuses=["422"])
    assert (posted, reads) == (["my-workflow"], ["my-workflow"])
    clock.now += 0.5  # inside the 1 s window (poll interval)
    posted, reads = await _poll_once(monkeypatch, tmp_path, statuses=[])
    assert posted == [] and reads == []
    assert (tmp_path / "in" / "note.txt").exists()


async def test_the_slug_is_retried_once_the_window_has_passed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: _Clock
) -> None:
    await _poll_once(monkeypatch, tmp_path, statuses=["422"])
    clock.now += 1.0
    posted, _ = await _poll_once(monkeypatch, tmp_path, statuses=["422"])
    assert posted == ["my-workflow"]


async def test_consecutive_422s_double_the_window_up_to_the_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: _Clock
) -> None:
    from src.cli import watch

    windows: list[float] = []
    for _ in range(14):
        await _poll_once(monkeypatch, tmp_path, statuses=["422"])
        windows.append(watch._BACKOFF.retry_at("my-workflow") - clock.now)  # pyright: ignore[reportPrivateUsage]
        clock.now = watch._BACKOFF.retry_at("my-workflow")  # pyright: ignore[reportPrivateUsage]
    assert windows[:4] == [1.0, 2.0, 4.0, 8.0]
    assert max(windows) == watch.BACKOFF_MAX_SECONDS
    assert windows[-1] == watch.BACKOFF_MAX_SECONDS


async def test_a_success_resets_the_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: _Clock
) -> None:
    from src.cli import watch

    for _ in range(3):
        await _poll_once(monkeypatch, tmp_path, statuses=["422"])
        clock.now = watch._BACKOFF.retry_at("my-workflow")  # pyright: ignore[reportPrivateUsage]
    await _poll_once(monkeypatch, tmp_path, statuses=["ok"])
    # Next 422 starts again at the poll interval, not at 8 s.
    (tmp_path / "in").mkdir(exist_ok=True)
    await _poll_once(monkeypatch, tmp_path, statuses=["422"])
    assert watch._BACKOFF.retry_at("my-workflow") - clock.now == 1.0  # pyright: ignore[reportPrivateUsage]


async def test_a_server_error_resets_the_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: _Clock
) -> None:
    from src.cli import watch

    await _poll_once(monkeypatch, tmp_path, statuses=["422"])
    clock.now = watch._BACKOFF.retry_at("my-workflow")  # pyright: ignore[reportPrivateUsage]
    await _poll_once(monkeypatch, tmp_path, statuses=["500"])
    assert not watch._BACKOFF.blocked("my-workflow")  # pyright: ignore[reportPrivateUsage]
    await _poll_once(monkeypatch, tmp_path, statuses=["422"])
    assert watch._BACKOFF.retry_at("my-workflow") - clock.now == 1.0  # pyright: ignore[reportPrivateUsage]


async def test_backoff_is_per_slug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: _Clock
) -> None:
    await _poll_once(monkeypatch, tmp_path / "a", statuses=["422"], slug="wf-a")
    posted, _ = await _poll_once(monkeypatch, tmp_path / "b", statuses=["ok"], slug="wf-b")
    assert posted == ["wf-b"]
    posted, _ = await _poll_once(monkeypatch, tmp_path / "a", statuses=[], slug="wf-a")
    assert posted == []


async def test_backoff_logs_entering_and_recovering_without_file_contents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clock: _Clock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    with caplog.at_level(logging.INFO, logger="src.cli.watch"):
        await _poll_once(monkeypatch, tmp_path, statuses=["422"])
        clock.now += 1.0
        await _poll_once(monkeypatch, tmp_path, statuses=["ok"])
    msgs = [r.getMessage() for r in caplog.records]
    assert len([m for m in msgs if "backing off" in m]) == 1
    assert len([m for m in msgs if "recovered" in m]) == 1
    assert not any("content" in m for m in msgs)
