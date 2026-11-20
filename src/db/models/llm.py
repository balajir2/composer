"""LLM provider API keys and the enabled model catalog."""

from datetime import datetime

from sqlalchemy import Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.models.base import Base, bool_column, created_at_column, id_column, updated_at_column
from src.db.types import UTCDateTime


class LlmApiKey(Base):
    """The encrypted API key stored for one LLM provider."""

    __tablename__ = "llm_api_keys"
    __table_args__ = (Index("llm_api_keys_provider_key", "provider", unique=True),)

    id: Mapped[str] = id_column()
    provider: Mapped[str] = mapped_column(Text)
    encrypted_key: Mapped[str] = mapped_column(Text)
    key_prefix: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class LlmModel(Base):
    """An LLM model offered under a provider, with enabled flag and verification status."""

    __tablename__ = "llm_models"
    __table_args__ = (
        Index("llm_models_provider_enabled_idx", "provider", "enabled"),
        Index("llm_models_provider_model_id_key", "provider", "model_id", unique=True),
    )

    id: Mapped[str] = id_column()
    provider: Mapped[str] = mapped_column(Text)
    model_id: Mapped[str] = mapped_column(Text)
    label: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = bool_column(True)
    verification_status: Mapped[str | None] = mapped_column(Text)
    verification_message: Mapped[str | None] = mapped_column(Text)
    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()
