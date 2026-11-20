import pytest
from pydantic import ValidationError

from src.config import Settings


def test_sqlalchemy_pool_defaults_are_small_while_prisma_also_holds_connections() -> None:
    assert Settings.model_fields["db_pool_size"].default == 2
    assert Settings.model_fields["db_max_overflow"].default == 3


def test_connect_requires_at_least_one_attempt() -> None:
    with pytest.raises(ValidationError):
        Settings(db_connect_max_attempts=0)
