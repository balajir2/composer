from src.config import Settings


def test_sqlalchemy_pool_defaults_are_small_while_prisma_also_holds_connections() -> None:
    settings = Settings()
    assert settings.db_pool_size == 2
    assert settings.db_max_overflow == 3
