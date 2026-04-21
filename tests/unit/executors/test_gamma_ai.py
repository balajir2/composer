"""Tests for the gamma-ai executor (mocked Gamma API via pytest-httpx)."""

from typing import Any

import pytest
from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]

from src.engine.state import initial_state
from src.engine.workflow import GammaAiNode
from src.executors.gamma_ai import (
    GAMMA_API_BASE,
    GammaAiExecutor,
    GammaNodeError,
)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:  # pyright: ignore[reportUnusedFunction]
    """Make asyncio.sleep a no-op so tests don't actually wait 60s."""
    import asyncio

    async def _instant(*_a: Any, **_kw: Any) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _instant)


@pytest.fixture(autouse=True)
def _stub_api_key(monkeypatch: pytest.MonkeyPatch) -> None:  # pyright: ignore[reportUnusedFunction]
    """Provide a fake API key so tests don't hit 'GAMMA_API_KEY not configured'."""
    from src.config import get_settings

    monkeypatch.setenv("GAMMA_API_KEY", "test-key-123")
    get_settings.cache_clear()


def _node(**overrides: Any) -> GammaAiNode:
    data: dict[str, Any] = {"label": "GA", "prompt": "Make a deck"}
    data.update(overrides)
    return GammaAiNode.model_validate(
        {
            "id": "ga",
            "type": "gamma-ai",
            "position": {"x": 0, "y": 0},
            "data": data,
        }
    )


async def test_successful_create_and_immediate_complete(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(
        method="POST",
        url=f"{GAMMA_API_BASE}/generations",
        json={"id": "gen_123"},
        status_code=200,
    )
    httpx_mock.add_response(
        method="GET",
        url=f"{GAMMA_API_BASE}/generations/gen_123",
        json={"state": "completed", "gammaUrl": "https://gamma.app/docs/gen_123"},
        status_code=200,
    )

    delta = await GammaAiExecutor(_node()).arun(initial_state())
    assert delta["variables"]["lastOutput"] == "https://gamma.app/docs/gen_123"
    output = delta["node_results"]["ga"]["output"]
    assert output["generationId"] == "gen_123"
    assert output["status"] == "completed"


async def test_generation_id_from_nested_data_key(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    """Parse generationId from data.id when top-level id/generationId missing."""
    httpx_mock.add_response(
        method="POST",
        url=f"{GAMMA_API_BASE}/generations",
        json={"data": {"id": "nested_id"}},
        status_code=200,
    )
    httpx_mock.add_response(
        method="GET",
        url=f"{GAMMA_API_BASE}/generations/nested_id",
        json={"state": "completed", "gammaUrl": "https://x"},
        status_code=200,
    )
    delta = await GammaAiExecutor(_node()).arun(initial_state())
    assert delta["node_results"]["ga"]["output"]["generationId"] == "nested_id"


async def test_polling_sees_pending_then_completed(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(
        method="POST",
        url=f"{GAMMA_API_BASE}/generations",
        json={"id": "gen_x"},
        status_code=200,
    )
    httpx_mock.add_response(
        method="GET",
        url=f"{GAMMA_API_BASE}/generations/gen_x",
        json={"state": "pending"},
        status_code=200,
    )
    httpx_mock.add_response(
        method="GET",
        url=f"{GAMMA_API_BASE}/generations/gen_x",
        json={"state": "pending"},
        status_code=200,
    )
    httpx_mock.add_response(
        method="GET",
        url=f"{GAMMA_API_BASE}/generations/gen_x",
        json={"state": "completed", "gammaUrl": "https://ok"},
        status_code=200,
    )
    delta = await GammaAiExecutor(_node()).arun(initial_state())
    assert delta["variables"]["lastOutput"] == "https://ok"


async def test_polling_sees_failed_raises(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(
        method="POST",
        url=f"{GAMMA_API_BASE}/generations",
        json={"id": "gen_f"},
        status_code=200,
    )
    httpx_mock.add_response(
        method="GET",
        url=f"{GAMMA_API_BASE}/generations/gen_f",
        json={"state": "failed", "error": "ran out of quota"},
        status_code=200,
    )
    with pytest.raises(GammaNodeError, match="quota"):
        await GammaAiExecutor(_node()).arun(initial_state())


async def test_export_wait_succeeds_with_download_url(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(
        method="POST",
        url=f"{GAMMA_API_BASE}/generations",
        json={"id": "gen_e"},
        status_code=200,
    )
    # Completion response has no downloadUrl yet
    httpx_mock.add_response(
        method="GET",
        url=f"{GAMMA_API_BASE}/generations/gen_e",
        json={"state": "completed", "gammaUrl": "https://web"},
        status_code=200,
    )
    # Export-wait first poll: no downloadUrl
    httpx_mock.add_response(
        method="GET",
        url=f"{GAMMA_API_BASE}/generations/gen_e",
        json={"state": "completed", "gammaUrl": "https://web"},
        status_code=200,
    )
    # Export-wait second poll: downloadUrl ready
    httpx_mock.add_response(
        method="GET",
        url=f"{GAMMA_API_BASE}/generations/gen_e",
        json={
            "state": "completed",
            "gammaUrl": "https://web",
            "downloadUrl": "https://cdn.gamma.app/export.pptx",
        },
        status_code=200,
    )
    delta = await GammaAiExecutor(_node(exportAs="pptx")).arun(initial_state())
    assert delta["variables"]["lastOutput"] == "https://cdn.gamma.app/export.pptx"


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
async def test_export_wait_timeout_returns_web_url(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    """Shrink EXPORT_WAIT_SECONDS so the test exits the export loop quickly.

    The export loop may fire zero or many polls before the deadline expires
    (sleep is a no-op, deadline is 0.001s). Use is_reusable=True so any
    number of GET calls are handled without exhausting the mock.
    """
    import src.executors.gamma_ai as gm

    monkeypatch.setattr(gm, "EXPORT_WAIT_SECONDS", 0.001)

    httpx_mock.add_response(
        method="POST",
        url=f"{GAMMA_API_BASE}/generations",
        json={"id": "gen_t"},
        status_code=200,
    )
    # Completion poll: one response, consumed exactly once.
    httpx_mock.add_response(
        method="GET",
        url=f"{GAMMA_API_BASE}/generations/gen_t",
        json={"state": "completed", "gammaUrl": "https://fallback"},
        status_code=200,
    )
    # Export-wait polls: reusable — handles zero or many calls depending on timing.
    httpx_mock.add_response(
        method="GET",
        url=f"{GAMMA_API_BASE}/generations/gen_t",
        json={"state": "completed", "gammaUrl": "https://fallback"},
        status_code=200,
        is_reusable=True,
        is_optional=True,
    )
    delta = await GammaAiExecutor(_node(exportAs="pptx")).arun(initial_state())
    assert delta["variables"]["lastOutput"] == "https://fallback"


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
async def test_polling_timeout_with_short_deadline(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    """Shrink MAX_POLL_SECONDS so the polling loop exits fast when generation
    never completes. Executor returns last-known status without raising.

    With sleep as a no-op and deadline 0.001s, the while loop may spin many
    times before expiring. Use is_reusable=True on the in-loop GET, and a
    separate response for the post-timeout final GET.
    """
    import src.executors.gamma_ai as gm

    monkeypatch.setattr(gm, "MAX_POLL_SECONDS", 0.001)

    httpx_mock.add_response(
        method="POST",
        url=f"{GAMMA_API_BASE}/generations",
        json={"id": "gen_s"},
        status_code=200,
    )
    # In-loop polls (zero or many) — reusable so any number of iterations work.
    httpx_mock.add_response(
        method="GET",
        url=f"{GAMMA_API_BASE}/generations/gen_s",
        json={"state": "processing", "message": "still working"},
        status_code=200,
        is_reusable=True,
    )
    delta = await GammaAiExecutor(_node()).arun(initial_state())
    assert delta["variables"]["lastOutput"] == ""  # no URL available
    assert delta["node_results"]["ga"]["output"]["status"] == "processing"


async def test_transient_502_retries(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    """A transient 502 during polling is retried; next poll succeeds."""
    httpx_mock.add_response(
        method="POST",
        url=f"{GAMMA_API_BASE}/generations",
        json={"id": "gen_r"},
        status_code=200,
    )
    httpx_mock.add_response(
        method="GET",
        url=f"{GAMMA_API_BASE}/generations/gen_r",
        status_code=502,
        text="bad gateway",
    )
    httpx_mock.add_response(
        method="GET",
        url=f"{GAMMA_API_BASE}/generations/gen_r",
        json={"state": "completed", "gammaUrl": "https://ok"},
        status_code=200,
    )
    delta = await GammaAiExecutor(_node()).arun(initial_state())
    assert delta["variables"]["lastOutput"] == "https://ok"


async def test_missing_api_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.config import get_settings

    monkeypatch.setenv("GAMMA_API_KEY", "")
    get_settings.cache_clear()

    with pytest.raises(GammaNodeError, match="GAMMA_API_KEY"):
        await GammaAiExecutor(_node()).arun(initial_state())


async def test_variable_substitution_in_prompt(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(
        method="POST",
        url=f"{GAMMA_API_BASE}/generations",
        json={"id": "gen_v"},
        status_code=200,
    )
    httpx_mock.add_response(
        method="GET",
        url=f"{GAMMA_API_BASE}/generations/gen_v",
        json={"state": "completed", "gammaUrl": "https://x"},
        status_code=200,
    )
    state = initial_state()
    state["variables"]["topic"] = "dogs"
    await GammaAiExecutor(_node(prompt="Deck about {{topic}}")).arun(state)

    import json

    post_request = httpx_mock.get_requests(
        method="POST",
        url=f"{GAMMA_API_BASE}/generations",
    )[0]
    body = json.loads(post_request.content)
    assert body["inputText"] == "Deck about dogs"


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
async def test_request_body_optional_fields(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    """exportAs='pdf' triggers the export-wait loop; use is_reusable=True on the
    GET response so sleep-as-no-op doesn't exhaust the single registered mock."""
    httpx_mock.add_response(
        method="POST",
        url=f"{GAMMA_API_BASE}/generations",
        json={"id": "gen_o"},
        status_code=200,
    )
    # Reusable: completion poll + any export-wait polls both consume this.
    httpx_mock.add_response(
        method="GET",
        url=f"{GAMMA_API_BASE}/generations/gen_o",
        json={"state": "completed", "gammaUrl": "https://x"},
        status_code=200,
        is_reusable=True,
    )
    node = _node(
        numCards=10,
        textAmount="detailed",
        imageSource="unsplash",
        language="es",
        exportAs="pdf",
    )
    await GammaAiExecutor(node).arun(initial_state())

    import json

    post_request = httpx_mock.get_requests(
        method="POST",
        url=f"{GAMMA_API_BASE}/generations",
    )[0]
    body = json.loads(post_request.content)
    assert body["numCards"] == 10
    assert body["textOptions"] == {"amount": "detailed", "language": "es"}
    assert body["imageOptions"] == {"source": "unsplash"}
    assert body["exportAs"] == "pdf"


async def test_request_body_web_export_omits_export_as(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    """When export_as='web' (the default), the field is NOT included in the request."""
    httpx_mock.add_response(
        method="POST",
        url=f"{GAMMA_API_BASE}/generations",
        json={"id": "gen_w"},
        status_code=200,
    )
    httpx_mock.add_response(
        method="GET",
        url=f"{GAMMA_API_BASE}/generations/gen_w",
        json={"state": "completed", "gammaUrl": "https://x"},
        status_code=200,
    )
    await GammaAiExecutor(_node()).arun(initial_state())

    import json

    post_request = httpx_mock.get_requests(
        method="POST",
        url=f"{GAMMA_API_BASE}/generations",
    )[0]
    body = json.loads(post_request.content)
    assert "exportAs" not in body


async def test_executor_is_registered() -> None:
    import src.executors.gamma_ai  # noqa: F401  # pyright: ignore[reportUnusedImport]
    from src.executors.base import build_executor

    node = _node()
    executor = build_executor(node)
    assert isinstance(executor, GammaAiExecutor)
