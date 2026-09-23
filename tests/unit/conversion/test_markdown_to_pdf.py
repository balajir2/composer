"""Tests for the Markdown -> PDF renderer.

xhtml2pdf output isn't practical to assert on structurally the way python-
docx's object model is — these tests check for a well-formed PDF (correct
magic bytes, non-trivial size) rather than parsing rendered content.
"""

import pytest


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


def test_conversion_failure_raises_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.conversion.markdown_to_pdf as mod
    from src.conversion.markdown_to_pdf import MarkdownToPdfError, markdown_to_pdf

    class _FakeStatus:
        # xhtml2pdf's pisaDocument returns a status object with .err; simulate failure.
        err = 1

    monkeypatch.setattr(
        mod, "pisa", type("P", (), {"CreatePDF": staticmethod(lambda *a, **kw: _FakeStatus())})
    )

    with pytest.raises(MarkdownToPdfError):
        markdown_to_pdf("# Title")


def test_ssrf_image_reference_does_not_trigger_network_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A markdown image pointing at a cloud-metadata-style address (the same
    169.254.169.254 target the HTTP node's SSRF guard blocks, kept here for a
    consistent threat-model reference) must not cause any outbound network
    I/O when rendered through markdown_to_pdf(). Proven by monkeypatching
    the exact seam that performs the real HTTP GET --
    xhtml2pdf.files.NetworkFileUri.get_httplib -- to raise if ever invoked.
    Without a link_callback wired into pisa.CreatePDF, xhtml2pdf's own
    <img>-tag resolution (files.py: FileNetworkManager/NetworkFileUri) would
    reach this exact method and perform a real GET.
    """
    from xhtml2pdf.files import NetworkFileUri  # pyright: ignore[reportMissingTypeStubs]

    from src.conversion.markdown_to_pdf import markdown_to_pdf

    calls: list[str] = []

    def _fail_if_called(self: object, uri: str) -> tuple[bytes | None, bool]:  # type: ignore[no-untyped-def]
        calls.append(uri)
        raise AssertionError(f"unexpected outbound network fetch attempted for {uri!r}")

    monkeypatch.setattr(NetworkFileUri, "get_httplib", _fail_if_called)

    content = "![metadata](http://169.254.169.254/latest/meta-data/)"
    result = markdown_to_pdf(content)

    assert calls == []
    assert result.startswith(b"%PDF-")


def test_bracket_placeholder_survives_in_rendered_html(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`<Client Name>`-style bracket placeholders (common in the BRDs this
    node targets) must not be swallowed by CommonMark's raw-HTML passthrough
    grammar -- they should reach xhtml2pdf as literal, HTML-escaped text
    (`&lt;Client Name&gt;`), not vanish as an unrecognized inline HTML tag.
    Checked at the HTML-before-PDF stage (captured via a monkeypatched
    pisa.CreatePDF) since rendered PDF bytes aren't structurally inspectable.
    """
    import src.conversion.markdown_to_pdf as mod

    captured: dict[str, str] = {}

    class _FakeStatus:
        err = 0

    def _capture_create_pdf(src: object, dest: object = None, **_kwargs: object) -> _FakeStatus:
        captured["html"] = src.getvalue()  # type: ignore[union-attr]
        return _FakeStatus()

    monkeypatch.setattr(
        mod, "pisa", type("P", (), {"CreatePDF": staticmethod(_capture_create_pdf)})
    )

    mod.markdown_to_pdf("**Client:** <Client Name>")

    assert "&lt;Client Name&gt;" in captured["html"]
    assert "<Client Name>" not in captured["html"]
