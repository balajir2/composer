"""`composer checkpoints copy` with every collaborator faked."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import pytest

from src.checkpoints.copy import CopyReport
from src.cli import checkpoints as cli
from src.db.repos import checkpoints as checkpoints_repo
from src.db.repos import executions as executions_repo


@dataclass
class _Rec:
    status: str
    thread_id: str


class _Session:
    async def __aenter__(self) -> "_Session":
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None


@pytest.fixture
def copies(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    done: list[tuple[str, str]] = []
    records = {
        "e-run": _Rec("running", "t-run"),
        "e-wait": _Rec("waiting_approval", "t-wait"),
        "e-boom": _Rec("waiting_approval", "t-boom"),
    }

    @asynccontextmanager
    async def sm() -> AsyncGenerator[Any]:
        yield lambda: _Session()

    @asynccontextmanager
    async def saver(_settings: Any) -> AsyncGenerator[object]:
        yield object()

    async def get(_s: Any, eid: str) -> _Rec | None:
        return records.get(eid)

    async def waiting(_s: Any) -> list[tuple[str, str]]:
        return [("e-wait", "t-wait"), ("e-w2", "t-w2")]

    async def namespaces(_s: Any, _t: str) -> list[str]:
        return [""]

    async def fake_copy(
        _src: Any, _dst: Any, thread_id: str, *, to: str, namespaces: list[str]
    ) -> CopyReport:
        if thread_id == "t-boom":
            raise RuntimeError("secret checkpoint contents")
        done.append((thread_id, to))
        return CopyReport(thread_id, 2, 3)

    async def no_ids(_s: Any, _t: str, ns: list[str]) -> dict[str, str | None]:
        return dict.fromkeys(ns)

    monkeypatch.setattr(cli, "_newest_ids", no_ids)
    monkeypatch.setattr(cli, "_sessionmaker", sm)
    monkeypatch.setattr(cli, "open_one_shot_saver", saver)
    monkeypatch.setattr(cli, "copy_thread", fake_copy)
    monkeypatch.setattr(executions_repo, "get", get)
    monkeypatch.setattr(checkpoints_repo, "waiting_threads", waiting)
    monkeypatch.setattr(checkpoints_repo, "legacy_namespaces", namespaces)
    monkeypatch.setattr(checkpoints_repo, "saver_namespaces", namespaces)
    return done


async def test_refuses_running_without_force(
    copies: list[tuple[str, str]], capsys: pytest.CaptureFixture[str]
) -> None:
    assert await cli.checkpoints_copy("postgres", ["e-run"], False, False) == 2
    out = capsys.readouterr().out
    assert "e-run" in out and "running" in out
    assert copies == []


async def test_force_copies(copies: list[tuple[str, str]]) -> None:
    assert await cli.checkpoints_copy("legacy", ["e-run"], False, True) == 0
    assert copies == [("t-run", "legacy")]


async def test_all_waiting(
    copies: list[tuple[str, str]], capsys: pytest.CaptureFixture[str]
) -> None:
    assert await cli.checkpoints_copy("postgres", [], True, False) == 0
    assert copies == [("t-wait", "postgres"), ("t-w2", "postgres")]
    assert "copied e-wait: 2 checkpoints, 3 writes" in capsys.readouterr().out


async def test_failure_prints_class_not_message(
    copies: list[tuple[str, str]], capsys: pytest.CaptureFixture[str]
) -> None:
    assert await cli.checkpoints_copy("postgres", ["e-boom", "e-wait"], False, False) == 1
    out = capsys.readouterr().out
    assert "RuntimeError" in out and "secret" not in out
    assert copies == [("t-wait", "postgres")]


async def test_pre_loop_failure_prints_class_only(
    copies: list[tuple[str, str]],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def get(_s: Any, _eid: str) -> Any:
        raise ConnectionError("host=db.internal user=secret")

    monkeypatch.setattr(executions_repo, "get", get)
    assert await cli.checkpoints_copy("postgres", ["e-wait"], False, False) == 1
    out = capsys.readouterr().out
    assert "ConnectionError" in out and "secret" not in out


async def test_zero_checkpoints_fails_unless_forced(
    copies: list[tuple[str, str]],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def empty(*_a: Any, **_k: Any) -> CopyReport:
        return CopyReport("t", 0, 0)

    monkeypatch.setattr(cli, "copy_thread", empty)
    assert await cli.checkpoints_copy("postgres", ["e-wait"], False, False) == 1
    assert "0 checkpoints" in capsys.readouterr().out
    assert await cli.checkpoints_copy("postgres", ["e-wait"], False, True) == 0


async def test_refuses_when_target_is_ahead_unless_forced(
    copies: list[tuple[str, str]],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    official = object()

    async def newest(saver: Any, _t: str, _ns: list[str]) -> dict[str, str | None]:
        return {"": "9" if saver is official else "1"}

    @asynccontextmanager
    async def saver(_settings: Any) -> AsyncGenerator[object]:
        yield official

    monkeypatch.setattr(cli, "open_one_shot_saver", saver)
    monkeypatch.setattr(cli, "_newest_ids", newest)
    assert await cli.checkpoints_copy("legacy", ["e-wait"], False, False) == 0
    assert await cli.checkpoints_copy("postgres", ["e-wait"], False, False) == 2
    assert "ahead" in capsys.readouterr().out
    assert await cli.checkpoints_copy("postgres", ["e-wait"], False, True) == 0
