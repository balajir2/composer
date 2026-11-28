"""Nothing outside the frozen revisions and their tests refers to the dropped tables or the bridge
code that existed for them (phase 7, revision 0006)."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

FORBIDDEN = (
    "langgraph_checkpoint",
    "LangGraphCheckpoint",
    "LegacyCheckpointSaver",
    "bring_forward",
    "_prisma_migrations",
    "composer checkpoints",
    "src.checkpoints.sync",
)

# Files that name the dropped objects on purpose: the 0006 tests, the adoption tests that build
# the ledger by hand, and tests/comments asserting that the tables and the bridge are gone.
ALLOWED = {
    "src/db/schema_filters.py",
    "tests/integration/db/repos/test_checkpoints.py",
    "tests/integration/sqla_phase5/test_deliberate_changes.py",
    "tests/unit/engine/test_resume_checkpoint_probe.py",
    "tests/unit/db/test_legacy_tables_gone.py",
    "tests/integration/db/test_baseline_schema.py",
    "tests/integration/db/schema_catalog.py",
    "tests/integration/db/test_alembic_0006_drop_legacy_tables.py",
    "tests/unit/db/test_revision_0006.py",
    "tests/unit/db/test_schema_filters.py",
    "tests/unit/db/test_baseline_frozen.py",  # test name only (twenty_prisma_migrations)
    "tests/unit/test_deploy_workflow.py",  # asserts the guard step is gone
}


def _files() -> list[Path]:
    found: list[Path] = []
    for path in (ROOT / "src").rglob("*.py"):
        if "migrations/versions" in path.as_posix() or "__pycache__" in path.parts:
            continue
        if path.relative_to(ROOT).as_posix() in ALLOWED:
            continue
        found.append(path)
    for path in (ROOT / "tests").rglob("*.py"):
        if "__pycache__" in path.parts or path.relative_to(ROOT).as_posix() in ALLOWED:
            continue
        found.append(path)
    found.extend((ROOT / ".github" / "workflows").glob("*.yml"))
    return found


def test_no_reference_to_the_dropped_tables_or_the_bridge_remains() -> None:
    hits: list[str] = []
    for path in _files():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if any(word in line for word in FORBIDDEN):
                hits.append(f"{path.relative_to(ROOT).as_posix()}:{number}")
    assert hits == []
