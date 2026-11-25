"""Channel-version conversion between the old integer saver and the official string saver.

The old saver (and LangGraph's in-memory one) uses integer channel versions. The official
``AsyncPostgresSaver`` uses ``"<32-digit zero padded int>.<16-digit random fraction>"`` strings
and its blob table is keyed by that exact string. Neither base ``get_next_version`` copes with
the other's shape (integer + string raises ``NotImplementedError``), so a copied checkpoint must
carry versions in the target's shape.

Mapping: ``n -> f"{n:032}.{0:016}"`` (the integer prefix is what ``get_next_version`` parses, the
fraction only breaks ties and is zero for converted versions); ``"<n>.<frac>" -> n``. Both are
monotonic on the integer part, which is all LangGraph compares across checkpoints of one thread
(each new version is ``prefix + 1``), so ordering, "seen" relationships and version equality
between ``channel_versions`` and ``versions_seen`` are preserved.
"""

from typing import Any, Literal, cast

from langgraph.checkpoint.base import Checkpoint

Target = Literal["postgres", "legacy"]


def to_postgres_version(v: str | int | float) -> str:
    if isinstance(v, str):
        return v
    return f"{int(v):032}.{0:016}"


def to_legacy_version(v: str | int | float) -> int:
    if isinstance(v, str):
        return int(v.split(".", 1)[0])
    return int(v)


def _convert(v: str | int | float, to: Target) -> str | int:
    return to_postgres_version(v) if to == "postgres" else to_legacy_version(v)


def convert_checkpoint(cp: Checkpoint, to: Target) -> Checkpoint:
    """A copy of ``cp`` with every channel version (``channel_versions`` and each node's
    ``versions_seen``) in the target's shape. Ids, timestamps, values and
    ``updated_channels`` are untouched; the input is not mutated."""
    out = cast("dict[str, Any]", dict(cp))
    out["channel_versions"] = {k: _convert(v, to) for k, v in cp["channel_versions"].items()}
    out["versions_seen"] = {
        node: {ch: _convert(v, to) for ch, v in seen.items()}
        for node, seen in cp["versions_seen"].items()
    }
    return cast("Checkpoint", out)
