from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import build_sessionmaker, get_session

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


class _FakeSessionmaker:
    def __init__(self) -> None:
        self.closed = False

    def __call__(self) -> "_FakeSessionmaker":
        return self

    async def __aenter__(self) -> str:
        return "session"

    async def __aexit__(self, *_exc: Any) -> None:
        self.closed = True


def _request(state: SimpleNamespace) -> Request:
    return cast("Request", SimpleNamespace(app=SimpleNamespace(state=state)))


async def test_get_session_yields_a_session_and_closes_it() -> None:
    sessionmaker = _FakeSessionmaker()
    generator = get_session(_request(SimpleNamespace(db_sessionmaker=sessionmaker)))
    session = await anext(generator)
    assert session == "session"
    with pytest.raises(StopAsyncIteration):
        await anext(generator)
    assert sessionmaker.closed is True


async def test_get_session_fails_clearly_when_lifespan_did_not_run() -> None:
    generator = get_session(_request(SimpleNamespace()))
    with pytest.raises(RuntimeError, match="did lifespan run"):
        await anext(generator)


def test_sessions_do_not_expire_objects_on_commit() -> None:
    from src.config import Settings
    from src.db.engine import build_engine

    engine = build_engine(Settings(database_url="postgresql://u:p@localhost/db"))
    sessionmaker = build_sessionmaker(engine)
    assert sessionmaker.kw["expire_on_commit"] is False
    assert sessionmaker.class_ is AsyncSession


async def test_get_session_closes_session_when_the_request_raises() -> None:
    sessionmaker = _FakeSessionmaker()
    generator = cast(
        "AsyncGenerator[AsyncSession]",
        get_session(_request(SimpleNamespace(db_sessionmaker=sessionmaker))),
    )
    await anext(generator)
    with pytest.raises(ValueError, match="boom"):
        await generator.athrow(ValueError("boom"))
    assert sessionmaker.closed is True
