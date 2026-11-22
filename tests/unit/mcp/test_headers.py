"""Custom-header validation: names only in errors, never values."""

import pytest

from src.mcp.headers import InvalidHeaderError, header_problem, validate_custom_headers


def test_valid_headers_are_stripped() -> None:
    assert validate_custom_headers({" X-Tenant ": "  t1 \n"}) == {"X-Tenant": "t1"}


@pytest.mark.parametrize("name", ["", "  ", "Bad Name", "X:Y", "X\nY", "Ünï"])
def test_bad_names_are_rejected_by_name(name: str) -> None:
    with pytest.raises(InvalidHeaderError, match="not a valid HTTP header name"):
        validate_custom_headers({name: "v"})


@pytest.mark.parametrize("bad", ["ab\ncd", "ab\rcd", "ab\0cd"])
def test_control_characters_in_a_value_are_rejected_without_echoing_it(bad: str) -> None:
    with pytest.raises(InvalidHeaderError) as exc_info:
        validate_custom_headers({"X-Api-Key": "SECRET" + bad})
    assert "X-Api-Key" in str(exc_info.value)
    assert "SECRET" not in str(exc_info.value)
    assert "SECRET" not in repr(exc_info.value.args)


@pytest.mark.parametrize(
    "name",
    [
        "Host",
        "content-length",
        "TRANSFER-ENCODING",
        "Connection",
        "Keep-Alive",
        "TE",
        "Trailer",
        "Upgrade",
        "Proxy-Connection",
    ],
)
def test_hop_by_hop_and_framing_headers_are_rejected(name: str) -> None:
    assert header_problem(name, "x") is not None
    with pytest.raises(InvalidHeaderError, match="set by the HTTP client"):
        validate_custom_headers({name: "x"})
