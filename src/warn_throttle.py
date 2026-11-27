"""Log a recurring warning once per key per interval instead of on every poll."""

import time
from collections.abc import Callable


class WarnThrottle:
    """`allow(key)` is True the first time and again once `interval_seconds` have passed."""

    def __init__(
        self, interval_seconds: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._interval = interval_seconds
        self._clock = clock
        self._last: dict[str, float] = {}

    def allow(self, key: str) -> bool:
        now = self._clock()
        last = self._last.get(key)
        if last is not None and now - last < self._interval:
            return False
        self._last[key] = now
        return True


__all__ = ["WarnThrottle"]
