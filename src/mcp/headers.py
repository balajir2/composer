"""Validation of an MCP server's custom request headers.

A header value with a CR, LF or NUL, or a character outside latin-1, makes the HTTP stack raise
an error whose text contains the value, which for an API key or bearer token would leak a secret into stored and returned error
messages. Everything here names a header only, never its value.
"""

import re
from collections.abc import Mapping

# RFC 7230 token: the characters allowed in a header field name.
_TOKEN = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_FORBIDDEN_VALUE_CHARS = ("\r", "\n", "\0")

# Hop-by-hop and framing headers belong to the HTTP client, never to configuration.
UNSAFE_HEADER_NAMES = frozenset(
    {
        "host",
        "content-length",
        "transfer-encoding",
        "connection",
        "keep-alive",
        "te",
        "trailer",
        "upgrade",
        "proxy-connection",
    }
)


def is_latin1(value: str) -> bool:
    """True when ``value`` can go on the wire: h11 encodes header values as latin-1."""
    try:
        value.encode("latin-1")
    except UnicodeEncodeError:
        return False
    return True


class InvalidHeaderError(ValueError):
    """A custom header that cannot be sent. ``name`` is the header; the value is never kept."""

    def __init__(self, name: str, reason: str) -> None:
        super().__init__(f"header {name!r}: {reason}")
        self.name = name
        self.reason = reason


def header_problem(name: str, value: str) -> str | None:
    """Why ``name: value`` cannot be sent, or None. The reason never contains the value."""
    stripped = name.strip()
    if not stripped or not _TOKEN.match(stripped):
        return "name is not a valid HTTP header name"
    if stripped.lower() in UNSAFE_HEADER_NAMES:
        return "this header is set by the HTTP client and cannot be customized"
    if any(ch in value for ch in _FORBIDDEN_VALUE_CHARS):
        return "value must not contain CR, LF or NUL characters"
    if not is_latin1(value):
        return "value must only contain Latin-1 (ISO 8859-1) characters"
    return None


def validate_custom_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Return ``headers`` with names and values stripped; raise InvalidHeaderError otherwise.

    Surrounding whitespace is removed (a pasted key often ends in a newline); a CR, LF or NUL
    left inside a value is rejected.
    """
    cleaned: dict[str, str] = {}
    for name, value in headers.items():
        stripped_value = value.strip()
        problem = header_problem(name, stripped_value)
        if problem:
            raise InvalidHeaderError(name.strip() or "<empty>", problem)
        cleaned[name.strip()] = stripped_value
    return cleaned


__all__ = [
    "UNSAFE_HEADER_NAMES",
    "InvalidHeaderError",
    "header_problem",
    "is_latin1",
    "validate_custom_headers",
]
