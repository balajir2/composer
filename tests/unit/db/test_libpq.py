"""Every Postgres connection carries a connect bound and TCP keepalives (startup-hang fix)."""

from psycopg.conninfo import conninfo_to_dict, make_conninfo

from src.config import Settings
from src.db.libpq import bounded_connect_params


def _settings(timeout: float) -> Settings:
    return Settings(database_url="postgresql://u:p@h:5432/db", db_connect_timeout_seconds=timeout)


def test_params_bound_connect_and_detect_a_silent_peer() -> None:
    assert bounded_connect_params(_settings(3.0)) == {
        "connect_timeout": 3,
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 3,
        "tcp_user_timeout": 30_000,
    }


def test_connect_timeout_is_whole_seconds_and_at_least_two() -> None:
    assert bounded_connect_params(_settings(0.5))["connect_timeout"] == 2
    assert bounded_connect_params(_settings(3.2))["connect_timeout"] == 4


def test_params_are_valid_libpq_keywords() -> None:
    # make_conninfo validates keywords against the linked libpq; an unknown one raises.
    params = bounded_connect_params(_settings(3.0))
    merged = conninfo_to_dict(make_conninfo("postgresql://u:p@h:5432/db", **params))
    assert {k: merged[k] for k in params} == {k: str(v) for k, v in params.items()}
