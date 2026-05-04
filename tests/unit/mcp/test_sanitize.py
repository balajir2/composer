"""Tests for the MCP base64 binary-blob sanitizer.

Covers the two patterns we strip:
  1. Highspot's `Base64 Encoded Content` preamble — replaced with a
     metadata-only stub that tells the agent what the item was and that
     it cannot be retrieved.
  2. Generic >4KB base64 inside a fenced block — replaced with a one-line
     marker; surrounding prose stays intact.
"""

from __future__ import annotations

from src.mcp.sanitize import strip_binary_blobs


def test_short_text_passes_through_unchanged() -> None:
    """Plain text is never modified; no false positives on small content."""
    text = "Item 12 found in catalog. Title: 'Q3 Roadmap'. Last edited 2026-04-15."
    assert strip_binary_blobs(text) == text


def test_small_base64_inside_code_fence_passes_through() -> None:
    """Code blocks shorter than the 4KB threshold are not touched.

    Designers occasionally paste short base64 snippets (small icons,
    test fixtures) into MCP tool docs.  We don't want to mangle those.
    """
    short_b64 = "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo="  # ~36 chars
    text = f"Here's the icon:\n```\n{short_b64}\n```\nUse it as the avatar."
    assert strip_binary_blobs(text) == text


def test_highspot_xlsx_response_replaced_with_metadata() -> None:
    """Highspot's `get_item_content` on a spreadsheet → metadata-only stub.

    The agent should learn what the item is + that it can't be retrieved,
    and should not see any of the base64 payload.
    """
    big_b64 = "PK" + "A" * 4096  # well over the threshold
    response = (
        "File Content Retrieved\n"
        "Item ID: 638a7314df63f89e41ceccf9\n"
        "File Name: Q3-roadmap.xlsx\n"
        "Content Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\n"
        "Format: text\n"
        "Size: 57928 bytes\n"
        "\n"
        "Base64 Encoded Content:\n"
        "\n"
        f"```\n{big_b64}\n```\n"
    )
    out = strip_binary_blobs(response)

    # Payload removed
    assert big_b64 not in out
    assert "Base64 Encoded Content" not in out
    # Marker present
    assert "Binary file omitted" in out
    # Metadata preserved so the agent knows which item this was
    assert "638a7314df63f89e41ceccf9" in out
    assert "Q3-roadmap.xlsx" in out
    assert "spreadsheetml.sheet" in out
    # Anti-loop hint present
    assert "Do not retry" in out


def test_highspot_response_with_missing_metadata_uses_unknown() -> None:
    """Stub still emits when the response lacks expected metadata fields."""
    response = (
        "File Content Retrieved\n"
        "File Name: only-this-field\n"
        "Base64 Encoded Content:\n"
        f"```\n{'A' * 4100}\n```\n"
    )
    out = strip_binary_blobs(response)
    assert "only-this-field" in out
    assert "Item ID: unknown" in out
    assert "Content Type: unknown" in out


def test_generic_large_base64_block_replaced_with_marker() -> None:
    """Non-Highspot servers that emit fenced base64 also get stripped."""
    blob = "Z" * 5000
    response = f"Here is the file you asked for:\n```\n{blob}\n```\nLet me know."
    out = strip_binary_blobs(response)
    assert blob not in out
    assert "Binary blob" in out
    # Surrounding prose is preserved
    assert "Here is the file you asked for" in out
    assert "Let me know." in out


def test_multiple_large_base64_blocks_all_replaced() -> None:
    """Two binary blobs in one response → both stripped, prose between them kept."""
    blob_a = "A" * 4500
    blob_b = "B" * 4500
    response = f"First file:\n```\n{blob_a}\n```\nSecond file:\n```\n{blob_b}\n```\nDone."
    out = strip_binary_blobs(response)
    assert blob_a not in out
    assert blob_b not in out
    assert out.count("Binary blob") == 2
    assert "First file:" in out
    assert "Second file:" in out
    assert "Done." in out


def test_legitimate_text_response_unchanged() -> None:
    """Realistic prose response from `get_item_content` on a Markdown doc.

    The whole text channel passes through unmodified — no heuristic
    false-positives on long paragraphs.
    """
    long_text = (
        "Q3 Roadmap\n\n"
        "## Executive summary\n\n"
        + ("This quarter we will ship the new search experience. " * 80)
        + "\n\n## Detailed plan\n\n"
        + ("Phase 1 of the rollout covers the agent-search rewrite. " * 80)
    )
    out = strip_binary_blobs(long_text)
    assert out == long_text
