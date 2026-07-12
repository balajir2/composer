"""Tests for the Markdown -> PDF renderer.

xhtml2pdf output isn't practical to assert on structurally the way python-
docx's object model is — these tests check for a well-formed PDF (correct
magic bytes, non-trivial size) rather than parsing rendered content.
"""


def test_produces_valid_pdf_bytes() -> None:
    from src.conversion.markdown_to_pdf import markdown_to_pdf

    result = markdown_to_pdf("# Title\n\nSome body text.\n\n- one\n- two")
    assert result.startswith(b"%PDF-")
    assert len(result) > 100


def test_empty_content_still_produces_valid_pdf() -> None:
    from src.conversion.markdown_to_pdf import markdown_to_pdf

    result = markdown_to_pdf("")
    assert result.startswith(b"%PDF-")


def test_table_content_does_not_raise() -> None:
    from src.conversion.markdown_to_pdf import markdown_to_pdf

    md = "| A | B |\n|---|---|\n| 1 | 2 |"
    result = markdown_to_pdf(md)
    assert result.startswith(b"%PDF-")


def test_conversion_failure_raises_clear_error(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import src.conversion.markdown_to_pdf as mod
    from src.conversion.markdown_to_pdf import MarkdownToPdfError, markdown_to_pdf

    class _FakeStatus:
        # xhtml2pdf's pisaDocument returns a status object with .err; simulate failure.
        err = 1

    monkeypatch.setattr(mod, "pisa", type("P", (), {"CreatePDF": staticmethod(lambda *a, **kw: _FakeStatus())}))
    import pytest

    with pytest.raises(MarkdownToPdfError):
        markdown_to_pdf("# Title")
