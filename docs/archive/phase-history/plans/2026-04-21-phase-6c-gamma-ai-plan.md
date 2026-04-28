# Phase 6c — Gamma-AI: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development`. Steps use checkbox (`- [ ]`).

**Goal:** Ship `gamma-ai` executor — HTTP integration with Gamma.app public API. POST create → poll GET → optional export-URL wait. `lastOutput = downloadUrl || gammaUrl`.

**Architecture.** `httpx.AsyncClient` with `X-API-KEY` header. OAB-compatible polling cadence. Runs inline in the BackgroundTask (no interrupt). Sleep durations configurable-at-module-scope so tests can monkeypatch.

**Tech Stack:** httpx (already in deps), pytest-httpx (already in deps), existing executor registry + `substitute()`.

**Spec:** [`docs/superpowers/specs/2026-04-21-phase-6c-gamma-ai-design.md`](../specs/2026-04-21-phase-6c-gamma-ai-design.md)

---

## Sequencing + discipline

6 tasks, one commit each. Every task ends green on:

```bash
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m ruff format --check src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

**⚠️ Forbidden files:** `pyproject.toml`, `.github/workflows/*`, `CLAUDE.md` (except Task 6), `docs/design/*` (no new ADR this phase), `docs/superpowers/plans/*`, `docs/superpowers/specs/*`, Prisma schema + migrations.

Sentinel-test authorization: when adding `gamma-ai` to the executor registry, the existing `test_registry.py` / `test_graph_builder.py` / `test_langgraph_executor.py` "unshipped Phase 6 type" sentinel tests might use `gamma-ai` as their example. If they do, migrate to a still-unshipped type (`arcade` or `vector-db`). This is authorized — same precedent as Phase 5a / Phase 6b.

Commit footer:
```
Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
```

Stay on `main`.

---

## Task 1: Tighten `GammaAiNodeData` + add `settings.gamma_api_key`

**Files:**
- Modify: `src/engine/workflow.py`
- Modify: `src/config.py`
- Modify: `tests/unit/engine/test_workflow_models.py` (append)

- [ ] **Step 1: Tighten `GammaAiNodeData`**

Current (`src/engine/workflow.py` around lines 306-308):
```python
class GammaAiNodeData(BaseNodeData):
    # TODO(phase-6): tighten against OAB's Gamma AI node fields.
    config: dict[str, Any] = Field(default_factory=dict)
```

Replace with:
```python
class GammaAiNodeData(BaseNodeData):
    model_config = ConfigDict(populate_by_name=True)

    prompt: str | None = Field(default=None)
    format: Literal["presentation", "document", "social"] = Field(default="presentation")
    text_mode: Literal["generate", "condense", "preserve"] = Field(
        default="generate", alias="textMode"
    )
    num_cards: int | None = Field(default=None, alias="numCards")
    text_amount: Literal["brief", "medium", "detailed"] | None = Field(
        default=None, alias="textAmount"
    )
    image_source: str | None = Field(default=None, alias="imageSource")
    language: str | None = Field(default=None)
    export_as: Literal["pptx", "pdf", "web"] = Field(default="web", alias="exportAs")
```

`ConfigDict` and `Literal` should be imported already. If not, add them.

- [ ] **Step 2: Add `gamma_api_key` to `src/config.py`**

After the existing `# ─── Agent tools (Phase 2) ───` block, add (or alongside the existing API key settings):

```python
    # ─── Gamma-AI (Phase 6c) ─────────────────────
    gamma_api_key: str = Field(default="", description="Gamma.app public API key.")
```

Alphabetize appropriately. Keep the existing env-loading pattern (no `validation_alias` needed — `GAMMA_API_KEY` is the default Pydantic env-var name).

- [ ] **Step 3: Append tests to `tests/unit/engine/test_workflow_models.py`**

```python
def test_gamma_ai_node_data_parses_camelcase_aliases() -> None:
    from src.engine.workflow import GammaAiNodeData

    data = GammaAiNodeData.model_validate({
        "label": "GA",
        "prompt": "Make a deck about {{topic}}",
        "format": "presentation",
        "textMode": "generate",
        "numCards": 8,
        "textAmount": "medium",
        "imageSource": "unsplash",
        "language": "en",
        "exportAs": "pptx",
    })
    assert data.prompt == "Make a deck about {{topic}}"
    assert data.format == "presentation"
    assert data.text_mode == "generate"
    assert data.num_cards == 8
    assert data.text_amount == "medium"
    assert data.image_source == "unsplash"
    assert data.language == "en"
    assert data.export_as == "pptx"


def test_gamma_ai_node_data_defaults() -> None:
    from src.engine.workflow import GammaAiNodeData

    data = GammaAiNodeData.model_validate({"label": "GA"})
    assert data.prompt is None
    assert data.format == "presentation"
    assert data.text_mode == "generate"
    assert data.num_cards is None
    assert data.text_amount is None
    assert data.image_source is None
    assert data.language is None
    assert data.export_as == "web"


def test_gamma_ai_node_data_invalid_format_raises() -> None:
    import pytest
    from pydantic import ValidationError

    from src.engine.workflow import GammaAiNodeData

    with pytest.raises(ValidationError):
        GammaAiNodeData.model_validate({"label": "GA", "format": "bogus"})


def test_gamma_ai_node_full_round_trip() -> None:
    from src.engine.workflow import GammaAiNode

    node = GammaAiNode.model_validate({
        "id": "ga1",
        "type": "gamma-ai",
        "position": {"x": 0, "y": 0},
        "data": {"label": "GA", "prompt": "Hello"},
    })
    assert node.id == "ga1"
    assert node.type == "gamma-ai"
    assert node.data.prompt == "Hello"
```

If the existing `test_every_node_type_parses_minimal_instance` parametrized test includes `"gamma-ai"`, it should keep passing since all fields have defaults.

- [ ] **Step 4: Run + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/engine/test_workflow_models.py -v -k "gamma"
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

Expected: 4 new tests pass; overall ~437 (from 433 at Phase 6b exit).

```bash
git add src/engine/workflow.py src/config.py tests/unit/engine/test_workflow_models.py
git commit -m "feat(workflow): tighten GammaAiNodeData + add gamma_api_key setting (Phase 6c)

Replaces the Phase 1 placeholder {config: dict} with explicit fields
matching OAB types.ts:77-85:
  - prompt / format / text_mode / num_cards / text_amount /
    image_source / language / export_as
  - All optional; enum literals pinned (presentation|document|social,
    generate|condense|preserve, brief|medium|detailed, pptx|pdf|web).

settings.gamma_api_key added; read from env var GAMMA_API_KEY.

See Phase 6c spec §4 + §5.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `GammaAiExecutor` implementation

**Files:**
- Create: `src/executors/gamma_ai.py`
- Modify: `src/engine/graph_builder.py` — side-effect import
- Create: `tests/unit/executors/test_gamma_ai.py`

**Pre-task authorization:** If `test_registry.py` / `test_graph_builder.py` / `test_langgraph_executor.py` have sentinel tests that use `gamma-ai` as their "unshipped Phase 6" example, migrate them to `arcade` or `vector-db` in the same commit. Precedent: Phase 5a (`user-approval` → `guardrails`), Phase 6b (`guardrails` → `vector-db`).

- [ ] **Step 1: Write failing tests `tests/unit/executors/test_gamma_ai.py`**

```python
"""Tests for the gamma-ai executor (mocked Gamma API via pytest-httpx)."""

from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from src.engine.state import initial_state
from src.engine.workflow import GammaAiNode
from src.executors.gamma_ai import (
    GAMMA_API_BASE,
    GammaAiExecutor,
    GammaNodeError,
)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make asyncio.sleep a no-op for all tests in this module."""
    import asyncio

    async def _instant(*_a: Any, **_kw: Any) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _instant)


@pytest.fixture(autouse=True)
def _stub_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide a fake API key so tests don't hit 'GAMMA_API_KEY not configured'."""
    from src.config import get_settings

    monkeypatch.setenv("GAMMA_API_KEY", "test-key-123")
    get_settings.cache_clear()


def _node(**overrides: Any) -> GammaAiNode:
    data: dict[str, Any] = {"label": "GA", "prompt": "Make a deck"}
    data.update(overrides)
    return GammaAiNode.model_validate({
        "id": "ga",
        "type": "gamma-ai",
        "position": {"x": 0, "y": 0},
        "data": data,
    })


async def test_successful_create_and_immediate_complete(httpx_mock: HTTPXMock) -> None:
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


async def test_generation_id_from_alternative_keys(httpx_mock: HTTPXMock) -> None:
    """Parse generationId from various response shapes."""
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


async def test_polling_sees_pending_then_completed(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST", url=f"{GAMMA_API_BASE}/generations",
        json={"id": "gen_x"}, status_code=200,
    )
    httpx_mock.add_response(
        method="GET", url=f"{GAMMA_API_BASE}/generations/gen_x",
        json={"state": "pending"}, status_code=200,
    )
    httpx_mock.add_response(
        method="GET", url=f"{GAMMA_API_BASE}/generations/gen_x",
        json={"state": "pending"}, status_code=200,
    )
    httpx_mock.add_response(
        method="GET", url=f"{GAMMA_API_BASE}/generations/gen_x",
        json={"state": "completed", "gammaUrl": "https://ok"},
        status_code=200,
    )
    delta = await GammaAiExecutor(_node()).arun(initial_state())
    assert delta["variables"]["lastOutput"] == "https://ok"


async def test_polling_sees_failed_raises(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST", url=f"{GAMMA_API_BASE}/generations",
        json={"id": "gen_f"}, status_code=200,
    )
    httpx_mock.add_response(
        method="GET", url=f"{GAMMA_API_BASE}/generations/gen_f",
        json={"state": "failed", "error": "ran out of quota"},
        status_code=200,
    )
    with pytest.raises(GammaNodeError, match="quota"):
        await GammaAiExecutor(_node()).arun(initial_state())


async def test_export_wait_succeeds_with_download_url(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST", url=f"{GAMMA_API_BASE}/generations",
        json={"id": "gen_e"}, status_code=200,
    )
    # Completion response has no downloadUrl yet
    httpx_mock.add_response(
        method="GET", url=f"{GAMMA_API_BASE}/generations/gen_e",
        json={"state": "completed", "gammaUrl": "https://web"},
        status_code=200,
    )
    # Export-wait first poll: no downloadUrl
    httpx_mock.add_response(
        method="GET", url=f"{GAMMA_API_BASE}/generations/gen_e",
        json={"state": "completed", "gammaUrl": "https://web"},
        status_code=200,
    )
    # Export-wait second poll: downloadUrl ready
    httpx_mock.add_response(
        method="GET", url=f"{GAMMA_API_BASE}/generations/gen_e",
        json={
            "state": "completed",
            "gammaUrl": "https://web",
            "downloadUrl": "https://cdn.gamma.app/export.pptx",
        },
        status_code=200,
    )
    delta = await GammaAiExecutor(_node(exportAs="pptx")).arun(initial_state())
    assert delta["variables"]["lastOutput"] == "https://cdn.gamma.app/export.pptx"


async def test_export_wait_timeout_returns_web_url(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST", url=f"{GAMMA_API_BASE}/generations",
        json={"id": "gen_t"}, status_code=200,
    )
    # Completion
    httpx_mock.add_response(
        method="GET", url=f"{GAMMA_API_BASE}/generations/gen_t",
        json={"state": "completed", "gammaUrl": "https://fallback"},
        status_code=200,
    )
    # Then many polls with no downloadUrl
    for _ in range(20):
        httpx_mock.add_response(
            method="GET", url=f"{GAMMA_API_BASE}/generations/gen_t",
            json={"state": "completed", "gammaUrl": "https://fallback"},
            status_code=200,
        )
    delta = await GammaAiExecutor(_node(exportAs="pptx")).arun(initial_state())
    assert delta["variables"]["lastOutput"] == "https://fallback"


async def test_polling_timeout_returns_last_known(httpx_mock: HTTPXMock) -> None:
    """After 4 min of polling, return last known status without raising."""
    httpx_mock.add_response(
        method="POST", url=f"{GAMMA_API_BASE}/generations",
        json={"id": "gen_slow"}, status_code=200,
    )
    # Many pending polls — with _no_sleep fixture, this iterates many times
    # until the loop's deadline check kicks in. Since the deadline uses
    # asyncio.get_running_loop().time() (not the monkeypatched sleep), the
    # loop WILL exit once 4 minutes of wall-clock elapse... but this test
    # would take real wall-clock time. Instead, we assert that at least
    # one "processing" response is returned via the final-GET fallback.
    for _ in range(50):
        httpx_mock.add_response(
            method="GET", url=f"{GAMMA_API_BASE}/generations/gen_slow",
            json={"state": "pending"}, status_code=200,
        )
    # This test likely can't be exercised with the standard deadline because
    # the wall clock advances normally. Instead, we reduce the MAX_POLL_SECONDS
    # via monkeypatch:
    # (Implemented in the _short_timeouts helper below.)
    pass  # Replace the body with the helper-based version below if needed.


# Alternative timeout test using module-level constant override
async def test_polling_timeout_with_short_deadline(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,
) -> None:
    """Shrink MAX_POLL_SECONDS so the polling loop exits fast."""
    import src.executors.gamma_ai as gm

    monkeypatch.setattr(gm, "MAX_POLL_SECONDS", 0.001)

    httpx_mock.add_response(
        method="POST", url=f"{GAMMA_API_BASE}/generations",
        json={"id": "gen_s"}, status_code=200,
    )
    # Final GET after timeout — return last known status
    httpx_mock.add_response(
        method="GET", url=f"{GAMMA_API_BASE}/generations/gen_s",
        json={"state": "processing", "message": "still working"},
        status_code=200,
    )
    delta = await GammaAiExecutor(_node()).arun(initial_state())
    # No exception; lastOutput is empty string because no URL was present
    assert delta["variables"]["lastOutput"] == ""
    assert delta["node_results"]["ga"]["output"]["status"] == "processing"


async def test_transient_502_retries(httpx_mock: HTTPXMock) -> None:
    """Transient 502 during polling doesn't fail the workflow; next poll succeeds."""
    httpx_mock.add_response(
        method="POST", url=f"{GAMMA_API_BASE}/generations",
        json={"id": "gen_r"}, status_code=200,
    )
    httpx_mock.add_response(
        method="GET", url=f"{GAMMA_API_BASE}/generations/gen_r",
        status_code=502, text="bad gateway",
    )
    httpx_mock.add_response(
        method="GET", url=f"{GAMMA_API_BASE}/generations/gen_r",
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


async def test_variable_substitution_in_prompt(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST", url=f"{GAMMA_API_BASE}/generations",
        json={"id": "gen_v"}, status_code=200,
        match_json=None,  # We'll inspect the request separately
    )
    httpx_mock.add_response(
        method="GET", url=f"{GAMMA_API_BASE}/generations/gen_v",
        json={"state": "completed", "gammaUrl": "https://x"},
        status_code=200,
    )
    state = initial_state()
    state["variables"]["topic"] = "dogs"
    await GammaAiExecutor(_node(prompt="Deck about {{topic}}")).arun(state)

    # Verify the POST body had the substituted prompt
    post_request = httpx_mock.get_requests(
        method="POST",
        url=f"{GAMMA_API_BASE}/generations",
    )[0]
    import json

    body = json.loads(post_request.content)
    assert body["inputText"] == "Deck about dogs"


async def test_request_body_optional_fields(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST", url=f"{GAMMA_API_BASE}/generations",
        json={"id": "gen_o"}, status_code=200,
    )
    httpx_mock.add_response(
        method="GET", url=f"{GAMMA_API_BASE}/generations/gen_o",
        json={"state": "completed", "gammaUrl": "https://x"},
        status_code=200,
    )
    node = _node(
        numCards=10,
        textAmount="detailed",
        imageSource="unsplash",
        language="es",
        exportAs="pdf",
    )
    await GammaAiExecutor(node).arun(initial_state())

    post_request = httpx_mock.get_requests(
        method="POST", url=f"{GAMMA_API_BASE}/generations",
    )[0]
    import json

    body = json.loads(post_request.content)
    assert body["numCards"] == 10
    assert body["textOptions"] == {"amount": "detailed", "language": "es"}
    assert body["imageOptions"] == {"source": "unsplash"}
    assert body["exportAs"] == "pdf"


async def test_request_body_web_export_omits_export_as(httpx_mock: HTTPXMock) -> None:
    """When export_as='web' (the default), the field is NOT included in the request."""
    httpx_mock.add_response(
        method="POST", url=f"{GAMMA_API_BASE}/generations",
        json={"id": "gen_w"}, status_code=200,
    )
    httpx_mock.add_response(
        method="GET", url=f"{GAMMA_API_BASE}/generations/gen_w",
        json={"state": "completed", "gammaUrl": "https://x"},
        status_code=200,
    )
    await GammaAiExecutor(_node()).arun(initial_state())

    post_request = httpx_mock.get_requests(
        method="POST", url=f"{GAMMA_API_BASE}/generations",
    )[0]
    import json

    body = json.loads(post_request.content)
    assert "exportAs" not in body


async def test_executor_is_registered() -> None:
    import src.executors.gamma_ai  # noqa: F401  # pyright: ignore[reportUnusedImport]

    from src.executors.base import build_executor

    node = _node()
    executor = build_executor(node)
    assert isinstance(executor, GammaAiExecutor)
```

Note: Delete the placeholder `test_polling_timeout_returns_last_known` body — it's superseded by `test_polling_timeout_with_short_deadline`. I left the stub in the plan to show the thinking, but in the actual test file delete it entirely and keep only the short-deadline version.

- [ ] **Step 2: Verify fail**

```bash
.venv/Scripts/python -m pytest tests/unit/executors/test_gamma_ai.py -v
```

Expected: ImportError on `src.executors.gamma_ai`.

- [ ] **Step 3: Implement `src/executors/gamma_ai.py`**

Copy the exact code block from Phase 6c spec §6. Verify module-level constants (`GAMMA_API_BASE`, `INITIAL_WAIT_SECONDS`, `POLL_INTERVAL_SECONDS`, `MAX_POLL_SECONDS`, `EXPORT_WAIT_SECONDS`, `EXPORT_POLL_INTERVAL`) are there so tests can monkeypatch them.

Module docstring + full executor body as specified.

- [ ] **Step 4: Register side-effect import in `graph_builder.py`**

Add alphabetically (between `extract`/`guardrails` and `http`):

```python
from src.executors import (
    gamma_ai as _gamma_ai_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
```

- [ ] **Step 5: Check + migrate sentinel tests if needed**

```bash
grep -rn '"gamma-ai"\|"gamma_ai"' tests/unit/
```

If any test in `tests/unit/executors/test_registry.py`, `tests/unit/engine/test_graph_builder.py`, or `tests/unit/engine/test_langgraph_executor.py` uses `"gamma-ai"` as the "unshipped Phase 6" sentinel, change it to `"arcade"` (which IS still unshipped until Phase 6d). Precedent: Phase 5a did `user-approval` → `guardrails`; Phase 6b did `guardrails` → `vector-db`; Phase 6c does (if needed) `gamma-ai` → `arcade`.

Note: Phase 6b already migrated those sentinels to `vector-db`. They probably won't need to change again unless someone added a new sentinel using `gamma-ai` in between. Quick grep will confirm.

- [ ] **Step 6: Run + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/executors/test_gamma_ai.py -v
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

Expected: ~12 new tests pass; overall +12 (~449 from 437 after Task 1). Pyright 0 errors.

```bash
git add src/executors/gamma_ai.py src/engine/graph_builder.py tests/unit/executors/test_gamma_ai.py
git commit -m "feat(executors): gamma-ai — Gamma.app HTTP integration (Phase 6c)

Two-step protocol: POST /generations to create, then poll
GET /generations/{id} until state=completed or failed.  Polling
cadence matches OAB's lib/workflow/executors/gamma.ts: 60s initial
wait, 10s interval, 4-min max.  When exportAs in {pptx, pdf} and
completion response lacks downloadUrl, wait up to 60s more for the
export URL.

lastOutput = downloadUrl || gammaUrl.  Timeout behavior matches OAB:
workflow continues, lastOutput is last-known URL (may be empty).

Transient 4xx/5xx during polling retried; POST failure or state=failed
raises GammaNodeError.

Module-scope sleep constants (INITIAL_WAIT_SECONDS etc.) so tests can
monkeypatch.

See Phase 6c spec §6.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Sanity check

No code changes; just verify no regressions.

```bash
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m ruff format --check src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

All green. No commit.

---

## Task 4: Optional manual integration smoke test

**This task is gated by `GAMMA_API_KEY` presence.** If the user's `.env` has it, the controller runs a minimal smoke test — NOT a committed pytest file, just an ad-hoc Python script that POSTs a tiny prompt and polls once. Used to verify the live wire.

If `GAMMA_API_KEY` is not set, skip the smoke test. Note it in CHANGELOG.

```bash
.venv/Scripts/python -c "
import os
from dotenv import load_dotenv
load_dotenv('.env', override=True)
if not os.environ.get('GAMMA_API_KEY'):
    print('SKIP: GAMMA_API_KEY not set')
else:
    print('TODO: manual smoke test here')
"
```

No commit.

---

## Task 5: Reserved — fix-up slot

If unit tests uncover an issue not caught by the spec (e.g., Gamma API schema drift), fix in a dedicated `fix(phase-6c): ...` commit before Task 6.

If no issues, skip.

---

## Task 6: Phase-exit — CHANGELOG + CLAUDE.md + push

**Authorized:** `CHANGELOG.md`, `CLAUDE.md`.

- [ ] **Step 1: Run full exit checklist**

```bash
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m ruff format --check src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

- [ ] **Step 2: Update `CHANGELOG.md`** — insert above Phase 6b:

```markdown
### Phase 6c — Gamma-AI (2026-04-21)

#### Added
- [Phase 6c design spec](docs/superpowers/specs/2026-04-21-phase-6c-gamma-ai-design.md).
- `src/executors/gamma_ai.py` — `GammaAiExecutor` calls the Gamma.app public API (`https://public-api.gamma.app/v1.0`) to generate a presentation/document/webpage. Two-step protocol: POST create → poll GET until `state=completed` or `failed`. OAB-compatible cadence: 60s initial wait, 10s interval, 4-min max. When `exportAs` in {pptx, pdf}, waits up to 60s more for the download URL. `lastOutput = downloadUrl || gammaUrl`.
- `src/engine/workflow.py` — `GammaAiNodeData` tightened with 8 explicit fields matching OAB `types.ts:77-85` (`prompt`, `format`, `textMode`, `numCards`, `textAmount`, `imageSource`, `language`, `exportAs`). Enum literals pinned.
- `src/config.py` — `gamma_api_key` setting (env var `GAMMA_API_KEY`).
- Unit tests: ~12, all mocked via `pytest-httpx`. Sleep monkeypatched so tests run fast.

#### Notes
- Max runtime ~6 minutes. Runs inline in the BackgroundTask (machine-bounded wait; no LangGraph interrupt needed).
- Transient 4xx/5xx during polling is retried; POST create failure or `state=failed` raises `GammaNodeError`.
- Integration smoke test not committed — Gamma requires an active account + key. Users with `GAMMA_API_KEY` can exercise manually.

#### Verified
- ~449/449 unit tests green (+16 from Phase 6b 433: 4 Pydantic tests + 12 executor tests).
- Pyright 0 errors, ruff + format clean.
```

- [ ] **Step 3: Update `CLAUDE.md` phase table**

Change:
```markdown
| 6c — Gamma-AI + Arcade | ⏭ Next | HTTP integrations |
| 6d — Vector-DB | ⏸ | provider framework (embed/upsert/query) |
```
To:
```markdown
| 6c — Gamma-AI | ✅ Complete | HTTP integration with gamma.app; 60s/10s/4min polling; exportAs pptx/pdf supported; ~12 unit tests, integration manual |
| 6d — Arcade | ⏭ Next | HTTP integration with auth-interrupt flow (reuses Phase 5a `/resume` pattern) |
| 6e — Vector-DB | ⏸ | provider framework (embed/upsert/query) |
```

- [ ] **Step 4: Commit + push**

```bash
git add CHANGELOG.md CLAUDE.md
git commit -m "docs(phase-6c): mark Phase 6c complete

Gamma-AI shipped.  HTTP integration with gamma.app's public API;
two-step create→poll protocol; optional PPTX/PDF export URL wait.
Runs inline in BackgroundTask; max runtime ~6 minutes.

~12 unit tests via pytest-httpx; sleep monkeypatched.  Integration
smoke test manual (requires live GAMMA_API_KEY).

Phase 6d (Arcade — auth-interrupt flow) is next.  Phase 6 is now 5
sub-phases total (6a/6b/6c/6d/6e) after splitting the original 6c.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"

git push origin main
```

---

## Spec coverage self-review

| Spec section | Tasks |
|---|---|
| §4 Pydantic tightening | Task 1 |
| §5 settings | Task 1 |
| §6 executor | Task 2 |
| §7 error model | Task 2 (tests 4, 9) |
| §8.1 unit tests | Task 2 (12 tests) |
| §8.2 integration (manual) | Task 4 |
| §9 phase-exit | Task 6 |

No placeholders. Type consistency:
- Field names `prompt` / `format` / `text_mode` / `num_cards` / `text_amount` / `image_source` / `language` / `export_as` consistent across §4 Pydantic, §6 executor body, §8 tests.
- `GammaNodeError` usage pinned: raises on missing key, POST failure, and state='failed'; does NOT raise on polling timeout or export timeout.
- Module-scope constants (`INITIAL_WAIT_SECONDS`, `POLL_INTERVAL_SECONDS`, `MAX_POLL_SECONDS`, etc.) consistent between executor and tests.

---

## Execution handoff

Plan saved. Controller proceeds to `superpowers:subagent-driven-development` for Tasks 1–6.
