"""Tests for the HTML -> PDF renderer.

Mirrors tests/unit/conversion/test_markdown_to_pdf.py's pattern -- xhtml2pdf
output isn't practical to assert on structurally, so these check for a
well-formed PDF (correct magic bytes) rather than parsing rendered content.
"""

import pytest


def test_produces_valid_pdf_bytes() -> None:
    from src.conversion.html_to_pdf import html_to_pdf

    result = html_to_pdf("<html><body><h1>Title</h1><p>Some body text.</p></body></html>")
    assert result.startswith(b"%PDF-")
    assert len(result) > 100


def test_empty_content_still_produces_valid_pdf() -> None:
    from src.conversion.html_to_pdf import html_to_pdf

    result = html_to_pdf("")
    assert result.startswith(b"%PDF-")


def test_table_content_does_not_raise() -> None:
    from src.conversion.html_to_pdf import html_to_pdf

    html = "<html><body><table><tr><td>A</td><td>B</td></tr></table></body></html>"
    result = html_to_pdf(html)
    assert result.startswith(b"%PDF-")


def test_conversion_failure_raises_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.conversion.html_to_pdf as mod
    from src.conversion.html_to_pdf import HtmlToPdfError, html_to_pdf

    class _FakeStatus:
        err = 1

    monkeypatch.setattr(
        mod, "pisa", type("P", (), {"CreatePDF": staticmethod(lambda *a, **kw: _FakeStatus())})
    )

    with pytest.raises(HtmlToPdfError):
        html_to_pdf("<html><body>Title</body></html>")


def test_ssrf_image_reference_does_not_trigger_network_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An <img> tag pointing at a cloud-metadata-style address must not
    cause any outbound network I/O when rendered through html_to_pdf().
    Same threat model and same verification seam as
    test_markdown_to_pdf.py's identically-named test."""
    from xhtml2pdf.files import NetworkFileUri  # pyright: ignore[reportMissingTypeStubs]

    from src.conversion.html_to_pdf import html_to_pdf

    calls: list[str] = []

    def _fail_if_called(self: object, uri: str) -> tuple[bytes | None, bool]:  # type: ignore[no-untyped-def]
        calls.append(uri)
        raise AssertionError(f"unexpected outbound network fetch attempted for {uri!r}")

    monkeypatch.setattr(NetworkFileUri, "get_httplib", _fail_if_called)

    content = '<html><body><img src="http://169.254.169.254/latest/meta-data/"></body></html>'
    result = html_to_pdf(content)

    assert calls == []
    assert result.startswith(b"%PDF-")
