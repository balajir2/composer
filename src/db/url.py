"""Turn Composer's DATABASE_URL (written for Prisma) into a SQLAlchemy URL."""

from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

# Query parameters Prisma understands but libpq/psycopg reject.
_PRISMA_ONLY_PARAMS = frozenset(
    {
        "connection_limit",
        "pool_timeout",
        "pgbouncer",
        "socket_timeout",
        "statement_cache_size",
        "sslaccept",
        "sslidentity",
        "sslpassword",
    }
)


def to_sqlalchemy_url(database_url: str) -> str:
    """Return ``database_url`` with the psycopg driver and libpq-only parameters.

    Prisma's ``schema`` parameter is dropped only when it is ``public``; any other
    value raises ``ValueError`` because psycopg cannot select a schema that way.
    """
    parts = urlsplit(database_url)
    if parts.scheme not in ("postgresql", "postgres"):
        raise ValueError(f"Unsupported database URL scheme {parts.scheme!r}")
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    for key, value in pairs:
        if key == "schema" and value != "public":
            raise ValueError(
                f"Only the public schema is supported; DATABASE_URL has schema={value}"
            )
    query = [
        (key, value) for key, value in pairs if key != "schema" and key not in _PRISMA_ONLY_PARAMS
    ]
    return urlunsplit(
        (
            "postgresql+psycopg",
            parts.netloc,
            parts.path,
            urlencode(query, quote_via=quote),
            parts.fragment,
        )
    )


def to_libpq_url(database_url: str) -> str:
    """``database_url`` as a plain libpq URL (``postgresql://``) without Prisma-only parameters."""
    return to_sqlalchemy_url(database_url).replace("postgresql+psycopg://", "postgresql://", 1)
