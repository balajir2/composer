"""Conservative recursive secret scrubbing for telemetry values."""

import re
from collections.abc import Mapping
from typing import cast
from urllib.parse import unquote, urlsplit

_SECRET_KEY = re.compile(
    r"(?:authorization|cookie|password|passwd|secret|credential|token|api[_-]?key|private[_-]?key|database[_-]?url)",
    re.I,
)
_URL = re.compile(r"[a-z][a-z0-9+.-]*://[^\s<>\"']+", re.I)
_COOKIE = re.compile(r"\b(?:set-cookie|cookie)\s*[:=][^\r\n]*", re.I)
_SECRET_TEXT = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{16,}|bearer\s+\S+|(?:api[_-]?key|authorization|password|secret|token)\s*[:=]\s*\S+)",
    re.I,
)


def redact(value: object, *, max_bytes: int) -> object:
    """Scrub values recursively; max_bytes caps each output text field in UTF-8."""
    limit = max(0, max_bytes)

    def cap(text: str) -> str:
        return text.encode("utf-8")[:limit].decode("utf-8", errors="ignore")

    marker = cap("[REDACTED]")

    def scrub_url(match: re.Match[str]) -> str:
        url = match.group()
        try:
            parsed = urlsplit(url)
            if parsed.username is not None or parsed.password is not None:
                return marker
            if parsed.scheme.lower() not in ("http", "https"):
                return marker
            if _SECRET_KEY.search(unquote(parsed.query)) or re.search(
                r"(?:sig|approval)", unquote(url), re.I
            ):
                return marker
        except ValueError:
            return marker
        return url

    def text(item: str) -> str:
        return cap(_SECRET_TEXT.sub(marker, _COOKIE.sub(marker, _URL.sub(scrub_url, item))))

    def visit(item: object, seen: set[int]) -> object:
        if isinstance(item, str):
            return text(item)
        if item is None or isinstance(item, (bool, int, float)):
            return item
        identity = id(item)
        if isinstance(item, (Mapping, list, tuple)):
            if identity in seen:
                return marker
            seen.add(identity)
            try:
                if isinstance(item, Mapping):
                    result: dict[str, object] = {}
                    for key, child in cast("Mapping[object, object]", item).items():
                        # Never stringify arbitrary objects/bytes as keys. Dropping
                        # them avoids collisions with a literal redaction marker.
                        if not isinstance(key, str):
                            continue
                        cleaned_key = text(key)
                        if cleaned_key in result or _SECRET_KEY.search(key):
                            result[cleaned_key] = marker
                        else:
                            result[cleaned_key] = visit(child, seen)
                    return result
                return [visit(v, seen) for v in cast("list[object] | tuple[object, ...]", item)]
            finally:
                seen.remove(identity)
        return marker

    return visit(value, set())
