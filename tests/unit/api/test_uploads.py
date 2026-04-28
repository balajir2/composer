"""Tests for the document upload + text-extraction endpoint.

The endpoint is the bridge between the run-input form's file picker
and the workflow execution path: file in, plain text out, no
persistence.  Tests cover the supported formats, the content-type
dispatch, the size cap, the empty-file rejection, and the bytes-only
extension fallback for files Chrome misreports as octet-stream.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING, Any

from fastapi.testclient import TestClient

from src.main import create_app

if TYPE_CHECKING:
    import pytest


def _client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    from src.config import get_settings
    from src.security.rate_limit import RateLimiter

    get_settings.cache_clear()
    app = create_app()
    # Fresh rate limiter per test so the 20/min bucket from previous
    # cases doesn't bleed into the next.
    app.state.rate_limiter = RateLimiter()
    return TestClient(app)


def _build_pdf_bytes() -> bytes:
    """Minimal valid PDF byte-string with no extractable text.

    Hand-crafting a PDF with extractable text without a generator
    library (reportlab) is fiddly — the dispatch path is what we're
    really testing here.  The PDF extractor is exercised separately
    via monkeypatching in `test_extract_pdf_dispatches`."""
    return (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 100 100]>>endobj\n"
        b"xref\n0 4\n"
        b"0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"0000000054 00000 n \n"
        b"0000000099 00000 n \n"
        b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n147\n%%EOF\n"
    )


def _build_docx_bytes(paragraphs: list[str]) -> bytes:
    from docx import Document  # pyright: ignore[reportMissingImports]

    doc = Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_extract_plain_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """UTF-8 plain text round-trips verbatim."""
    client = _client(monkeypatch)
    payload = "Hello world.\nSecond line."
    resp = client.post(
        "/uploads/extract-text",
        files={"file": ("notes.txt", payload.encode("utf-8"), "text/plain")},
    )
    assert resp.status_code == 200
    body: dict[str, Any] = resp.json()
    assert body["filename"] == "notes.txt"
    assert body["text"] == payload
    assert body["size_bytes"] == len(payload.encode("utf-8"))


def test_extract_markdown_treated_as_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """Markdown extension routes through the same plain-text path —
    we don't strip markdown syntax (downstream agents see it as-is
    and benefit from the structure)."""
    client = _client(monkeypatch)
    payload = "# Heading\n\nSome **bold** content."
    resp = client.post(
        "/uploads/extract-text",
        files={"file": ("README.md", payload.encode("utf-8"), "text/markdown")},
    )
    assert resp.status_code == 200
    assert resp.json()["text"] == payload


def test_extract_text_with_bom_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    """utf-8-sig handles the byte-order mark Excel and Notepad emit."""
    client = _client(monkeypatch)
    payload = "data here"
    body_bytes = b"\xef\xbb\xbf" + payload.encode("utf-8")
    resp = client.post(
        "/uploads/extract-text",
        files={"file": ("with-bom.txt", body_bytes, "text/plain")},
    )
    assert resp.status_code == 200
    assert resp.json()["text"] == payload


def test_extract_text_latin1_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Files that aren't valid UTF-8 still return content (latin-1)
    rather than 422-ing the whole upload."""
    client = _client(monkeypatch)
    # 0xa3 is the UK pound sign in latin-1, invalid as a UTF-8
    # leading byte.
    body_bytes = b"Cost: \xa3500"
    resp = client.post(
        "/uploads/extract-text",
        files={"file": ("price.txt", body_bytes, "text/plain")},
    )
    assert resp.status_code == 200
    text = resp.json()["text"]
    assert "Cost:" in text
    assert "500" in text


def test_extract_pdf_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Files with .pdf extension route through the pypdf extractor.

    We monkeypatch the extractor itself so the test doesn't depend on
    crafting a real PDF byte-stream — that's pypdf's responsibility,
    not ours.  The dispatch logic is what this endpoint owns and what
    can break under refactoring."""
    import src.api.uploads as uploads_mod

    monkeypatch.setattr(
        uploads_mod, "_extract_text_pdf", lambda data: f"PDF text ({len(data)} bytes)"
    )
    client = _client(monkeypatch)
    pdf = _build_pdf_bytes()
    resp = client.post(
        "/uploads/extract-text",
        files={"file": ("doc.pdf", pdf, "application/pdf")},
    )
    assert resp.status_code == 200
    body: dict[str, Any] = resp.json()
    assert body["text"].startswith("PDF text")
    assert body["filename"] == "doc.pdf"


def test_extract_docx(monkeypatch: pytest.MonkeyPatch) -> None:
    """python-docx pulls paragraphs verbatim, joins with double-newlines."""
    client = _client(monkeypatch)
    docx = _build_docx_bytes(["First paragraph", "Second paragraph"])
    resp = client.post(
        "/uploads/extract-text",
        files={
            "file": (
                "doc.docx",
                docx,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert resp.status_code == 200
    text = resp.json()["text"]
    assert "First paragraph" in text
    assert "Second paragraph" in text


def test_unsupported_extension_returns_415(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unknown extensions get a clear error rather than silent garbage."""
    client = _client(monkeypatch)
    resp = client.post(
        "/uploads/extract-text",
        files={"file": ("data.xyz", b"binary nonsense", "application/octet-stream")},
    )
    assert resp.status_code == 415
    detail = resp.json()["detail"]
    assert "unsupported" in detail.lower()
    assert ".pdf" in detail
    assert ".docx" in detail


def test_empty_file_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zero-byte upload is a user error — 422 with a clear message
    rather than passing an empty string downstream."""
    client = _client(monkeypatch)
    resp = client.post(
        "/uploads/extract-text",
        files={"file": ("empty.txt", b"", "text/plain")},
    )
    assert resp.status_code == 422
    assert "empty" in resp.json()["detail"].lower()


def test_oversized_upload_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hard cap protects the worker from multi-gigabyte uploads."""
    from src.api.uploads import MAX_UPLOAD_BYTES

    client = _client(monkeypatch)
    # Just over the cap to keep the test fast.
    big = b"x" * (MAX_UPLOAD_BYTES + 1)
    resp = client.post(
        "/uploads/extract-text",
        files={"file": ("huge.txt", big, "text/plain")},
    )
    assert resp.status_code == 413
    assert "exceeds" in resp.json()["detail"].lower()


def test_extension_takes_priority_over_misreported_content_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chrome reports .md as application/octet-stream; route on
    extension so the upload still succeeds."""
    client = _client(monkeypatch)
    resp = client.post(
        "/uploads/extract-text",
        files={"file": ("notes.md", b"# Hello", "application/octet-stream")},
    )
    assert resp.status_code == 200
    assert resp.json()["text"] == "# Hello"
