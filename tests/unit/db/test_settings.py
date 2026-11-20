import pytest
from pydantic import ValidationError

from src.config import Settings, get_settings


def test_sqlalchemy_pool_defaults_are_small_while_prisma_also_holds_connections() -> None:
    assert Settings.model_fields["db_pool_size"].default == 2
    assert Settings.model_fields["db_max_overflow"].default == 3


def test_connect_requires_at_least_one_attempt() -> None:
    with pytest.raises(ValidationError):
        Settings(db_connect_max_attempts=0)


def test_cache_clear_makes_get_settings_see_a_changed_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host-a/db_a")
    get_settings.cache_clear()
    try:
        assert get_settings().database_url == "postgresql://u:p@host-a/db_a"
        monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host-b/db_b")
        assert get_settings().database_url == "postgresql://u:p@host-a/db_a"
        get_settings.cache_clear()
        assert get_settings().database_url == "postgresql://u:p@host-b/db_b"
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
