"""Tests for the DownloadPdfNode/DownloadPdfNodeData schema."""

from typing import Any

import pytest
from pydantic import ValidationError

from src.engine.workflow import DownloadPdfNode


def _download_pdf_node_json(**data_overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "label": "Download PDF",
        "inputFormat": "html",
        "content": "<html><body>Report</body></html>",
        "destinationPath": "/out",
        "filename": "report",
    }
    data.update(data_overrides)
    return {"id": "dp1", "type": "download-pdf", "position": {"x": 0, "y": 0}, "data": data}


def test_camelcase_fields_parse_with_aliases() -> None:
    node = DownloadPdfNode.model_validate(_download_pdf_node_json())
    assert node.data.input_format == "html"
    assert node.data.destination_path == "/out"
    assert node.data.filename == "report"


def test_markdown_input_format_accepted() -> None:
    node = DownloadPdfNode.model_validate(_download_pdf_node_json(inputFormat="markdown"))
    assert node.data.input_format == "markdown"


def test_input_format_is_required_no_default() -> None:
    payload = _download_pdf_node_json()
    del payload["data"]["inputFormat"]
    with pytest.raises(ValidationError):
        DownloadPdfNode.model_validate(payload)


def test_provider_defaults_to_local() -> None:
    node = DownloadPdfNode.model_validate(_download_pdf_node_json())
    assert node.data.provider == "local"


def test_google_drive_fields_parse_with_aliases() -> None:
    node = DownloadPdfNode.model_validate(
        _download_pdf_node_json(
            provider="google-drive",
            connectionId="conn-1",
            driveFolderId="folder-1",
        )
    )
    assert node.data.provider == "google-drive"
    assert node.data.connection_id == "conn-1"
    assert node.data.drive_folder_id == "folder-1"


def test_google_drive_fields_default_to_none() -> None:
    node = DownloadPdfNode.model_validate(_download_pdf_node_json())
    assert node.data.connection_id is None
    assert node.data.drive_folder_id is None
