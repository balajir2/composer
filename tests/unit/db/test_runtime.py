from collections.abc import Iterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.db import runtime


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
