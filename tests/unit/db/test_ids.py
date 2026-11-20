import time
import uuid

from src.db.ids import new_id


def test_new_id_is_a_version_7_uuid_string() -> None:
    value = new_id()
    parsed = uuid.UUID(value)
    assert parsed.version == 7
    assert parsed.variant == uuid.RFC_4122
    assert str(parsed) == value


def test_new_id_embeds_the_current_time_in_milliseconds() -> None:
    before = time.time_ns() // 1_000_000
    value = new_id()
    after = time.time_ns() // 1_000_000
    embedded_ms = uuid.UUID(value).int >> 80
    assert before <= embedded_ms <= after


def test_new_ids_are_unique() -> None:
    values = {new_id() for _ in range(10_000)}
    assert len(values) == 10_000


def test_new_ids_sort_by_creation_time_across_milliseconds() -> None:
    first = new_id()
    start_ms = time.time_ns() // 1_000_000
    while time.time_ns() // 1_000_000 == start_ms:
        time.sleep(0.001)
    second = new_id()
    assert first < second
