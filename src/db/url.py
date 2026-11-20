"""Turn Composer's DATABASE_URL (written for Prisma) into a SQLAlchemy URL."""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Query parameters Prisma understands but libpq/psycopg reject.
_PRISMA_ONLY_PARAMS = frozenset(
    {
        "schema",
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
    """Return ``database_url`` with the psycopg driver and libpq-only parameters."""
    parts = urlsplit(database_url)
    if parts.scheme not in ("postgresql", "postgres"):
        raise ValueError(f"Unsupported database URL scheme {parts.scheme!r}")
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key not in _PRISMA_ONLY_PARAMS
    ]
    return urlunsplit(
        ("postgresql+psycopg", parts.netloc, parts.path, urlencode(query), parts.fragment)
    )
