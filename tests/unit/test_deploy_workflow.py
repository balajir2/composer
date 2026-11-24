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
