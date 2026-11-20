from datetime import datetime

from sqlalchemy import Index, Text
from sqlalchemy.dialects.postgresql import DOUBLE_PRECISION
from sqlalchemy.orm import Mapped, mapped_column

from src.db.models.base import Base, id_column
from src.db.types import UTCDateTime


class RateLimitBucket(Base):
    __tablename__ = "rate_limit_buckets"
    __table_args__ = (
        Index(
            "rate_limit_buckets_route_key_client_key_key", "route_key", "client_key", unique=True
        ),
    )

    id: Mapped[str] = id_column()
    route_key: Mapped[str] = mapped_column(Text)
    client_key: Mapped[str] = mapped_column(Text)
    tokens: Mapped[float] = mapped_column(DOUBLE_PRECISION, nullable=False)
    last_refill: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
