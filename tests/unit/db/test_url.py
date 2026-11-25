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


def test_schema_public_is_dropped_and_other_params_kept() -> None:
    url = to_sqlalchemy_url("postgresql://u:p@h/db?schema=public&sslmode=require")
    assert url == "postgresql+psycopg://u:p@h/db?sslmode=require"


def test_non_public_schema_is_refused() -> None:
    with pytest.raises(ValueError, match="Only the public schema"):
        to_sqlalchemy_url("postgresql://u:p@h/db?schema=tenant1")


def test_percent_encoded_password_is_unchanged() -> None:
    url = to_sqlalchemy_url("postgresql://u:p%40ss%2Fw@h/db")
    assert url == "postgresql+psycopg://u:p%40ss%2Fw@h/db"


def test_no_query_string_gives_no_trailing_question_mark() -> None:
    assert to_sqlalchemy_url("postgresql://u:p@h/db") == "postgresql+psycopg://u:p@h/db"


def test_only_prisma_params_gives_no_trailing_question_mark() -> None:
    url = to_sqlalchemy_url("postgresql://u:p@h/db?pgbouncer=true&connection_limit=5")
    assert url == "postgresql+psycopg://u:p@h/db"


def test_space_in_value_stays_percent_20() -> None:
    url = to_sqlalchemy_url("postgresql://u:p@h/db?options=-c%20search_path%3Dx")
    assert url == "postgresql+psycopg://u:p@h/db?options=-c%20search_path%3Dx"


def test_libpq_url_strips_prisma_params_and_uses_plain_scheme() -> None:
    from src.db.url import to_libpq_url

    url = to_libpq_url(
        "postgres://u:p@h/db?schema=public&sslmode=require&pgbouncer=true&connection_limit=5"
    )
    assert url == "postgresql://u:p@h/db?sslmode=require"
