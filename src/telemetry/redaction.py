"""Conservative recursive secret scrubbing for telemetry values."""

import re
from collections.abc import Mapping

_SECRET_KEY = re.compile(
    r"(?:authorization|cookie|password|passwd|secret|credential|token|api[_-]?key|private[_-]?key|database[_-]?url)",
    re.I,
)
_SECRET_TEXT = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{16,}|bearer\s+\S+|(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://\S+|https?://\S*(?:token|signature|sig|approval)\S*|(?:cookie|set-cookie|api[_-]?key|authorization|password|secret|token)\s*[:=]\s*\S+)",
    re.I,
)


def redact(value: object, *, max_bytes: int) -> object:
    """Remove recognizable secrets and cap each textual value in UTF-8 bytes."""

    def visit(item: object, seen: set[int]) -> object:
        if isinstance(item, str):
            cleaned = _SECRET_TEXT.sub("[REDACTED]", item)
            return cleaned.encode("utf-8")[: max(0, max_bytes)].decode("utf-8", errors="ignore")
        if item is None or isinstance(item, (bool, int, float)):
            return item
        if isinstance(item, (Mapping, list, tuple)):
            if id(item) in seen:
                return "[REDACTED]"
            seen.add(id(item))
            try:
                if isinstance(item, Mapping):
                    return {
                        str(k): "[REDACTED]" if _SECRET_KEY.search(str(k)) else visit(v, seen)
                        for k, v in item.items()
                    }
                return [visit(v, seen) for v in item]
            finally:
                seen.remove(id(item))
        return "[REDACTED]"

    return visit(value, set())
