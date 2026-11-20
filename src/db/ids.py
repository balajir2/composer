"""Primary-key generation for rows created through SQLAlchemy.

Prisma generated cuid ids in Python. New rows use UUIDv7 instead: a standard,
time-ordered format (better index locality). Both are stored as text, so old
and new ids coexist in the same columns. On Python 3.15+, replace the body
with the standard library's ``uuid.uuid7()``.
"""

import os
import time
import uuid

_RAND_BITS = 80
_TIMESTAMP_MASK = (1 << 48) - 1


def new_id() -> str:
    """Return a new UUIDv7 (RFC 9562) as its canonical 36-character string.

    Ids are time-ordered across milliseconds but not strictly monotonic within
    one, so never rely on id order for event ordering.
    """
    timestamp_ms = time.time_ns() // 1_000_000
    value = ((timestamp_ms & _TIMESTAMP_MASK) << _RAND_BITS) | int.from_bytes(os.urandom(10), "big")
    value = (value & ~(0xF << 76)) | (0x7 << 76)  # version 7
    value = (value & ~(0x3 << 62)) | (0x2 << 62)  # RFC 4122 variant
    return str(uuid.UUID(int=value))
