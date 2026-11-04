"""Which chat models the "Build with AI" panel may use.

Admins choose a default and an allow-list from the enabled LLM-model catalog.
Both live in deployment settings, so no migration is needed:

- ``builder_model``           the default ("provider/modelId")
- ``builder_allowed_models``  JSON list of extra "provider/modelId" strings

Until an admin configures it, only the default is allowed, so cost cannot
widen by accident. The default is always allowed; other entries must still be
enabled in the catalog, so disabling a model there also removes it here.
"""

import json
from dataclasses import dataclass, field
from typing import Any

DEFAULT_BUILDER_MODEL = "anthropic/claude-sonnet-4-5"
SETTING_DEFAULT = "builder_model"
SETTING_ALLOWED = "builder_allowed_models"


class ModelNotAllowedError(ValueError):
    """The requested model is not in the admin-approved set."""


@dataclass(frozen=True)
class ModelOption:
    id: str
    label: str


@dataclass(frozen=True)
class BuilderModelPolicy:
    default: str
    options: list[ModelOption] = field(default_factory=list[ModelOption])

    @property
    def allowed_ids(self) -> list[str]:
        return [o.id for o in self.options]

    def resolve(self, requested: str | None) -> str:
        if not requested:
            return self.default
        if requested not in self.allowed_ids:
            raise ModelNotAllowedError(f"Model {requested!r} is not enabled for the builder.")
        return requested


async def _setting(db: Any, key: str) -> str | None:
    row = await db.deploymentsetting.find_unique(where={"key": key})
    return row.value if row else None


async def _enabled_catalog(db: Any) -> dict[str, str | None]:
    rows = await db.llmmodel.find_many(where={"enabled": True})
    return {f"{r.provider}/{r.modelId}": r.label for r in rows}


def _parse_allowed(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, str)]  # pyright: ignore[reportUnknownVariableType]


async def load_policy(db: Any) -> BuilderModelPolicy:
    default = (await _setting(db, SETTING_DEFAULT)) or DEFAULT_BUILDER_MODEL
    catalog = await _enabled_catalog(db)
    extras = [m for m in _parse_allowed(await _setting(db, SETTING_ALLOWED)) if m in catalog]
    ids = [default, *[m for m in dict.fromkeys(extras) if m != default]]
    return BuilderModelPolicy(
        default=default,
        options=[ModelOption(id=i, label=catalog.get(i) or i) for i in ids],
    )


async def save_policy(db: Any, *, default: str, allowed: list[str]) -> None:
    catalog = await _enabled_catalog(db)
    unknown = [m for m in [default, *allowed] if m not in catalog]
    if unknown:
        raise ValueError("Not enabled in the LLM model catalog: " + ", ".join(sorted(set(unknown))))
    extras = [m for m in dict.fromkeys(allowed) if m != default]
    for key, value in ((SETTING_DEFAULT, default), (SETTING_ALLOWED, json.dumps(extras))):
        await db.deploymentsetting.upsert(
            where={"key": key},
            data={"create": {"key": key, "value": value}, "update": {"value": value}},
        )
