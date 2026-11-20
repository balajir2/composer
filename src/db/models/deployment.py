from datetime import datetime

from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.models.base import Base, updated_at_column


class DeploymentSetting(Base):
    __tablename__ = "deployment_settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = updated_at_column()
