from datetime import datetime

from sqlalchemy import Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.models.base import Base, created_at_column, id_column, updated_at_column
from src.db.types import UTCDateTime


class CloudStorageConnection(Base):
    __tablename__ = "cloud_storage_connections"
    __table_args__ = (
        Index("cloud_storage_connections_user_id_idx", "user_id"),
        Index(
            "cloud_storage_connections_user_id_provider_account_email_key",
            "user_id",
            "provider",
            "account_email",
            unique=True,
        ),
    )

    id: Mapped[str] = id_column()
    user_id: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(Text)
    account_email: Mapped[str] = mapped_column(Text)
    encrypted_access_token: Mapped[str] = mapped_column(Text)
    encrypted_refresh_token: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    scope: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()
