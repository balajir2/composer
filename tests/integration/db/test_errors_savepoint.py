import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.errors import AlreadyExists, add_or_already_exists
from src.db.models.users import User

pytestmark = pytest.mark.integration


async def test_duplicate_leaves_the_session_usable(session: AsyncSession) -> None:
    await add_or_already_exists(session, User(email="dup@example.com"))
    with pytest.raises(AlreadyExists) as info:
        await add_or_already_exists(session, User(email="dup@example.com"))
    assert info.value.constraint == "users_email_key"

    assert (await session.execute(text("SELECT 1"))).scalar_one() == 1
    await add_or_already_exists(session, User(email="other@example.com"))
    emails = set((await session.execute(select(User.email))).scalars())
    assert {"dup@example.com", "other@example.com"} <= emails
