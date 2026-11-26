"""Guards on the production deploy workflow."""

from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/deploy-gcp.yml"


def test_production_deploys_are_serialised_and_never_cancelled() -> None:
    doc = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)

    concurrency = doc["concurrency"]

    assert concurrency["group"] == "deploy-gcp-production"
    # Cancelling a deploy mid-migration is worse than queueing the next one.
    assert concurrency["cancel-in-progress"] == "false"


def test_checkpoint_guard_runs_before_alembic_and_before_deploy_without_override() -> None:
    doc = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    steps = doc["jobs"]["backend"]["steps"] if "backend" in doc["jobs"] else None
    if steps is None:
        steps = next(
            j["steps"]
            for j in doc["jobs"].values()
            if any(s.get("name") == "Apply database migrations" for s in j["steps"])
        )
    names = [s.get("name") for s in steps]
    migrate = steps[names.index("Apply database migrations")]
    lines = [ln.strip() for ln in migrate["run"].splitlines() if not ln.strip().startswith("#")]

    assert "uv run composer checkpoints guard" in lines
    guard = lines.index("uv run composer checkpoints guard")
    assert lines.index("uv sync --frozen") < guard
    assert guard < lines.index("uv run alembic upgrade head")
    assert lines.index("uv run alembic upgrade head") < lines.index("uv run alembic current")
    assert not any("prisma" in ln for ln in lines)
    assert names.index("Apply database migrations") < names.index("Deploy to Cloud Run")
    # Fail closed: no override, no tolerated failure, no `|| true`.
    assert "continue-on-error" not in migrate
    assert not any("guard" in ln and ("||" in ln or "true" in ln) for ln in lines)
    assert "if" not in migrate
