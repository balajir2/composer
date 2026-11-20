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

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.repos import deployment_settings as settings_repo
from src.db.repos import llm_models as models_repo

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


async def _setting(session: AsyncSession, key: str) -> str | None:
    row = await settings_repo.get(session, key)
    return row.value if row else None


async def enabled_model_catalog(session: AsyncSession) -> dict[str, str | None]:
    rows = await models_repo.list_models(session, enabled_only=True)
    return {f"{r.provider}/{r.model_id}": r.label for r in rows}


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


async def load_policy(session: AsyncSession) -> BuilderModelPolicy:
    default = (await _setting(session, SETTING_DEFAULT)) or DEFAULT_BUILDER_MODEL
    catalog = await enabled_model_catalog(session)
    extras = [m for m in _parse_allowed(await _setting(session, SETTING_ALLOWED)) if m in catalog]
    ids = [default, *[m for m in dict.fromkeys(extras) if m != default]]
    return BuilderModelPolicy(
        default=default,
        options=[ModelOption(id=i, label=catalog.get(i) or i) for i in ids],
    )


async def save_policy(session: AsyncSession, *, default: str, allowed: list[str]) -> None:
    catalog = await enabled_model_catalog(session)
    unknown = [m for m in [default, *allowed] if m not in catalog]
    if unknown:
        raise ValueError("Not enabled in the LLM model catalog: " + ", ".join(sorted(set(unknown))))
    extras = [m for m in dict.fromkeys(allowed) if m != default]
    for key, value in ((SETTING_DEFAULT, default), (SETTING_ALLOWED, json.dumps(extras))):
        await settings_repo.upsert(session, key, value)
