"""Strip base64-encoded binary blobs from MCP tool responses.

Some MCP servers (Highspot's `get_item_content` is the example we hit, but
it's a defect class — see docs/2026-04-30-execution-status-truth.md) return
raw bytes of binary file types (xlsx / pptx / pdf / docx / zip / images)
base64-encoded inside the tool's *text* response channel.  A 50KB spreadsheet
becomes ~80KB of base64 ≈ 20K useless tokens in the agent's context window.
A few such fetches will exhaust even a 200K-token context.

The strip belongs at the unwrap boundary — the single chokepoint every MCP
tool response flows through (`_render_content_blocks` in src/mcp/base.py).
One fix protects every agent.

The replacement marker matters as much as the strip itself.  The agent needs
to know *what* the item was (so it can summarize / skip it intelligently),
*that* it can't be retrieved (so it doesn't retry the same call), and *what
to do instead* (use search-result metadata).  Without those three the model
will sometimes hallucinate content for the binary or loop on the call.
"""

from __future__ import annotations

import re
from typing import Final

# Highspot's response shape — `Base64 Encoded Content` is followed by a
# fenced run of base64 bytes.  We detect and strip on the literal phrase.
_HIGHSPOT_MARKER: Final[str] = "Base64 Encoded Content"

# Generic fallback: any contiguous base64-like run >4KB inside a fenced
# block is almost certainly binary masquerading as text.  Threshold chosen
# so legitimate code blocks (which can include base64 images < a few KB)
# pass through unchanged.
_GENERIC_BLOB_RE: Final[re.Pattern[str]] = re.compile(
    r"```[\s\S]*?[A-Za-z0-9+/=\r\n]{4096,}[\s\S]*?```"
)

_HIGHSPOT_META_KEYS: Final[tuple[str, ...]] = (
    "Item ID",
    "File Name",
    "Content Type",
    "Size",
)


def _highspot_replacement(text: str) -> str:
    """Build a metadata-only replacement for a Highspot binary response.

    The agent gets enough to identify the item without any of the binary
    payload, plus an explicit "do not retry" hint.
    """
    meta: dict[str, str] = {}
    for line in text.split("\n")[:12]:
        for key in _HIGHSPOT_META_KEYS:
            prefix = f"{key}:"
            if line.startswith(prefix):
                meta[key] = line[len(prefix) :].strip()
                break
    lines = [
        "[Binary file omitted from agent context — content cannot be "
        "extracted from binary formats.]",
    ]
    for key in _HIGHSPOT_META_KEYS:
        lines.append(f"{key}: {meta.get(key, 'unknown')}")
    lines.append(
        "Use only the search_content metadata (title, summary, URL) for this "
        "item. Do not retry get_item_content on it."
    )
    return "\n".join(lines)


def strip_binary_blobs(text: str) -> str:
    """Remove base64-encoded binary content from MCP tool response text.

    Two passes:
      1. Highspot-style preamble (`Base64 Encoded Content`) → replace the
         whole response with a metadata-only stub.
      2. Generic fenced base64 run (>4KB) → replace each fenced block with
         a one-line marker.  Surrounding prose is left intact.
    """
    if _HIGHSPOT_MARKER in text:
        return _highspot_replacement(text)

    return _GENERIC_BLOB_RE.sub(
        "[Binary blob (>4KB base64) omitted from agent context.]",
        text,
    )


__all__ = ["strip_binary_blobs"]
