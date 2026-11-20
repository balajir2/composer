import pytest

from src.db.url import to_sqlalchemy_url


def test_switches_scheme_to_psycopg() -> None:
    url = to_sqlalchemy_url("postgresql://user:pw@host:5432/db")
    assert url == "postgresql+psycopg://user:pw@host:5432/db"


def test_accepts_short_postgres_scheme() -> None:
    assert to_sqlalchemy_url("postgres://u:p@h/db") == "postgresql+psycopg://u:p@h/db"


def test_keeps_libpq_parameters() -> None:
    url = to_sqlalchemy_url("postgresql://u:p@h/db?sslmode=require&channel_binding=require")
    assert url == "postgresql+psycopg://u:p@h/db?sslmode=require&channel_binding=require"


def test_drops_prisma_only_parameters() -> None:
    url = to_sqlalchemy_url(
        "postgresql://u:p@h/db?schema=public&sslmode=require&pgbouncer=true&connection_limit=5"
    )
    assert url == "postgresql+psycopg://u:p@h/db?sslmode=require"


def test_rejects_non_postgres_urls() -> None:
    with pytest.raises(ValueError, match="Unsupported database URL scheme"):
        to_sqlalchemy_url("mysql://u:p@h/db")
