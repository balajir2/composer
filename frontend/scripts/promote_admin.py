"""Flip a user to admin role by email.  Used by Playwright admin tests."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root, for `src`

from src.config import get_settings
from src.db.models import UserRole
from src.db.repos import users as users_repo
from src.db.runtime import one_shot_session


async def promote(email: str) -> None:
    async with one_shot_session(get_settings()) as session:
        user = await users_repo.get_by_email(session, email)
        if user is None:
            raise SystemExit(f"user not found: {email}")
        await users_repo.set_role(session, user.id, UserRole.ADMIN)
        await session.commit()


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: python promote_admin.py <email>")
    if sys.platform == "win32":
        # psycopg async cannot use Windows' default Proactor loop.
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            runner.run(promote(sys.argv[1]))
    else:
        asyncio.run(promote(sys.argv[1]))


if __name__ == "__main__":
    main()
