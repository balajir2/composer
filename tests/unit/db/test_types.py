from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy.dialects import postgresql

from src.db.types import UTCDateTime, utcnow

DIALECT = postgresql.dialect()


def test_bind_converts_aware_datetime_to_naive_utc() -> None:
    ist = timezone(timedelta(hours=5, minutes=30))
    value = datetime(2026, 11, 20, 15, 30, tzinfo=ist)
    bound = UTCDateTime().process_bind_param(value, DIALECT)
    assert bound == datetime(2026, 11, 20, 10, 0)
    assert bound is not None and bound.tzinfo is None


def test_bind_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        UTCDateTime().process_bind_param(datetime(2026, 11, 20, 10, 0), DIALECT)


def test_bind_passes_none_through() -> None:
    assert UTCDateTime().process_bind_param(None, DIALECT) is None


def test_result_marks_stored_value_as_utc() -> None:
    result = UTCDateTime().process_result_value(datetime(2026, 11, 20, 10, 0), DIALECT)
    assert result == datetime(2026, 11, 20, 10, 0, tzinfo=UTC)


def test_result_passes_none_through() -> None:
    assert UTCDateTime().process_result_value(None, DIALECT) is None


def test_column_is_millisecond_precision_without_time_zone() -> None:
    impl = UTCDateTime().impl
    assert isinstance(impl, postgresql.TIMESTAMP)
    assert impl.precision == 3
    assert impl.timezone is False


def test_utcnow_is_aware_utc() -> None:
    now = utcnow()
    assert now.tzinfo is UTC


def test_utcnow_is_truncated_to_whole_milliseconds() -> None:
    assert utcnow().microsecond % 1000 == 0


def _bound(value: datetime) -> datetime:
    bound = UTCDateTime().process_bind_param(value, DIALECT)
    assert bound is not None
    return bound


def test_bind_rounds_up_to_the_nearest_millisecond() -> None:
    value = datetime(2026, 11, 20, 10, 0, 0, 123999, tzinfo=UTC)
    assert _bound(value) == datetime(2026, 11, 20, 10, 0, 0, 124000)


def test_bind_rounds_down_below_half_a_millisecond() -> None:
    value = datetime(2026, 11, 20, 10, 0, 0, 123499, tzinfo=UTC)
    assert _bound(value) == datetime(2026, 11, 20, 10, 0, 0, 123000)


def test_bind_rounding_carries_into_the_next_second_and_day() -> None:
    value = datetime(2026, 11, 20, 23, 59, 59, 999600, tzinfo=UTC)
    assert _bound(value) == datetime(2026, 11, 21, 0, 0, 0, 0)
