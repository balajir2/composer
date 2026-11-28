import pytest

from src.cli.main import _build_parser  # pyright: ignore[reportPrivateUsage]


def test_checkpoints_subcommand_is_gone() -> None:
    with pytest.raises(SystemExit) as exc:
        _build_parser().parse_args(["checkpoints", "guard"])
    assert exc.value.code == 2
