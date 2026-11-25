"""`composer checkpoints copy` through main() against the test database."""

import os

import pytest

from src.cli.main import main
from src.config import get_settings

pytestmark = pytest.mark.integration


def test_unknown_execution_exits_2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    get_settings.cache_clear()
    try:
        with pytest.raises(SystemExit) as exc:
            main(["checkpoints", "copy", "--to", "postgres", "--execution", "no-such-execution"])
    finally:
        get_settings.cache_clear()
    assert exc.value.code == 2
