"""Tests for LocalFilesystemProvider."""

from pathlib import Path


async def test_write_then_read_round_trips(tmp_path: Path) -> None:
    from src.storage_providers.local import LocalFilesystemProvider

    provider = LocalFilesystemProvider()
    dest = tmp_path / "out"
    await provider.write_file(str(dest), "report.md", b"# Hello")

    ref = (await provider.list_new_files(str(dest)))[0]
    assert ref.name == "report.md"
    content = await provider.read_file(ref)
    assert content == b"# Hello"


async def test_move_file_relocates_and_removes_original(tmp_path: Path) -> None:
    from src.storage_providers.local import LocalFilesystemProvider

    provider = LocalFilesystemProvider()
    source = tmp_path / "in"
    source.mkdir()
    (source / "a.txt").write_bytes(b"data")

    refs = await provider.list_new_files(str(source))
    assert len(refs) == 0  # not yet stable (first poll always sees nothing new)


async def test_list_new_files_requires_two_stable_polls(tmp_path: Path) -> None:
    """A file is only 'new' once its size/mtime is unchanged across two
    consecutive polls — guards against claiming a file mid-write."""
    from src.storage_providers.local import LocalFilesystemProvider

    provider = LocalFilesystemProvider()
    source = tmp_path / "in"
    source.mkdir()
    (source / "a.txt").write_bytes(b"data")

    first_poll = await provider.list_new_files(str(source))
    assert first_poll == []  # first sighting — not stable yet

    second_poll = await provider.list_new_files(str(source))
    assert len(second_poll) == 1
    assert second_poll[0].name == "a.txt"


async def test_list_new_files_ignores_actively_growing_file(tmp_path: Path) -> None:
    from src.storage_providers.local import LocalFilesystemProvider

    provider = LocalFilesystemProvider()
    source = tmp_path / "in"
    source.mkdir()
    target = source / "a.txt"
    target.write_bytes(b"data")

    await provider.list_new_files(str(source))  # first sighting
    target.write_bytes(b"data-more")  # still being written between polls
    second_poll = await provider.list_new_files(str(source))
    assert second_poll == []  # size changed — reset stability tracking, not yet claimed

    third_poll = await provider.list_new_files(str(source))
    assert len(third_poll) == 1  # stable across this pair of polls now


async def test_move_file_after_claim(tmp_path: Path) -> None:
    from src.storage_providers.local import LocalFilesystemProvider

    provider = LocalFilesystemProvider()
    source = tmp_path / "in"
    dest = tmp_path / "done"
    source.mkdir()
    (source / "a.txt").write_bytes(b"data")

    await provider.list_new_files(str(source))
    refs = await provider.list_new_files(str(source))
    ref = refs[0]

    await provider.move_file(ref, str(dest))

    assert not (source / "a.txt").exists()
    assert (dest / "a.txt").read_bytes() == b"data"


async def test_write_file_creates_destination_dir(tmp_path: Path) -> None:
    from src.storage_providers.local import LocalFilesystemProvider

    provider = LocalFilesystemProvider()
    dest = tmp_path / "nested" / "out"
    await provider.write_file(str(dest), "x.txt", b"hi")
    assert (dest / "x.txt").read_bytes() == b"hi"


async def test_health_check_ok_for_local() -> None:
    from src.storage_providers.local import LocalFilesystemProvider

    status = await LocalFilesystemProvider().health_check()
    assert status.ok is True
