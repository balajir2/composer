"""Channel-version conversion between the integer (legacy) and string (official) shapes."""

from typing import Any, cast

from langgraph.checkpoint.base import Checkpoint

from src.checkpoints.versions import convert_checkpoint, to_legacy_version, to_postgres_version


def _cp() -> Checkpoint:
    return cast(
        "Checkpoint",
        {
            "v": 4,
            "id": "cp-1",
            "ts": "2026-01-01T00:00:00+00:00",
            "channel_values": {"a": 1},
            "channel_versions": {"a": 3, "__start__": 1.0},
            "versions_seen": {"__input__": {}, "n": {"a": 2, "__start__": 1}},
            "updated_channels": ["a"],
        },
    )


def test_to_postgres_version_shape() -> None:
    assert to_postgres_version(5) == "00000000000000000000000000000005.0000000000000000"
    assert to_postgres_version(5.0) == to_postgres_version(5)


def test_strings_and_ints_pass_through() -> None:
    s = "00000000000000000000000000000007.0123456789012345"
    assert to_postgres_version(s) == s
    assert to_legacy_version(9) == 9


def test_to_legacy_version_parses_prefix() -> None:
    assert to_legacy_version("00000000000000000000000000000007.0123456789") == 7
    assert to_legacy_version(7.0) == 7


def test_ordering_preserved() -> None:
    xs = [1, 2, 10, 11]
    assert sorted(to_postgres_version(x) for x in xs) == [to_postgres_version(x) for x in xs]


def test_round_trip() -> None:
    for n in (0, 1, 42, 10**9):
        assert to_legacy_version(to_postgres_version(n)) == n


def test_convert_checkpoint_converts_both_maps_without_mutating() -> None:
    cp = _cp()
    before = repr(cp)
    out: Any = convert_checkpoint(cp, "postgres")
    assert repr(cp) == before
    assert out["channel_versions"]["a"] == to_postgres_version(3)
    assert out["channel_versions"]["__start__"] == to_postgres_version(1)
    assert out["versions_seen"]["n"]["a"] == to_postgres_version(2)
    assert out["versions_seen"]["__input__"] == {}
    assert out["id"] == "cp-1" and out["channel_values"] == {"a": 1}
    assert out["updated_channels"] == ["a"]
    back: Any = convert_checkpoint(out, "legacy")
    assert back["channel_versions"] == {"a": 3, "__start__": 1}
    assert back["versions_seen"]["n"] == {"a": 2, "__start__": 1}
