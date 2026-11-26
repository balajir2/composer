"""The checkpointer pool's connections carry the libpq network bounds (startup-hang fix)."""

from src.checkpoints.saver import SAVER_CONNECTION_KWARGS, build_saver_pool, saver_connection_kwargs
from src.config import Settings
from src.db.libpq import bounded_connect_params


def _settings() -> Settings:
    return Settings(database_url="postgresql://u:p@h:5432/db", db_connect_timeout_seconds=3.0)


def test_kwargs_keep_the_saver_requirements_and_add_the_bounds() -> None:
    kwargs = saver_connection_kwargs(_settings())
    assert kwargs == {**SAVER_CONNECTION_KWARGS, **bounded_connect_params(_settings())}
    assert kwargs["connect_timeout"] == 3
    assert kwargs["keepalives"] == 1
    assert kwargs["autocommit"] is True
    assert kwargs["prepare_threshold"] is None


def test_the_pool_connects_with_them() -> None:
    pool = build_saver_pool(_settings())
    assert pool.kwargs == saver_connection_kwargs(_settings())
