"""libpq parameters that put an upper bound on a connection's network waits.

Every Postgres connection Composer opens (SQLAlchemy's engine, the checkpointer's pool, the
LISTEN connection) passes these. Without them a peer that stops answering without closing the
socket (a Neon compute restart, a dropped NAT entry) leaves the client waiting on the kernel's
defaults: no connect bound beyond psycopg's 130 s, no keepalive probes on an idle connection,
and about 15 minutes of TCP retransmissions on one with unacknowledged data.

These are backstops. Waits that must finish quickly (startup) also carry an ``asyncio.timeout``.
"""

import math

from src.config import Settings

# An idle connection to a dead peer is detected after 30 + 3 * 10 = 60 s.
KEEPALIVES_IDLE_SECONDS = 30
KEEPALIVES_INTERVAL_SECONDS = 10
KEEPALIVES_COUNT = 3
# Sent data unacknowledged this long closes the connection (Linux TCP_USER_TIMEOUT; libpq
# ignores it where the OS lacks the option). A slow query is not affected: its request is
# acknowledged, only the answer is pending.
TCP_USER_TIMEOUT_MS = 30_000


def bounded_connect_params(settings: Settings) -> dict[str, int]:
    """``connect_timeout`` plus TCP keepalive and user-timeout parameters for psycopg."""
    return {
        # libpq takes whole seconds and treats anything below 2 as 2.
        "connect_timeout": max(2, math.ceil(settings.db_connect_timeout_seconds)),
        "keepalives": 1,
        "keepalives_idle": KEEPALIVES_IDLE_SECONDS,
        "keepalives_interval": KEEPALIVES_INTERVAL_SECONDS,
        "keepalives_count": KEEPALIVES_COUNT,
        "tcp_user_timeout": TCP_USER_TIMEOUT_MS,
    }
