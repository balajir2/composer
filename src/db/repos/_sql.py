"""Small SQL helpers shared by repositories."""

from sqlalchemy import ARRAY, ColumnElement, Text, any_, literal
from sqlalchemy.orm import InstrumentedAttribute


def in_array(column: InstrumentedAttribute[str], values: list[str]) -> ColumnElement[bool]:
    """``column = ANY(:values)`` with the whole list bound as ONE array parameter.

    ``column.in_(values)`` binds one parameter per value, and PostgreSQL caps a statement at
    65535 parameters, so a bulk operation over a large list would fail.
    """
    return column == any_(literal(values, ARRAY(Text)))
