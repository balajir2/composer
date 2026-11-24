from datetime import UTC, datetime, timedelta, timezone

from src.db.types import ensure_utc, truncate_to_millis


def test_naive_datetime_is_taken_as_utc_like_prisma_did() -> None:
    assert ensure_utc(datetime(2026, 12, 1, 9, 30)) == datetime(2026, 12, 1, 9, 30, tzinfo=UTC)


def test_aware_datetime_is_converted_to_utc() -> None:
    ist = timezone(timedelta(hours=5, minutes=30))
    assert ensure_utc(datetime(2026, 12, 1, 15, 0, tzinfo=ist)) == datetime(
        2026, 12, 1, 9, 30, tzinfo=UTC
    )


def test_truncate_to_millis_drops_sub_millisecond_digits_like_utcnow() -> None:
    value = datetime(2026, 11, 24, 10, 0, 0, 123456, tzinfo=UTC)
    assert truncate_to_millis(value) == datetime(2026, 11, 24, 10, 0, 0, 123000, tzinfo=UTC)
    assert truncate_to_millis(truncate_to_millis(value)) == truncate_to_millis(value)
