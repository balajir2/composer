from src.config import Settings


def test_sqlalchemy_pool_defaults_are_small_while_prisma_also_holds_connections() -> None:
    assert Settings.model_fields["db_pool_size"].default == 2
    assert Settings.model_fields["db_max_overflow"].default == 3
