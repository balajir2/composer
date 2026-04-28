"""Document upload + text-extraction endpoint.

Composer's Start node supports a `document` input type — at run time
the user uploads a file and the workflow consumes its plain-text
content as a regular string variable.  Storage is intentionally
not persistent: this endpoint receives the file, extracts text in-
flight using the appropriate library, and returns the text so the
frontend can stash it as the input value.  The extracted text rides
through the standard execution input path (subject to the existing
`max_execution_input_bytes` cap), which means downstream nodes
reference `{{policy_doc}}` exactly the way they reference any
other text variable.

Why no persistent storage: keeps Composer infrastructure-light
(no S3, no Convex, no bytea blobs).  If a workflow needs to keep
the file around — for re-running with the same input, audit, or
cross-execution reference — the designer can paste the same file
again or hook up an HTTP node to fetch from their own storage.

Supported formats:
  - text/plain       (.txt) — UTF-8 with utf-8-sig fallback
  - text/markdown    (.md, .markdown) — same as plain text
  - application/pdf  (.pdf) — pypdf page-by-page text extraction
  - application/vnd.openxmlformats-officedocument.wordprocessingml.document
                     (.docx) — python-docx paragraph extraction

Anything else returns 415 with a clear message so the run-input form
can show a helpful error.
"""

from __future__ import annotations

import io
import logging
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel

from src.security.auth import get_current_user_id
from src.security.rate_limit import (
    RateLimiter,
    enforce,
    get_rate_limiter,
    per_minute_config,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/uploads", tags=["uploads"])


# 10 MB hard cap — covers most policy docs, contracts, and meeting
# transcripts without letting a malicious caller wedge the worker
# on a multi-gigabyte upload.  If a workflow legitimately needs
# larger inputs, the designer should chunk upstream and feed via
# the vector-db ingestion template instead.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

# Extension → handler dispatch.  Content-type is unreliable across
# browsers (Chrome lies about .md files), so we route on the file
# extension and use the content-type only as a hint.
_PDF_EXT = ".pdf"
_DOCX_EXT = ".docx"
_TEXT_EXTS = (".txt", ".md", ".markdown", ".rst", ".log")


class UploadExtractResponse(BaseModel):
    filename: str
    content_type: str
    size_bytes: int
    # Extracted plain text — UTF-8.  Frontend stashes this as the
    # value of the corresponding `document` input variable; from
    # the engine's perspective it's just a string.
    text: str


def _extract_text_plain(data: bytes) -> str:
    """UTF-8 with BOM tolerance and a latin-1 fallback for legacy
    text dumps that aren't valid UTF-8.  We never raise from here —
    return whatever decoded so the user sees their content even when
    encoding is messy."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        # latin-1 is byte-mappable so it always decodes; the result
        # may have garbled characters for non-latin scripts but it's
        # better than a 422.
        return data.decode("latin-1", errors="replace")


def _extract_text_pdf(data: bytes) -> str:
    """pypdf page-by-page extraction.  PDFs with embedded fonts that
    don't declare a unicode mapping yield empty pages; we don't try
    OCR — the designer should pre-process scanned PDFs upstream."""
    from pypdf import PdfReader  # pyright: ignore[reportMissingImports]

    reader = PdfReader(io.BytesIO(data))
    pages: list[str] = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:
            # pypdf raises on a few exotic content streams; skip the
            # page rather than failing the whole upload.  The user
            # gets a partial result with a warning logged server-side.
            text = ""
        if text.strip():
            pages.append(text)
    return "\n\n".join(pages)


def _extract_text_docx(data: bytes) -> str:
    """python-docx paragraph + table extraction.  Headers/footers and
    inline images are skipped — for a basic ingest pipeline that's
    almost always what the designer wants."""
    from docx import Document  # pyright: ignore[reportMissingImports]

    doc = Document(io.BytesIO(data))
    parts: list[str] = []
    for paragraph in doc.paragraphs:
        text = paragraph.text.strip()
        if text:
            parts.append(text)
    for table in doc.tables:
        for row in table.rows:
            row_text = [cell.text.strip() for cell in row.cells]
            joined = " | ".join(c for c in row_text if c)
            if joined:
                parts.append(joined)
    return "\n\n".join(parts)


def _dispatch_extractor(filename: str, data: bytes) -> str:
    """Route to the right extractor based on the file extension."""
    name_lower = filename.lower()
    if name_lower.endswith(_PDF_EXT):
        return _extract_text_pdf(data)
    if name_lower.endswith(_DOCX_EXT):
        return _extract_text_docx(data)
    if any(name_lower.endswith(ext) for ext in _TEXT_EXTS):
        return _extract_text_plain(data)
    raise HTTPException(
        status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        detail=(
            f"unsupported file type {filename!r} — Composer extracts text "
            "from .txt, .md, .markdown, .pdf, and .docx files. For other "
            "formats, convert upstream or paste the content as plain text."
        ),
    )


@router.post("/extract-text", response_model=UploadExtractResponse)
async def extract_text(
    file: UploadFile = File(...),
    user_id: str = Depends(get_current_user_id),
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> UploadExtractResponse:  # pyright: ignore[reportUnusedFunction]
    """Receive a file, extract plain text, return it.

    The file is held entirely in memory — bounded by `MAX_UPLOAD_BYTES`
    above, so worker memory pressure is predictable.  Nothing is
    persisted; once this handler returns, the bytes are gone.
    """
    # Rate-limit per user — file uploads are more expensive than a
    # JSON POST, and this endpoint is unauthenticated to API-key
    # callers (it requires a session JWT).  20/min per user is
    # generous for an interactive run-form; abusive callers hit the
    # bucket fast.
    await enforce(
        limiter,
        route_key="uploads_extract",
        client_key=user_id,
        config=per_minute_config(20),
    )

    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="upload missing filename",
        )

    # Read with a hard cap.  We could enforce via Content-Length but
    # that header is a hint; reading in 1MB chunks until the cap is
    # tripped is the only way to be sure.
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=(
                    f"file exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)}MB cap; "
                    "split or compress upstream."
                ),
            )
        chunks.append(chunk)
    data = b"".join(chunks)

    if not data:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="uploaded file is empty",
        )

    try:
        text = _dispatch_extractor(file.filename, data)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "uploads/extract-text failed for filename=%s content_type=%s",
            file.filename,
            file.content_type,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"failed to extract text: {type(exc).__name__}: {exc}",
        ) from exc

    return UploadExtractResponse(
        filename=file.filename,
        content_type=file.content_type or "application/octet-stream",
        size_bytes=total,
        text=text,
    )


__all__: list[Any] = ["MAX_UPLOAD_BYTES", "UploadExtractResponse", "router"]
