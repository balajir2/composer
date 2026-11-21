from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy.exc import OperationalError

from src.db import runtime
from src.db.lifespan import sqlalchemy_lifespan


class _FakeEngine:
    def __init__(self) -> None:
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True


def _patch(monkeypatch: pytest.MonkeyPatch, engine: _FakeEngine, *, fail: bool) -> None:
    async def fake_connect(*_args: Any) -> None:
        if fail:
            raise OperationalError("SELECT 1", {}, Exception("down"))

    def fake_build_engine(_settings: object) -> _FakeEngine:
        return engine

    def fake_build_sessionmaker(_engine: object) -> object:
        return object()

    monkeypatch.setattr("src.db.lifespan.build_engine", fake_build_engine)
    monkeypatch.setattr("src.db.lifespan.connect_with_retry", fake_connect)
    monkeypatch.setattr("src.db.lifespan.build_sessionmaker", fake_build_sessionmaker)


async def test_lifespan_disposes_engine_when_connect_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _FakeEngine()
    _patch(monkeypatch, engine, fail=True)
    app = FastAPI()
    with pytest.raises(OperationalError):
        async with sqlalchemy_lifespan(app):
            pass
    assert engine.disposed is True
    assert not hasattr(app.state, "db_engine")


async def test_lifespan_attaches_state_and_cleans_up(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _FakeEngine()
    _patch(monkeypatch, engine, fail=False)
    app = FastAPI()
    async with sqlalchemy_lifespan(app):
        assert app.state.db_engine is engine
        assert hasattr(app.state, "db_sessionmaker")
        assert engine.disposed is False
    assert engine.disposed is True
    assert not hasattr(app.state, "db_engine")
    assert not hasattr(app.state, "db_sessionmaker")


async def test_lifespan_registers_sessionmaker_for_non_request_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _FakeEngine()
    _patch(monkeypatch, engine, fail=False)
    app = FastAPI()
    runtime.register_sessionmaker(None)
    async with sqlalchemy_lifespan(app):
        assert runtime.get_registered_sessionmaker() is app.state.db_sessionmaker
    assert runtime.get_registered_sessionmaker() is None


async def test_lifespan_unregisters_when_the_body_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _FakeEngine()
    _patch(monkeypatch, engine, fail=False)
    app = FastAPI()
    runtime.register_sessionmaker(None)
    with pytest.raises(ValueError):
        async with sqlalchemy_lifespan(app):
            raise ValueError("boom")
    assert runtime.get_registered_sessionmaker() is None
