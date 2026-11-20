import os

import pytest
from fastapi import FastAPI
from sqlalchemy.engine import make_url

from src.db.url import to_sqlalchemy_url

pytestmark = pytest.mark.integration


async def test_app_engine_points_at_the_test_database(app: FastAPI) -> None:
    expected = make_url(to_sqlalchemy_url(os.environ["TEST_DATABASE_URL"]))
    actual = app.state.db_engine.url
    assert actual.host == expected.host
    assert actual.database == expected.database
