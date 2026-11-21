from collections.abc import Iterator
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.db import runtime

if TYPE_CHECKING:
    from starlette.requests import HTTPConnection


@pytest.fixture(autouse=True)
def _clean_registry() -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
    runtime.register_sessionmaker(None)
    yield
    runtime.register_sessionmaker(None)


async def test_session_scope_without_registration_raises() -> None:
    with pytest.raises(RuntimeError, match="no SQLAlchemy sessionmaker registered"):
        async with runtime.session_scope():
            pass


async def test_registered_sessionmaker_is_returned_and_used() -> None:
    engine = create_async_engine("postgresql+psycopg://u:p@localhost:1/x")
    sm = async_sessionmaker(engine)
    runtime.register_sessionmaker(sm)
    assert runtime.get_registered_sessionmaker() is sm
    async with runtime.session_scope() as session:
        assert isinstance(session, AsyncSession)
    await engine.dispose()


async def test_unregister_of_a_different_sessionmaker_keeps_the_registered_one() -> None:
    engine = create_async_engine("postgresql+psycopg://u:p@localhost:1/x")
    mine, other = async_sessionmaker(engine), async_sessionmaker(engine)
    runtime.register_sessionmaker(mine)
    runtime.unregister_sessionmaker(other)
    assert runtime.get_registered_sessionmaker() is mine
    runtime.unregister_sessionmaker(mine)
    assert runtime.get_registered_sessionmaker() is None
    await engine.dispose()


def test_request_sessionmaker_reads_app_state() -> None:
    sm = object()
    with_sm = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db_sessionmaker=sm)))
    without = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    assert runtime.request_sessionmaker(cast("HTTPConnection", with_sm)) is sm
    assert runtime.request_sessionmaker(cast("HTTPConnection", without)) is None
