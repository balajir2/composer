"""Helpers shared by repository modules."""

import enum


class Unset(enum.Enum):
    """Marks an update argument that was not given (distinct from None)."""

    UNSET = enum.auto()


UNSET = Unset.UNSET
