"""Flip a user to admin role by email.  Used by Playwright admin tests."""

import asyncio
import sys

from prisma import Prisma


async def main(email: str) -> None:
    db = Prisma()
    await db.connect()
    try:
        user = await db.user.find_unique(where={"email": email})
        if user is None:
            raise SystemExit(f"user not found: {email}")
        await db.user.update(where={"id": user.id}, data={"role": "admin"})
    finally:
        await db.disconnect()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python promote_admin.py <email>")
    asyncio.run(main(sys.argv[1]))
