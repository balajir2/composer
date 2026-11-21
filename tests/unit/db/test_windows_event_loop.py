"""psycopg's async mode cannot use Windows' default Proactor event loop.

tests/conftest.py switches tests to the selector loop on Windows so the
SQLAlchemy integration tests run locally (CI is Linux and unaffected).
"""

import asyncio
import sys

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only event loop rule")
async def test_tests_run_on_a_selector_event_loop_on_windows() -> None:
    assert isinstance(asyncio.get_running_loop(), asyncio.SelectorEventLoop)
