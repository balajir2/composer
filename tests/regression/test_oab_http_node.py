"""Regression — HTTP node behavioural contract (ported from OAB parity).

OAB reference: lib/workflow/executors/http.ts
  - Non-2xx → execution failure
  - JSON response → parsed; otherwise text
  - Header / URL / body accept template substitution

Mocked — no real external calls.
"""

from typing import Any

import pytest
from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]

from src.engine.state import initial_state
from src.engine.workflow import HttpNode
from src.executors.http import HttpExecutor, HttpNodeError

pytestmark = pytest.mark.integration


def _http_node(**data: Any) -> HttpNode:
    return HttpNode.model_validate(
        {
            "id": "h",
            "type": "http",
            "position": {"x": 0, "y": 0},
            "data": {"label": "H", **data},
        }
    )


async def test_oab_regression_json_response_parsed(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        url="https://api.example.test/v1/thing",
        method="GET",
        json={"id": 1, "status": "ok"},
    )
    node = _http_node(httpMethod="GET", httpUrl="https://api.example.test/v1/thing")
    delta = await HttpExecutor(node).arun(initial_state())
    assert delta["variables"]["lastOutput"] == {"id": 1, "status": "ok"}


async def test_oab_regression_non_2xx_fails(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        url="https://api.example.test/fail",
        method="GET",
        status_code=500,
        text="Internal Server Error",
    )
    node = _http_node(httpMethod="GET", httpUrl="https://api.example.test/fail")
    with pytest.raises(HttpNodeError, match="500"):
        await HttpExecutor(node).arun(initial_state())


async def test_oab_regression_substitutes_template_in_url(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        url="https://api.example.test/users/42",
        method="GET",
        json={"id": 42},
    )
    node = _http_node(
        httpMethod="GET",
        httpUrl="https://api.example.test/users/{{user_id}}",
    )
    state = initial_state()
    state["variables"]["user_id"] = 42
    delta = await HttpExecutor(node).arun(state)
    assert delta["variables"]["lastOutput"] == {"id": 42}
