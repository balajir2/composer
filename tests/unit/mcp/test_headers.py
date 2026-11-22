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


def test_non_latin1_value_is_rejected_without_echoing_it() -> None:
    """h11 encodes header values as latin-1; a wider character fails at send time with an
    error quoting it, so it must be refused up front, naming only the header."""
    with pytest.raises(InvalidHeaderError) as exc_info:
        validate_custom_headers({"X-Api-Key": "SECRET-\u2603-TOKEN"})
    assert "X-Api-Key" in str(exc_info.value)
    assert "SECRET" not in str(exc_info.value)
    assert "\u2603" not in str(exc_info.value)
    assert header_problem("X-Api-Key", "caf\u00e9") is None  # latin-1 is fine


def test_send_time_filter_skips_non_latin1_values_and_logs_only_the_name(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from src.mcp.base import _merge_custom_headers  # pyright: ignore[reportPrivateUsage]

    with caplog.at_level("WARNING"):
        merged = _merge_custom_headers(
            {"Authorization": "Bearer t"},
            {"X-Bad": "SECRET-\u2603-TOKEN", "X-Good": "ok"},
        )
    assert merged == {"Authorization": "Bearer t", "X-Good": "ok"}
    assert "X-Bad" in caplog.text
    assert "SECRET" not in caplog.text
