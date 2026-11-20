from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

REPO_ROOT = Path(__file__).resolve().parents[3]
VERSION_NUM_LENGTH = 32  # alembic_version.version_num is VARCHAR(32)


def test_revision_ids_fit_alembic_version_num() -> None:
    script = ScriptDirectory.from_config(Config(str(REPO_ROOT / "alembic.ini")))
    revisions = list(script.walk_revisions())
    assert revisions
    too_long = [r.revision for r in revisions if len(r.revision) > VERSION_NUM_LENGTH]
    assert too_long == []
