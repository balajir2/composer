"""Tests for the ConfluenceNode/ConfluenceNodeData schema."""

from typing import Any

from src.engine.workflow import ConfluenceNode


def _confluence_node_json(**data_overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "label": "Confluence",
        "domain": "test.atlassian.net",
        "email": "test@example.com",
        "apiToken": "plaintext-token",
        "operation": "create_or_update_page",
    }
    data.update(data_overrides)
    return {"id": "c1", "type": "confluence", "position": {"x": 0, "y": 0}, "data": data}


def test_defaults_to_create_or_update_page() -> None:
    node = ConfluenceNode.model_validate(_confluence_node_json())
    assert node.data.operation == "create_or_update_page"


def test_camelcase_fields_parse_with_aliases() -> None:
    node = ConfluenceNode.model_validate(
        _confluence_node_json(
            operation="get_property",
            spaceKey="MB",
            parentPageId="100",
            bodyStorageHtml="<p>hi</p>",
            pageId="123",
            propertyKey="metrics_snapshot",
            propertyValue={"total": 3},
            labels=["weekly-report"],
        )
    )
    assert node.data.space_key == "MB"
    assert node.data.parent_page_id == "100"
    assert node.data.body_storage_html == "<p>hi</p>"
    assert node.data.page_id == "123"
    assert node.data.property_key == "metrics_snapshot"
    assert node.data.property_value == {"total": 3}
    assert node.data.labels == ["weekly-report"]
