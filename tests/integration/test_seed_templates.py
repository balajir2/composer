"""scripts/seed_templates.py seeds every template once and is idempotent."""

import pytest

from scripts.seed_templates import _TEMPLATES, seed  # pyright: ignore[reportPrivateUsage]
from src.db.models import Workflow
from tests.integration._rows import Rows

pytestmark = pytest.mark.integration


async def test_seed_creates_then_updates_without_duplicates(rows: Rows) -> None:
    sm = rows.sessionmaker
    slugs = [t["externalSlug"] for t in _TEMPLATES]
    await rows.remove(Workflow, Workflow.external_slug.in_(slugs))
    try:
        async with sm() as s:
            first = await seed(s)
            await s.commit()
        async with sm() as s:
            second = await seed(s)
            await s.commit()
        assert {o for o, _ in first} == {"created"}
        assert {o for o, _ in second} == {"updated"}
        seeded = await rows.where(Workflow, Workflow.external_slug.in_(slugs))
        assert len(seeded) == len(slugs)
        assert all(w.is_template and w.is_public and w.user_id is None for w in seeded)
    finally:
        await rows.remove(Workflow, Workflow.external_slug.in_(slugs))
