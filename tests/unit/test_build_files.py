"""Prisma and Node.js stay out of the backend build and the pipelines (SQLAlchemy migration
phase 6). The frontend jobs' actions/setup-node is fine: it is not the backend image."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILD_FILES = [
    ROOT / "Dockerfile",
    ROOT / ".dockerignore",
    *sorted((ROOT / ".github/workflows").glob("*.yml")),
]


def test_no_build_or_pipeline_file_mentions_prisma() -> None:
    offenders = [p.name for p in BUILD_FILES if "prisma" in p.read_text(encoding="utf-8").lower()]
    assert offenders == []


def test_backend_image_installs_no_node_toolchain() -> None:
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8").lower()
    for marker in ("nodesource", "nodejs", "apt-get install -y --no-install-recommends nodejs"):
        assert marker not in text


def test_security_refresh_arg_is_kept() -> None:
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^ARG SECURITY_REFRESH=\d{4}-\d{2}-\d{2}$", text, re.MULTILINE)
    assert 'echo "security refresh ${SECURITY_REFRESH}"' in text


def test_every_workflow_that_prepares_a_database_uses_alembic_only() -> None:
    for name in ("ci.yml", "live-provider-smoke.yml"):
        text = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
        assert "uv run alembic upgrade head" in text, name
