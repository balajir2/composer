from datetime import datetime

from sqlalchemy import ForeignKey, Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.models.base import Base, bool_column, created_at_column, id_column, updated_at_column
from src.db.models.enums import USER_ROLE, UserRole
from src.db.types import UTCDateTime


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        Index("users_email_idx", "email"),
        Index("users_email_key", "email", unique=True),
    )

    id: Mapped[str] = id_column()
    email: Mapped[str] = mapped_column(Text)
    password_hash: Mapped[str | None] = mapped_column(Text)
    display_name: Mapped[str | None] = mapped_column(Text)
    role: Mapped[UserRole] = mapped_column(
        USER_ROLE,
        nullable=False,
        default=UserRole.MEMBER,
        server_default=text("'member'::\"UserRole\""),
    )
    is_active: Mapped[bool] = bool_column(True)
    must_change_password: Mapped[bool] = bool_column(False)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class ApiKey(Base):
    __tablename__ = "api_keys"
    __table_args__ = (
        Index("api_keys_user_id_idx", "user_id"),
        Index("api_keys_key_prefix_idx", "key_prefix"),
        Index("api_keys_key_hash_key", "key_hash", unique=True),
    )

    id: Mapped[str] = id_column()
    user_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey(
            "users.id", name="api_keys_user_id_fkey", ondelete="CASCADE", onupdate="CASCADE"
        ),
    )
    label: Mapped[str] = mapped_column(Text)
    key_hash: Mapped[str] = mapped_column(Text)
    key_prefix: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_column()
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
