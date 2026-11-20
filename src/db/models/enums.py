"""Postgres enum types created by Prisma, reused as-is (never re-created)."""

import enum

from sqlalchemy.dialects.postgresql import ENUM


def _enum_values(enum_cls: type[enum.Enum]) -> list[str]:
    return [str(member.value) for member in enum_cls]


class UserRole(enum.StrEnum):
    ADMIN = "admin"
    MEMBER = "member"


class ApprovalDecision(enum.StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


USER_ROLE = ENUM(UserRole, name="UserRole", create_type=False, values_callable=_enum_values)
APPROVAL_DECISION = ENUM(
    ApprovalDecision, name="ApprovalDecision", create_type=False, values_callable=_enum_values
)
