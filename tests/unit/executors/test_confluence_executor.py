"""Tests for the Confluence executor — deterministic create/update/property operations."""

from typing import Any

import pytest
from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]

from src.engine.state import initial_state
from src.engine.workflow import ConfluenceNode
from src.executors.confluence import (
    ConfluenceConfigError,
    ConfluenceExecutor,
    ConfluenceHttpError,
)
from src.security.encryption import encrypt_marked


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


async def test_create_or_update_page_creates_when_no_existing_page(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    # Only 2 responses registered: find-by-title (none) + create POST. A
    # freshly created page cannot already have labels, so the create path
    # must NOT issue a GET to the labels endpoint before deciding what to
    # add — pytest_httpx raises if a request has no matching registered
    # response, so an extra unwanted GET would fail this test.
    httpx_mock.add_response(
        method="GET", json={"results": []}
    )  # find-by-title: none  # pyright: ignore[reportUnknownMemberType]
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        method="POST",
        url="https://test.atlassian.net/wiki/rest/api/content",
        json={"id": "123", "version": {"number": 1}},
    )

    node = ConfluenceNode.model_validate(
        _confluence_node_json(
            spaceKey="MB",
            parentPageId="100",
            title="MB - Weekly Delivery Report - 2026-07-19",
            bodyStorageHtml="<p>hello</p>",
        )
    )
    delta = await ConfluenceExecutor(node).arun(initial_state())
    output = delta["variables"]["lastOutput"]
    assert output["pageId"] == "123"
    assert output["version"] == 1
    assert output["created"] is True
    assert "123" in output["url"]


async def test_create_page_with_labels_adds_without_a_preceding_get(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    """Create path with a non-empty labels list: still no GET to the labels
    endpoint (known_current=set() short-circuits it), straight to POST."""
    httpx_mock.add_response(
        method="GET", json={"results": []}
    )  # find-by-title: none  # pyright: ignore[reportUnknownMemberType]
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        method="POST",
        url="https://test.atlassian.net/wiki/rest/api/content",
        json={"id": "123", "version": {"number": 1}},
    )
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        method="POST",
        url="https://test.atlassian.net/wiki/rest/api/content/123/label",
        json={"results": []},
    )

    node = ConfluenceNode.model_validate(
        _confluence_node_json(
            spaceKey="MB",
            title="MB - Weekly Delivery Report - 2026-07-19",
            bodyStorageHtml="<p>hello</p>",
            labels=["weekly-report"],
        )
    )
    delta = await ConfluenceExecutor(node).arun(initial_state())
    output = delta["variables"]["lastOutput"]
    assert output["pageId"] == "123"
    assert output["created"] is True

    requests = httpx_mock.get_requests()  # pyright: ignore[reportUnknownMemberType]
    label_requests = [
        r for r in requests if r.url.path.endswith("/label") or "/label" in str(r.url)
    ]
    assert all(r.method != "GET" for r in label_requests)


async def test_create_or_update_page_updates_and_reconciles_labels(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    httpx_mock.add_response(  # find-by-title: existing page  # pyright: ignore[reportUnknownMemberType]
        method="GET",
        json={"results": [{"id": "123", "version": {"number": 2}}]},
    )
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        method="PUT",
        url="https://test.atlassian.net/wiki/rest/api/content/123",
        json={"id": "123", "version": {"number": 3}},
    )
    httpx_mock.add_response(  # existing labels: one to keep out, one to remove  # pyright: ignore[reportUnknownMemberType]
        method="GET",
        json={"results": [{"name": "stale-label", "prefix": "global"}]},
    )
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        method="DELETE",
        url="https://test.atlassian.net/wiki/rest/api/content/123/label/stale-label",
        json={},
    )
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        method="POST",
        url="https://test.atlassian.net/wiki/rest/api/content/123/label",
        json={"results": []},
    )

    node = ConfluenceNode.model_validate(
        _confluence_node_json(
            spaceKey="MB",
            title="MB - Weekly Delivery Report - 2026-07-19",
            bodyStorageHtml="<p>updated</p>",
            labels=["weekly-report"],
        )
    )
    delta = await ConfluenceExecutor(node).arun(initial_state())
    output = delta["variables"]["lastOutput"]
    assert output["pageId"] == "123"
    assert output["version"] == 3
    assert output["created"] is False


async def test_get_page_returns_found_false_when_no_match(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    httpx_mock.add_response(method="GET", json={"results": []})  # pyright: ignore[reportUnknownMemberType]

    node = ConfluenceNode.model_validate(
        _confluence_node_json(operation="get_page", spaceKey="MB", title="Not There Yet")
    )
    delta = await ConfluenceExecutor(node).arun(initial_state())
    output = delta["variables"]["lastOutput"]
    assert output == {"found": False, "pageId": None, "bodyStorageHtml": None, "version": None}


async def test_get_page_returns_body_when_found(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        method="GET",
        json={
            "results": [
                {
                    "id": "123",
                    "version": {"number": 4},
                    "body": {"storage": {"value": "<p>existing content</p>"}},
                }
            ]
        },
    )

    node = ConfluenceNode.model_validate(
        _confluence_node_json(operation="get_page", spaceKey="MB", title="Weekly Report")
    )
    delta = await ConfluenceExecutor(node).arun(initial_state())
    output = delta["variables"]["lastOutput"]
    assert output == {
        "found": True,
        "pageId": "123",
        "bodyStorageHtml": "<p>existing content</p>",
        "version": 4,
    }


async def test_get_property_returns_found_false_on_404(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        method="GET",
        url="https://test.atlassian.net/wiki/rest/api/content/123/property/metrics_snapshot",
        status_code=404,
        json={},
    )
    node = ConfluenceNode.model_validate(
        _confluence_node_json(
            operation="get_property", pageId="123", propertyKey="metrics_snapshot"
        )
    )
    delta = await ConfluenceExecutor(node).arun(initial_state())
    assert delta["variables"]["lastOutput"] == {"found": False, "value": None}


async def test_set_property_creates_when_missing(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    httpx_mock.add_response(  # existence check: not found  # pyright: ignore[reportUnknownMemberType]
        method="GET",
        url="https://test.atlassian.net/wiki/rest/api/content/123/property/metrics_snapshot",
        status_code=404,
        json={},
    )
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        method="POST",
        url="https://test.atlassian.net/wiki/rest/api/content/123/property",
        json={
            "key": "metrics_snapshot",
            "version": {"number": 1, "when": "2026-07-19T00:00:00.000Z"},
        },
    )
    node = ConfluenceNode.model_validate(
        _confluence_node_json(
            operation="set_property",
            pageId="123",
            propertyKey="metrics_snapshot",
            propertyValue={"total": 3},
        )
    )
    delta = await ConfluenceExecutor(node).arun(initial_state())
    output = delta["variables"]["lastOutput"]
    assert output["key"] == "metrics_snapshot"


async def test_set_property_updates_when_existing(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        method="GET",
        url="https://test.atlassian.net/wiki/rest/api/content/123/property/metrics_snapshot",
        json={"key": "metrics_snapshot", "value": {"total": 2}, "version": {"number": 1}},
    )
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        method="PUT",
        url="https://test.atlassian.net/wiki/rest/api/content/123/property/metrics_snapshot",
        json={
            "key": "metrics_snapshot",
            "version": {"number": 2, "when": "2026-07-19T00:00:00.000Z"},
        },
    )
    node = ConfluenceNode.model_validate(
        _confluence_node_json(
            operation="set_property",
            pageId="123",
            propertyKey="metrics_snapshot",
            propertyValue={"total": 3},
        )
    )
    delta = await ConfluenceExecutor(node).arun(initial_state())
    output = delta["variables"]["lastOutput"]
    assert output["key"] == "metrics_snapshot"


async def test_requires_credentials() -> None:
    node = ConfluenceNode.model_validate(
        _confluence_node_json(
            domain="", email="", apiToken="", operation="get_page", spaceKey="MB", title="X"
        )
    )
    with pytest.raises(ConfluenceConfigError):
        await ConfluenceExecutor(node).arun(initial_state())


async def test_decrypts_stored_token_before_use(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    encrypted = encrypt_marked("real-secret-token")
    httpx_mock.add_response(method="GET", json={"results": []})  # pyright: ignore[reportUnknownMemberType]

    node = ConfluenceNode.model_validate(
        _confluence_node_json(apiToken=encrypted, operation="get_page", spaceKey="MB", title="X")
    )
    await ConfluenceExecutor(node).arun(initial_state())

    req = httpx_mock.get_request()  # pyright: ignore[reportUnknownMemberType]
    assert req is not None
    import base64

    auth_header = req.headers["Authorization"]
    assert auth_header.startswith("Basic ")
    decoded = base64.b64decode(auth_header[len("Basic ") :]).decode()
    assert decoded == "test@example.com:real-secret-token"


async def test_non_2xx_non_404_raises_confluence_http_error(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    httpx_mock.add_response(method="GET", status_code=500, text="server error")  # pyright: ignore[reportUnknownMemberType]

    node = ConfluenceNode.model_validate(
        _confluence_node_json(operation="get_page", spaceKey="MB", title="X")
    )
    with pytest.raises(ConfluenceHttpError):
        await ConfluenceExecutor(node).arun(initial_state())
