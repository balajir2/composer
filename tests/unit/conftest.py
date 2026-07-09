"""Shared fixtures for the `tests/unit/` tree.

Centralizes cross-file `lru_cache` isolation for `src.config.get_settings`.
Several unit test modules under `tests/unit/api/` monkeypatch `ENVIRONMENT`
(and other settings-affecting env vars) and call `get_settings.cache_clear()`
at setup, but not all of them clear it again on teardown. Without a shared
teardown, whichever such module happens to run last (an artifact of pytest's
collection order) leaves its mutated Settings instance cached indefinitely,
silently breaking later test modules that expect the development default
(e.g. the ADR-0015 dev-mode fallback). Clearing the cache after every test
in this tree removes the ordering dependency entirely.
"""

from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _reset_settings_cache_after_test() -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
    """Clear the get_settings() lru_cache after every unit test.

    Mirrors `tests/integration/test_embedded_auth.py`'s `_restore_settings_cache`
    fixture, applied process-wide across `tests/unit/` instead of a single module.
    """
    yield
    from src.config import get_settings

    get_settings.cache_clear()
