"""Shared plain-text extraction for claimed files — used by both
`composer watch` (src/cli/watch.py) and the Google Drive poller
(src/api/internal.py's poll_file_triggers), so both watchers extract
text identically."""

import io


class UnsupportedFileTypeError(ValueError):
    """Raised when a claimed file's extension has no known text-extraction path."""


def extract_text(filename: str, raw: bytes) -> str:
    lower = filename.lower()
    if lower.endswith((".txt", ".md", ".markdown")):
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("utf-8-sig")
    if lower.endswith(".pdf"):
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(raw))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    if lower.endswith(".docx"):
        from docx import Document

        doc = Document(io.BytesIO(raw))
        return "\n".join(p.text for p in doc.paragraphs)
    raise UnsupportedFileTypeError(f"no extraction path for {filename!r}")


__all__ = ["UnsupportedFileTypeError", "extract_text"]
