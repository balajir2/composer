from typing import Any, cast

import pytest

from src.checkpoints.schema import SAVER_MIGRATION_COUNT, assert_saver_schema_current


class _Cursor:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row

    async def fetchone(self) -> dict[str, Any] | None:
        return self._row


class _Conn:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row

    async def execute(self, query: str) -> _Cursor:
        assert "checkpoint_migrations" in query
        return _Cursor(self._row)


async def _check(row: dict[str, Any] | None) -> None:
    await assert_saver_schema_current(cast("Any", _Conn(row)))


def test_count_is_ten() -> None:
    assert SAVER_MIGRATION_COUNT == 10


async def test_current_version_passes() -> None:
    await _check({"v": 9})


@pytest.mark.parametrize("row", [{"v": 8}, {"v": None}, None])
async def test_other_versions_raise_naming_found_and_expected(row: dict[str, Any] | None) -> None:
    with pytest.raises(RuntimeError) as exc:
        await _check(row)
    found = None if row is None else row["v"]
    message = str(exc.value)
    assert "9" in message
    assert str(found) in message
    assert "postgresql" not in message
