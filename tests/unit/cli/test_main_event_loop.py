"""psycopg async cannot use the Windows Proactor loop: CLI commands run on a selector loop there."""

import asyncio
import sys

import pytest

from src.cli import main as cli_main


def test_win32_runs_on_a_selector_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")

    async def which() -> type:
        return type(asyncio.get_running_loop())

    assert issubclass(cli_main._run(which()), asyncio.SelectorEventLoop)  # pyright: ignore[reportPrivateUsage]
