"""Tests for the shared extract_text() helper (used by composer watch and
the Google Drive poller)."""

import pytest


def test_extracts_plain_text() -> None:
    from src.storage_providers.text_extraction import extract_text

    assert extract_text("notes.txt", b"hello world") == "hello world"


def test_extracts_markdown() -> None:
    from src.storage_providers.text_extraction import extract_text

    assert extract_text("notes.md", b"# Heading") == "# Heading"


def test_raises_on_unsupported_extension() -> None:
    from src.storage_providers.text_extraction import UnsupportedFileTypeError, extract_text

    with pytest.raises(UnsupportedFileTypeError, match=r"image\.png"):
        extract_text("image.png", b"\x89PNG\r\n")
