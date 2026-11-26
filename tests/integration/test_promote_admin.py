"""frontend/scripts/promote_admin.py, run as a subprocess the way the E2E admin spec runs it."""

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from src.db.models import User, UserRole
from tests.integration._rows import Rows

pytestmark = pytest.mark.integration

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "frontend" / "scripts" / "promote_admin.py"


def _run(email: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "DATABASE_URL": os.environ["TEST_DATABASE_URL"]}
    return subprocess.run(
        [sys.executable, str(_SCRIPT), email],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


async def test_promote_admin_flips_a_member_to_admin(rows: Rows, tmp_path: Path) -> None:
    email = f"promote-{uuid.uuid4().hex[:8]}@example.com"
    user = await rows.add(User(email=email, role=UserRole.MEMBER))
    try:
        result = _run(email, tmp_path)
        assert result.returncode == 0, result.stderr
        after = await rows.get(User, user.id)
        assert after is not None
        assert after.role == UserRole.ADMIN
    finally:
        await rows.remove(User, User.id == user.id)


async def test_promote_admin_unknown_email_exits_non_zero(tmp_path: Path) -> None:
    result = _run(f"nobody-{uuid.uuid4().hex[:8]}@example.com", tmp_path)
    assert result.returncode != 0
    assert "user not found" in result.stderr
