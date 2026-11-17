from pathlib import Path


def test_workflow_execution_persists_attempt_timing_and_failure_code() -> None:
    repository_root = Path(__file__).parents[2]
    schema = (repository_root / "prisma" / "schema.prisma").read_text()
    assert "runningSince  DateTime? @map(\"running_since\")" in schema
    assert "failureCode   String?   @map(\"failure_code\")" in schema

    migration = (
        repository_root
        / "prisma"
        / "migrations"
        / "20261117000000_execution_attempt_timing"
        / "migration.sql"
    ).read_text()
    assert 'ADD COLUMN "running_since" TIMESTAMP(3)' in migration
    assert 'ADD COLUMN "failure_code" TEXT' in migration
