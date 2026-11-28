"""Guards on the production deploy workflow."""

from pathlib import Path
from typing import Any

import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/deploy-gcp.yml"


def test_production_deploys_are_serialised_and_never_cancelled() -> None:
    doc = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)

    concurrency = doc["concurrency"]

    assert concurrency["group"] == "deploy-gcp-production"
    # Cancelling a deploy mid-migration is worse than queueing the next one.
    assert concurrency["cancel-in-progress"] == "false"


def _backend_steps() -> list[dict[str, Any]]:
    doc = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    return next(
        j["steps"]
        for j in doc["jobs"].values()
        if any(s.get("name") == "Apply database migrations" for s in j["steps"])
    )


def test_migrations_run_before_the_deploy_without_override_or_guard() -> None:
    steps = _backend_steps()
    names = [s.get("name") for s in steps]
    migrate = steps[names.index("Apply database migrations")]
    lines = [ln.strip() for ln in migrate["run"].splitlines() if not ln.strip().startswith("#")]

    assert lines.index("uv sync --frozen") < lines.index("uv run alembic upgrade head")
    assert lines.index("uv run alembic upgrade head") < lines.index("uv run alembic current")
    # Phase 7: the old-table guard is gone; revision 0006 performs that check itself.
    assert not any("composer checkpoints" in ln for ln in lines)
    assert not any("prisma" in ln for ln in lines)
    assert names.index("Apply database migrations") < names.index("Deploy to Cloud Run")
    # Fail closed: no tolerated failure, no `|| true` on the migration commands.
    assert "continue-on-error" not in migrate
    assert not any("||" in ln for ln in lines if "alembic" in ln)
    assert "if" not in migrate


def test_migration_step_tolerates_a_neon_cold_start() -> None:
    # Alembic is the deploy's first connection to Neon; a 3 s attempt
    # with no retry can fail while the compute wakes up.
    step = next(s for s in _backend_steps() if s.get("name") == "Apply database migrations")
    env = step["env"]
    assert int(env["DB_CONNECT_TIMEOUT_SECONDS"]) >= 10


def test_migration_step_uses_direct_host_and_rejects_empty_url() -> None:
    step = next(s for s in _backend_steps() if s.get("name") == "Apply database migrations")
    run = str(step["run"])
    assert r"sed 's/-pooler\././'" in run
    assert 'if [ -z "$DATABASE_URL" ]; then' in run
    assert "exit 1" in run


def test_ready_smoke_test_runs_after_the_deploy() -> None:
    names = [s.get("name") for s in _backend_steps()]
    assert names.index("Smoke test /ready") > names.index("Deploy to Cloud Run")
