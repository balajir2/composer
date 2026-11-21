from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

from src.db.health import check_database

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine


class _Result:
    def __init__(self, value: object) -> None:
        self._value = value

    def scalar(self) -> object:
        return self._value

    def scalar_one_or_none(self) -> object:
        return self._value


class _Conn:
    async def execute(self, stmt: Any) -> _Result:
        return _Result(None)  # to_regclass('alembic_version') is NULL


class _Connect:
    async def __aenter__(self) -> _Conn:
        return _Conn()

    async def __aexit__(self, *exc: object) -> bool:
        return False


async def test_missing_alembic_version_table_is_reachable_but_not_current() -> None:
    engine = cast("AsyncEngine", SimpleNamespace(connect=lambda: _Connect()))
    health = await check_database(engine, timeout_seconds=1, expected_revision="x")
    assert health.reachable is True and health.schema_current is False and health.error is None
