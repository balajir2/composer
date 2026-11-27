"""Prisma and Node.js stay out of the backend build and the pipelines (SQLAlchemy migration
phase 6). The frontend jobs' actions/setup-node is fine: it is not the backend image."""

import re
from pathlib import Path

import yaml

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
    for marker in ("nodejs", "nodesource", "setup_20", "npm ", "from node"):
        assert marker not in text, marker


def test_no_builder_apt_install_adds_node() -> None:
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    builder = text.split(" AS builder", 1)[1].split(" AS runtime", 1)[0]
    installs = [ln for ln in builder.splitlines() if "apt-get install" in ln]
    assert not [ln for ln in installs if "node" in ln.lower()]


def test_security_refresh_arg_is_kept() -> None:
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^ARG SECURITY_REFRESH=\d{4}-\d{2}-\d{2}$", text, re.MULTILINE)
    assert 'echo "security refresh ${SECURITY_REFRESH}"' in text


def _runs(workflow: str, job: str | None) -> list[str]:
    doc = yaml.safe_load((ROOT / ".github/workflows" / workflow).read_text(encoding="utf-8"))
    jobs = doc["jobs"] if job is None else {job: doc["jobs"][job]}
    return [str(s.get("run", "")) for j in jobs.values() for s in j["steps"]]


def test_every_job_that_prepares_a_database_uses_alembic_only() -> None:
    for workflow, job in (
        ("ci.yml", "lint-typecheck-test"),
        ("ci.yml", "e2e-smoke"),
        ("live-provider-smoke.yml", None),
    ):
        runs = _runs(workflow, job)
        assert "uv run alembic upgrade head" in runs, (workflow, job)
