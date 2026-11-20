from datetime import datetime
from typing import Any

from sqlalchemy import Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.models.base import Base, bool_column, created_at_column, id_column, updated_at_column
from src.db.types import UTCDateTime


class McpServer(Base):
    __tablename__ = "mcp_servers"
    __table_args__ = (
        Index("mcp_servers_user_id_idx", "user_id"),
        Index("mcp_servers_is_shared_idx", "is_shared"),
    )

    id: Mapped[str] = id_column()
    user_id: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(Text)
    auth_type: Mapped[str] = mapped_column(Text)
    encrypted_access_token: Mapped[str | None] = mapped_column(Text)
    header_name: Mapped[str | None] = mapped_column(Text)
    oauth_config: Mapped[Any | None] = mapped_column(JSONB(none_as_null=True))
    tools: Mapped[Any | None] = mapped_column(JSONB(none_as_null=True))
    connection_status: Mapped[str] = mapped_column(
        Text, nullable=False, default="untested", server_default=text("'untested'::text")
    )
    last_tested: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = bool_column(True)
    is_official: Mapped[bool] = bool_column(False)
    is_shared: Mapped[bool] = bool_column(False)
    headers: Mapped[Any | None] = mapped_column(JSONB(none_as_null=True))
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()
    original_owner_email: Mapped[str | None] = mapped_column(Text)


class McpOAuthToken(Base):
    __tablename__ = "mcp_oauth_tokens"
    __table_args__ = (
        Index("mcp_oauth_tokens_mcp_server_id_idx", "mcp_server_id"),
        Index("mcp_oauth_tokens_user_id_idx", "user_id"),
        Index(
            "mcp_oauth_tokens_mcp_server_id_user_id_key", "mcp_server_id", "user_id", unique=True
        ),
    )

    id: Mapped[str] = id_column()
    mcp_server_id: Mapped[str] = mapped_column(Text)
    user_id: Mapped[str] = mapped_column(Text)
    encrypted_access_token: Mapped[str] = mapped_column(Text)
    encrypted_refresh_token: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    scope: Mapped[str | None] = mapped_column(Text)
    token_type: Mapped[str] = mapped_column(
        Text, nullable=False, default="Bearer", server_default=text("'Bearer'::text")
    )
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class McpOAuthState(Base):
    __tablename__ = "mcp_oauth_states"
    __table_args__ = (
        Index("mcp_oauth_states_state_idx", "state"),
        Index("mcp_oauth_states_state_key", "state", unique=True),
        Index("mcp_oauth_states_expires_at_idx", "expires_at"),
    )

    id: Mapped[str] = id_column()
    mcp_server_id: Mapped[str] = mapped_column(Text)
    user_id: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text)
    code_verifier: Mapped[str] = mapped_column(Text)
    redirect_uri: Mapped[str] = mapped_column(Text)
    scope: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_at: Mapped[datetime] = created_at_column()
