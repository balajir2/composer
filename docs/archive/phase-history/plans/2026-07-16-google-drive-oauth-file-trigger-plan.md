# Google Drive OAuth File-Trigger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a `file-trigger` node point at a Google Drive folder, polled by Composer's own hosted backend on a schedule — no local CLI, no local process, works for a browser-only SaaS.

**Architecture:** A new `CloudStorageConnection` Prisma model stores per-user, per-provider OAuth tokens (mirrors `McpOAuthToken`, reuses the same AES-256-GCM encryption primitives). A new `src/integrations/google_drive/oauth.py` module handles the OAuth dance via direct `httpx` calls (no SDK). A new `GoogleDriveProvider` implements the existing `FileStorageProvider` ABC against the Drive v3 REST API, using a Drive `appProperties` marker as its claim mechanism instead of local's move-to-folder. A new `POST /internal/poll-file-triggers` endpoint — a fifth sibling to ADR-0033's `claim-and-run`/`sweep`, same OIDC auth — is the Cloud-Scheduler-triggered poll loop that replaces `composer watch` for this provider. The frontend gains a "Connect Google Drive" flow (OAuth popup + Google Picker for folder selection) in the `file-trigger` node's property panel.

**Tech Stack:** Python (no new dependencies — `httpx`, `cryptography`, `google-auth` already present), FastAPI/Pydantic node-schema conventions, Prisma Python, Next.js/React frontend, Google Drive v3 REST API + Google Picker JS API (loaded at runtime, no npm package).

**Design doc:** `docs/archive/phase-history/specs/2026-07-15-google-drive-oauth-file-trigger-design.md` — read this first if anything below is ambiguous; this plan implements it task-by-task and does not repeat its rationale.

---

### Task 1: `CloudStorageConnection` Prisma model + migration

**Files:**
- Modify: `prisma/schema.prisma:184` (insert after `McpOAuthState`, before `enum UserRole`)

- [ ] **Step 1: Add the model**

Insert after line 184 (`}` closing `McpOAuthState`) and before line 186 (`enum UserRole`):

```prisma
model CloudStorageConnection {
  id                    String    @id @default(cuid())
  userId                String    @map("user_id")
  provider              String    // "google-drive" (widens later: "dropbox", "onedrive")
  accountEmail          String    @map("account_email")
  encryptedAccessToken  String    @map("encrypted_access_token")
  encryptedRefreshToken String?   @map("encrypted_refresh_token")
  expiresAt             DateTime? @map("expires_at")
  scope                 String?
  createdAt             DateTime  @default(now()) @map("created_at")
  updatedAt             DateTime  @updatedAt       @map("updated_at")

  @@unique([userId, provider, accountEmail])
  @@map("cloud_storage_connections")
  @@index([userId])
}
```

- [ ] **Step 2: Create the migration and regenerate the client**

Run: `uv run prisma migrate dev --name add_cloud_storage_connection`
Expected: migration file created under `prisma/migrations/`, applies cleanly against the dev database, prints "Your database is now in sync with your schema."

Run: `uv run prisma generate`
Expected: "Generated Prisma Client Python" with no errors.

- [ ] **Step 3: Verify the model is queryable**

Run:
```bash
uv run python -c "
import asyncio
from prisma import Prisma

async def main():
    db = Prisma()
    await db.connect()
    count = await db.cloudstorageconnection.count()
    print('count:', count)
    await db.disconnect()

asyncio.run(main())
"
```
Expected: `count: 0` (empty table, no errors).

- [ ] **Step 4: Commit**

```bash
git add prisma/schema.prisma prisma/migrations/
git commit -m "feat(db): add CloudStorageConnection model for OAuth-connected cloud storage"
```

---

### Task 2: Config settings for Google OAuth + Picker

**Files:**
- Modify: `src/config.py` (insert after the `resend_from_email` field, ~line 92)
- Modify: `.env.example` (insert a new section after the Cloud Tasks block, ~line 102)
- Test: `tests/unit/test_config_validation.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_config_validation.py`:

```python
def test_google_oauth_settings_default_to_empty_string(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.config import Settings

    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("GOOGLE_PICKER_API_KEY", raising=False)
    settings = Settings(_env_file=None)
    assert settings.google_oauth_client_id == ""
    assert settings.google_oauth_client_secret == ""
    assert settings.google_picker_api_key == ""
```

(Check the top of `tests/unit/test_config_validation.py` for its existing `import pytest` and any `Settings(_env_file=None)` precedent — other tests in that file already isolate from a real `.env`; match whatever pattern they use if it differs from the above.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_config_validation.py::test_google_oauth_settings_default_to_empty_string -v`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'google_oauth_client_id'`

- [ ] **Step 3: Add the settings fields**

In `src/config.py`, insert after `resend_from_email` (the field right after `resend_api_key`, ~line 92):

```python
    google_oauth_client_id: str = Field(
        default="", description="Google Cloud OAuth 2.0 Client ID (Web application type)."
    )
    google_oauth_client_secret: str = Field(
        default="", description="Google Cloud OAuth 2.0 Client Secret, paired with google_oauth_client_id."
    )
    google_picker_api_key: str = Field(
        default="",
        description="Google Cloud API key restricted to the Picker API, used by the frontend "
        "to embed the Drive folder picker (separate from the OAuth client credentials).",
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_config_validation.py::test_google_oauth_settings_default_to_empty_string -v`
Expected: PASS

- [ ] **Step 5: Document in `.env.example`**

Insert after the Cloud Tasks block (after `EXECUTION_MAX_DELIVERY_ATTEMPTS=5`, ~line 102):

```
# ─── Google Drive OAuth (file-trigger cloud storage, 2026-07-15) ──
# From Google Cloud Console: OAuth consent screen (Testing mode) + an OAuth
# 2.0 Client ID (Web application), redirect URI =
# {BACKEND_PUBLIC_URL}/cloud-storage/google-drive/callback. See
# docs/archive/phase-history/specs/2026-07-15-google-drive-oauth-file-trigger-design.md §F.
GOOGLE_OAUTH_CLIENT_ID=
GOOGLE_OAUTH_CLIENT_SECRET=
# API key restricted to the Picker API — separate credential, used by the
# frontend to embed the Drive folder picker.
GOOGLE_PICKER_API_KEY=
```

- [ ] **Step 6: Commit**

```bash
git add src/config.py .env.example tests/unit/test_config_validation.py
git commit -m "feat(config): add Google OAuth client + Picker API key settings"
```

---

### Task 3: Extract shared text-extraction helper (DRY prep)

`composer watch`'s `_extract_text()` (`src/cli/watch.py`) needs to be reused by the new Drive poller. Extract it into a shared module first, as a pure refactor with no behavior change, so both call sites share one implementation.

**Files:**
- Create: `src/storage_providers/text_extraction.py`
- Modify: `src/cli/watch.py:1-67` (remove `_extract_text`/`UnsupportedFileTypeError`, import from the new module)
- Test: `tests/unit/storage_providers/test_text_extraction.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/storage_providers/test_text_extraction.py
"""Tests for the shared extract_text() helper (used by composer watch and
the Google Drive poller)."""

import pytest


def test_extracts_plain_text() -> None:
    from src.storage_providers.text_extraction import extract_text

    assert extract_text("notes.txt", b"hello world") == "hello world"


def test_extracts_markdown() -> None:
    from src.storage_providers.text_extraction import extract_text

    assert extract_text("notes.md", b"# Heading") == "# Heading"


def test_raises_on_unsupported_extension() -> None:
    from src.storage_providers.text_extraction import UnsupportedFileTypeError, extract_text

    with pytest.raises(UnsupportedFileTypeError, match="image.png"):
        extract_text("image.png", b"\x89PNG\r\n")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/storage_providers/test_text_extraction.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.storage_providers.text_extraction'`

- [ ] **Step 3: Create the shared module**

```python
# src/storage_providers/text_extraction.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/storage_providers/test_text_extraction.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Update `src/cli/watch.py` to use the shared module**

In `src/cli/watch.py`, replace the existing `UnsupportedFileTypeError` class and `_extract_text()` function (currently lines ~46-67) with an import, and update the one call site (`claim_file`, currently calling `_extract_text(ref.name, raw)`):

```python
from src.storage_providers.text_extraction import UnsupportedFileTypeError, extract_text
```

Remove the local `class UnsupportedFileTypeError(ValueError): ...` and `def _extract_text(...): ...` definitions. In `claim_file`, change:
```python
        text = _extract_text(ref.name, raw)
```
to:
```python
        text = extract_text(ref.name, raw)
```

- [ ] **Step 6: Run the full existing watch test suite to confirm no regression**

Run: `uv run pytest tests/unit/cli/test_watch.py -v`
Expected: PASS (all existing tests still pass — `test_claim_moves_to_error_path_on_unsupported_file_type` in particular exercises the extraction failure path end-to-end through `claim_file`)

- [ ] **Step 7: Commit**

```bash
git add src/storage_providers/text_extraction.py src/cli/watch.py tests/unit/storage_providers/test_text_extraction.py
git commit -m "refactor: extract shared text-extraction helper for reuse by the Drive poller"
```

---

### Task 4: Widen `FileTriggerNodeData` for the `google-drive` provider

**Files:**
- Modify: `src/engine/workflow.py:105-111`
- Test: `tests/unit/engine/test_workflow_models.py:977-1017`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/engine/test_workflow_models.py`, near the existing `test_file_trigger_node_parses_full_config`/`test_file_trigger_node_defaults`:

```python
def test_file_trigger_node_parses_google_drive_config() -> None:
    from src.engine.workflow import FileTriggerNode

    node = FileTriggerNode.model_validate(
        {
            "id": "ft1",
            "type": "file-trigger",
            "position": {"x": 0, "y": 0},
            "data": {
                "label": "File Trigger",
                "provider": "google-drive",
                "connectionId": "conn_abc123",
                "driveFolderId": "1a2b3c4d5e",
                "targetInputVariable": "file_content",
            },
        }
    )
    assert node.data.provider == "google-drive"
    assert node.data.connection_id == "conn_abc123"
    assert node.data.drive_folder_id == "1a2b3c4d5e"


def test_file_trigger_node_rejects_unknown_provider() -> None:
    from pydantic import ValidationError

    from src.engine.workflow import FileTriggerNode

    with pytest.raises(ValidationError):
        FileTriggerNode.model_validate(
            {
                "id": "ft1",
                "type": "file-trigger",
                "position": {"x": 0, "y": 0},
                "data": {"label": "File Trigger", "provider": "dropbox"},
            }
        )
```

(Confirm `import pytest` is already present at the top of `tests/unit/engine/test_workflow_models.py` — it is, used by other tests in that file.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/engine/test_workflow_models.py::test_file_trigger_node_parses_google_drive_config tests/unit/engine/test_workflow_models.py::test_file_trigger_node_rejects_unknown_provider -v`
Expected: first test FAILs with a Pydantic validation error (`connectionId`/`driveFolderId` not accepted / `provider` literal rejects `"google-drive"`); second test currently PASSes already by coincidence only if `"dropbox"` is already rejected — verify it currently fails for the wrong reason or passes trivially; either way, both must genuinely fail against the *current* `Literal["local"]` (both non-`"local"` providers are already rejected) — so step forward and confirm the FIRST test is the one that fails, confirming the gap this task closes.

- [ ] **Step 3: Widen the model**

In `src/engine/workflow.py`, replace lines 105-111:

```python
class FileTriggerNodeData(BaseNodeData):
    provider: Literal["local"] = "local"
    source_path: str | None = Field(default=None, alias="sourcePath")
    dest_path: str | None = Field(default=None, alias="destPath")
    error_path: str | None = Field(default=None, alias="errorPath")
    target_input_variable: str | None = Field(default=None, alias="targetInputVariable")
    poll_interval_seconds: int = Field(default=30, alias="pollIntervalSeconds")
```

with:

```python
class FileTriggerNodeData(BaseNodeData):
    provider: Literal["local", "google-drive"] = "local"
    source_path: str | None = Field(default=None, alias="sourcePath")  # local only
    dest_path: str | None = Field(default=None, alias="destPath")  # local only
    error_path: str | None = Field(default=None, alias="errorPath")  # local only
    target_input_variable: str | None = Field(default=None, alias="targetInputVariable")
    poll_interval_seconds: int = Field(default=30, alias="pollIntervalSeconds")  # local (CLI) only
    connection_id: str | None = Field(default=None, alias="connectionId")  # google-drive only
    drive_folder_id: str | None = Field(default=None, alias="driveFolderId")  # google-drive only
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/engine/test_workflow_models.py -k file_trigger -v`
Expected: PASS (all 4 file-trigger tests, including the two pre-existing ones)

- [ ] **Step 5: Commit**

```bash
git add src/engine/workflow.py tests/unit/engine/test_workflow_models.py
git commit -m "feat(engine): widen file-trigger node to support google-drive provider"
```

---

### Task 5: Google Drive OAuth primitives

**Files:**
- Create: `src/integrations/google_drive/__init__.py`
- Create: `src/integrations/google_drive/oauth.py`
- Test: `tests/unit/integrations/test_google_drive_oauth.py`

- [ ] **Step 1: Write the failing tests**

```python
# src/integrations/google_drive/__init__.py
```

```python
# tests/unit/integrations/test_google_drive_oauth.py
"""Tests for Google Drive OAuth primitives (standard 3-legged OAuth2, no
RFC 8707 resource param — that was MCP/Highspot-specific)."""

import base64
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]


def _set_enc_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    from src.config import get_settings

    get_settings.cache_clear()


def _set_oauth_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "client-abc")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "secret-xyz")
    from src.config import get_settings

    get_settings.cache_clear()


def test_build_authorize_url_includes_expected_params(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_enc_key(monkeypatch)
    _set_oauth_settings(monkeypatch)
    from src.integrations.google_drive.oauth import build_authorize_url

    url = build_authorize_url("user1", "https://api.example.com/cloud-storage/google-drive/callback")
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "client_id=client-abc" in url
    assert "scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fdrive.file" in url
    assert "access_type=offline" in url
    assert "state=" in url


def test_consume_state_round_trips_user_id(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_enc_key(monkeypatch)
    from src.integrations.google_drive.oauth import build_state, consume_state

    state = build_state("user1")
    assert consume_state(state) == "user1"


def test_consume_state_rejects_expired_state(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_enc_key(monkeypatch)
    import json

    from src.integrations.google_drive.oauth import InvalidStateError, consume_state
    from src.security.encryption import encrypt

    expired_payload = json.dumps(
        {"user_id": "user1", "exp": (datetime.now(UTC) - timedelta(minutes=1)).isoformat()}
    )
    with pytest.raises(InvalidStateError, match="expired"):
        consume_state(encrypt(expired_payload))


async def test_exchange_code_for_tokens_fetches_email(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    _set_enc_key(monkeypatch)
    _set_oauth_settings(monkeypatch)
    from src.integrations.google_drive.oauth import exchange_code_for_tokens

    httpx_mock.add_response(
        url="https://oauth2.googleapis.com/token",
        method="POST",
        json={"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600, "scope": "drive.file"},
    )
    httpx_mock.add_response(
        url="https://www.googleapis.com/oauth2/v2/userinfo",
        method="GET",
        json={"email": "designer@example.com"},
    )

    result = await exchange_code_for_tokens("auth-code", "https://api.example.com/callback")
    assert result["access_token"] == "at-1"
    assert result["email"] == "designer@example.com"


async def test_get_valid_drive_access_token_refreshes_when_near_expiry(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    _set_enc_key(monkeypatch)
    _set_oauth_settings(monkeypatch)
    from src.integrations.google_drive.oauth import get_valid_drive_access_token
    from src.security.encryption import encrypt

    connection = SimpleNamespace(
        id="conn1",
        encryptedAccessToken=encrypt("old-at"),
        encryptedRefreshToken=encrypt("rt-1"),
        expiresAt=datetime.now(UTC) - timedelta(minutes=1),
    )
    db = MagicMock()
    db.cloudstorageconnection = MagicMock()
    db.cloudstorageconnection.find_unique = AsyncMock(return_value=connection)
    db.cloudstorageconnection.update = AsyncMock(
        return_value=SimpleNamespace(id="conn1", encryptedAccessToken=encrypt("new-at"))
    )
    httpx_mock.add_response(
        url="https://oauth2.googleapis.com/token",
        method="POST",
        json={"access_token": "new-at", "expires_in": 3600},
    )

    token = await get_valid_drive_access_token("conn1", db)
    assert token == "new-at"
    db.cloudstorageconnection.update.assert_awaited_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/integrations/test_google_drive_oauth.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.integrations.google_drive.oauth'`

- [ ] **Step 3: Implement the module**

```python
# src/integrations/google_drive/oauth.py
"""Google Drive OAuth primitives — standard three-legged OAuth2 (no RFC
8707 `resource` param; that was an MCP/Highspot-specific requirement, not
applicable to Google's OAuth server). Tokens live server-side only,
encrypted with src/security/encryption.py — same primitives src/mcp/oauth.py
uses for MCP OAuth tokens.

State is a self-contained encrypted payload (user_id + expiry), not a DB
row like McpOAuthState — AES-GCM already gives tamper-evidence and expiry
without a separate table/cleanup sweep.

See docs/archive/phase-history/specs/2026-07-15-google-drive-oauth-file-trigger-design.md §B.
"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

from src.config import get_settings
from src.security.encryption import EncryptionError, decrypt, encrypt

GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"

_STATE_TTL = timedelta(minutes=10)
_EXPIRY_BUFFER_SECONDS = 60


class GoogleDriveOAuthError(RuntimeError):
    """Base class for all Google Drive OAuth errors."""


class InvalidStateError(GoogleDriveOAuthError):
    """State param missing, expired, or tampered with."""


class TokenExchangeError(GoogleDriveOAuthError):
    """Google rejected the authorization code exchange."""


class TokenRefreshError(GoogleDriveOAuthError):
    """Google rejected the token refresh request."""


class DriveTokenExpiredError(GoogleDriveOAuthError):
    """Token expired with no refresh token available — user must reconnect."""


class DriveConnectionMissingError(GoogleDriveOAuthError):
    """No CloudStorageConnection row found for the given id."""


def build_state(user_id: str) -> str:
    payload = {"user_id": user_id, "exp": (datetime.now(UTC) + _STATE_TTL).isoformat()}
    return encrypt(json.dumps(payload))


def consume_state(state: str) -> str:
    """Decrypt state, verify not expired, return user_id."""
    try:
        payload = json.loads(decrypt(state))
    except EncryptionError as exc:
        raise InvalidStateError(f"OAuth state is invalid or tampered: {exc}") from exc
    expires_at = datetime.fromisoformat(payload["exp"])
    if datetime.now(UTC) >= expires_at:
        raise InvalidStateError("OAuth state has expired.")
    return payload["user_id"]  # type: ignore[no-any-return]


def build_authorize_url(user_id: str, redirect_uri: str) -> str:
    settings = get_settings()
    params = {
        "client_id": settings.google_oauth_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": DRIVE_FILE_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": build_state(user_id),
    }
    return f"{GOOGLE_AUTHORIZE_URL}?{urlencode(params)}"


def expires_at_from_in(expires_in: int | None) -> datetime | None:
    if expires_in is None:
        return None
    return datetime.now(UTC) + timedelta(seconds=int(expires_in))


async def exchange_code_for_tokens(code: str, redirect_uri: str) -> dict[str, Any]:
    settings = get_settings()
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": settings.google_oauth_client_id,
        "client_secret": settings.google_oauth_client_secret,
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
        resp = await client.post(GOOGLE_TOKEN_URL, data=form, headers={"Accept": "application/json"})
    if resp.status_code >= 400:
        raise TokenExchangeError(
            f"Google token exchange failed (HTTP {resp.status_code}): {resp.text[:200]}"
        )
    payload: dict[str, Any] = resp.json()

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
        userinfo_resp = await client.get(
            GOOGLE_USERINFO_URL, headers={"Authorization": f"Bearer {payload['access_token']}"}
        )
    if userinfo_resp.status_code >= 400:
        raise TokenExchangeError(
            f"Google userinfo fetch failed (HTTP {userinfo_resp.status_code}): "
            f"{userinfo_resp.text[:200]}"
        )
    payload["email"] = userinfo_resp.json().get("email", "")
    return payload


async def refresh_access_token(refresh_token_plaintext: str) -> dict[str, Any]:
    settings = get_settings()
    form = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token_plaintext,
        "client_id": settings.google_oauth_client_id,
        "client_secret": settings.google_oauth_client_secret,
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
        resp = await client.post(GOOGLE_TOKEN_URL, data=form, headers={"Accept": "application/json"})
    if resp.status_code >= 400:
        raise TokenRefreshError(f"Google token refresh failed (HTTP {resp.status_code}): {resp.text[:200]}")
    return resp.json()  # type: ignore[no-any-return]


async def get_valid_drive_access_token(
    connection_id: str, db: Any, *, expiry_buffer_seconds: int = _EXPIRY_BUFFER_SECONDS
) -> str:
    """Return a decrypted plaintext access token, refreshing if near-expiry."""
    connection = await db.cloudstorageconnection.find_unique(where={"id": connection_id})
    if connection is None:
        raise DriveConnectionMissingError(f"No CloudStorageConnection found for id {connection_id!r}.")

    if connection.expiresAt is not None:
        now = datetime.now(UTC)
        if now >= connection.expiresAt - timedelta(seconds=expiry_buffer_seconds):
            if not connection.encryptedRefreshToken:
                raise DriveTokenExpiredError(
                    f"Drive connection {connection_id!r} expired with no refresh token; "
                    f"user must reconnect."
                )
            refreshed = await refresh_access_token(decrypt(connection.encryptedRefreshToken))
            connection = await db.cloudstorageconnection.update(
                where={"id": connection_id},
                data={
                    "encryptedAccessToken": encrypt(refreshed["access_token"]),
                    "expiresAt": expires_at_from_in(refreshed.get("expires_in")),
                },
            )

    return decrypt(connection.encryptedAccessToken)  # type: ignore[no-any-return]


__all__ = [
    "DRIVE_FILE_SCOPE",
    "DriveConnectionMissingError",
    "DriveTokenExpiredError",
    "GoogleDriveOAuthError",
    "InvalidStateError",
    "TokenExchangeError",
    "TokenRefreshError",
    "build_authorize_url",
    "build_state",
    "consume_state",
    "exchange_code_for_tokens",
    "expires_at_from_in",
    "get_valid_drive_access_token",
    "refresh_access_token",
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/integrations/test_google_drive_oauth.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add src/integrations/google_drive/ tests/unit/integrations/test_google_drive_oauth.py
git commit -m "feat: add Google Drive OAuth primitives (authorize/exchange/refresh)"
```

---

### Task 6: `POST /cloud-storage/google-drive/authorize` + `/callback` + `GET /cloud-storage/connections`

**Files:**
- Create: `src/api/cloud_storage_oauth.py`
- Modify: `src/main.py` (register the new router)
- Test: `tests/unit/api/test_cloud_storage_oauth.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/api/test_cloud_storage_oauth.py
"""Tests for the Google Drive OAuth connect routes."""

import base64
import os
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.main import create_app


def _client_with_mock_db() -> tuple[TestClient, MagicMock]:
    app = create_app()
    db = MagicMock()
    db.cloudstorageconnection = MagicMock()
    app.state.db = db
    app.state.checkpointer = MagicMock()
    from src.engine.events_pg import PostgresEventStore

    app.state.event_bus = PostgresEventStore(db)
    return TestClient(app), db


def _auth_headers(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Bypass real JWT verification the same way other route tests do —
    check tests/unit/api/test_mcp_servers.py for this codebase's exact
    get_current_user_id override pattern and mirror it here if this
    monkeypatch approach doesn't match (dependency_overrides vs monkeypatch
    on the auth module) — this codebase primarily favors monkeypatching the
    verification function at its import site, matching test_internal_sweep.py."""
    monkeypatch.setenv("ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    from src.config import get_settings

    get_settings.cache_clear()
    return {}


async def test_authorize_returns_google_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _auth_headers(monkeypatch)
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "client-abc")
    from src.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(
        "src.security.auth.get_current_user_id", AsyncMock(return_value="user1")
    )
    client, _db = _client_with_mock_db()

    resp = client.get("/cloud-storage/google-drive/authorize")
    assert resp.status_code == 200, resp.text
    assert resp.json()["authorizeUrl"].startswith("https://accounts.google.com/o/oauth2/v2/auth?")


def test_callback_rejects_invalid_state(monkeypatch: pytest.MonkeyPatch) -> None:
    _auth_headers(monkeypatch)
    client, _db = _client_with_mock_db()

    resp = client.get(
        "/cloud-storage/google-drive/callback",
        params={"code": "auth-code", "state": "not-a-real-state"},
    )
    assert resp.status_code == 200  # popup-close HTML, not an HTTP error
    assert "error" in resp.text


def test_list_connections_filters_by_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    _auth_headers(monkeypatch)
    from types import SimpleNamespace

    monkeypatch.setattr(
        "src.security.auth.get_current_user_id", AsyncMock(return_value="user1")
    )
    client, db = _client_with_mock_db()
    db.cloudstorageconnection.find_many = AsyncMock(
        return_value=[
            SimpleNamespace(id="conn1", provider="google-drive", accountEmail="a@example.com")
        ]
    )

    resp = client.get("/cloud-storage/connections", params={"provider": "google-drive"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == [
        {"id": "conn1", "provider": "google-drive", "accountEmail": "a@example.com"}
    ]


async def test_picker_token_returns_connections_access_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Google Picker embed (frontend, Task 9) needs the connection's
    current OAuth access token to open — this endpoint hands back the same
    token get_valid_drive_access_token() already produces server-side, for
    one-time client-side use by the Picker widget. Not a new/narrower
    scope: the Picker uses the exact drive.file-scoped token already
    granted by the OAuth flow."""
    _auth_headers(monkeypatch)
    from types import SimpleNamespace

    monkeypatch.setattr(
        "src.security.auth.get_current_user_id", AsyncMock(return_value="user1")
    )
    client, db = _client_with_mock_db()
    db.cloudstorageconnection.find_unique = AsyncMock(
        return_value=SimpleNamespace(
            id="conn1", userId="user1", encryptedAccessToken="enc", expiresAt=None
        )
    )
    monkeypatch.setattr(
        "src.api.cloud_storage_oauth.get_valid_drive_access_token",
        AsyncMock(return_value="at-1"),
    )

    resp = client.post("/cloud-storage/connections/conn1/picker-token")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"accessToken": "at-1"}


async def test_picker_token_rejects_other_users_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    """A connection belongs to the user who created it — another
    authenticated user must not be able to mint a Picker token for it."""
    _auth_headers(monkeypatch)
    from types import SimpleNamespace

    monkeypatch.setattr(
        "src.security.auth.get_current_user_id", AsyncMock(return_value="user2")
    )
    client, db = _client_with_mock_db()
    db.cloudstorageconnection.find_unique = AsyncMock(
        return_value=SimpleNamespace(
            id="conn1", userId="user1", encryptedAccessToken="enc", expiresAt=None
        )
    )

    resp = client.post("/cloud-storage/connections/conn1/picker-token")
    assert resp.status_code == 404
```

Before finalizing this test file, read `tests/unit/api/test_mcp_servers.py`'s top-of-file auth-bypass setup (it exercises the same `Depends(get_current_user_id)` pattern this new router uses) and adjust `_auth_headers`/the `get_current_user_id` monkeypatch target above to match exactly — the placeholder comment inside `_auth_headers` flags this as something to verify against that file rather than guess, since the plan-writing research didn't capture that file's auth-bypass helper verbatim.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/api/test_cloud_storage_oauth.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.api.cloud_storage_oauth'` (or a 404 once the module exists but before it's registered in `main.py`) — 5 tests total, all failing

- [ ] **Step 3: Implement the router**

```python
# src/api/cloud_storage_oauth.py
"""Google Drive OAuth connect routes. Structurally mirrors
src/api/mcp_servers.py's oauth_router, adapted for a single fixed-provider
OAuth app (client id/secret from settings, not a per-record oauthConfig).

See docs/archive/phase-history/specs/2026-07-15-google-drive-oauth-file-trigger-design.md §B, §E.
"""

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from src.config import get_settings
from src.integrations.google_drive.oauth import (
    DriveConnectionMissingError,
    GoogleDriveOAuthError,
    build_authorize_url,
    consume_state,
    exchange_code_for_tokens,
    expires_at_from_in,
    get_valid_drive_access_token,
)
from src.security.auth import get_current_user_id
from src.security.encryption import encrypt
from src.storage.db import get_db

router = APIRouter(tags=["cloud-storage"])


class AuthorizeResponse(BaseModel):
    authorize_url: str = Field(alias="authorizeUrl")

    model_config = ConfigDict(populate_by_name=True)


class CloudStorageConnectionRead(BaseModel):
    id: str
    provider: str
    account_email: str = Field(alias="accountEmail")

    model_config = ConfigDict(populate_by_name=True)


def _callback_redirect_uri() -> str:
    return f"{get_settings().backend_public_url}/cloud-storage/google-drive/callback"


@router.get("/cloud-storage/google-drive/authorize", response_model=AuthorizeResponse)
async def google_drive_authorize(
    user_id: str = Depends(get_current_user_id),
) -> AuthorizeResponse:
    url = build_authorize_url(user_id, _callback_redirect_uri())
    return AuthorizeResponse.model_validate({"authorizeUrl": url})


def _popup_close_html(oauth_status: str, detail: str = "") -> HTMLResponse:
    message = json.dumps(
        {"type": "composer:google-drive-oauth", "status": oauth_status, "detail": detail[:200]}
    )
    return HTMLResponse(
        "<html><body><script>"
        f"window.opener && window.opener.postMessage({message}, '*');"
        "window.close();"
        "</script></body></html>"
    )


@router.get("/cloud-storage/google-drive/callback")
async def google_drive_callback(
    code: str = Query(...),
    state: str = Query(...),
    db: Any = Depends(get_db),
) -> HTMLResponse:
    try:
        user_id = consume_state(state)
        payload = await exchange_code_for_tokens(code, _callback_redirect_uri())
    except GoogleDriveOAuthError as exc:
        return _popup_close_html("error", str(exc))

    token_data = {
        "encryptedAccessToken": encrypt(payload["access_token"]),
        "encryptedRefreshToken": (
            encrypt(payload["refresh_token"]) if payload.get("refresh_token") else None
        ),
        "expiresAt": expires_at_from_in(payload.get("expires_in")),
        "scope": payload.get("scope"),
    }
    await db.cloudstorageconnection.upsert(  # pyright: ignore[reportAttributeAccessIssue]
        where={
            "userId_provider_accountEmail": {
                "userId": user_id,
                "provider": "google-drive",
                "accountEmail": payload["email"],
            }
        },
        data={
            "create": {
                "userId": user_id,
                "provider": "google-drive",
                "accountEmail": payload["email"],
                **token_data,
            },
            "update": token_data,
        },
    )
    return _popup_close_html("success")


@router.get("/cloud-storage/connections", response_model=list[CloudStorageConnectionRead])
async def list_cloud_storage_connections(
    provider: str | None = None,
    db: Any = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> list[CloudStorageConnectionRead]:
    where: dict[str, Any] = {"userId": user_id}
    if provider:
        where["provider"] = provider
    rows = await db.cloudstorageconnection.find_many(where=where)  # pyright: ignore[reportAttributeAccessIssue]
    return [
        CloudStorageConnectionRead(id=r.id, provider=r.provider, account_email=r.accountEmail)
        for r in rows
    ]


class PickerTokenResponse(BaseModel):
    access_token: str = Field(alias="accessToken")

    model_config = ConfigDict(populate_by_name=True)


@router.post(
    "/cloud-storage/connections/{connection_id}/picker-token",
    response_model=PickerTokenResponse,
)
async def get_picker_token(
    connection_id: str,
    db: Any = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> PickerTokenResponse:
    """Hand back the connection's current OAuth access token for one-time
    client-side use by the Google Picker embed (Task 9's frontend
    component) — not a new or narrower scope, just the same drive.file
    token get_valid_drive_access_token() already produces server-side,
    exposed for the one call the Picker widget itself requires
    (setOAuthToken). 404s (not 403) for a connection owned by another
    user, matching this codebase's private-resource convention (CLAUDE.md
    Phase 8: private = 404 for non-owner)."""
    connection = await db.cloudstorageconnection.find_unique(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": connection_id}
    )
    if connection is None or connection.userId != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found.")
    try:
        token = await get_valid_drive_access_token(connection_id, db)
    except DriveConnectionMissingError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return PickerTokenResponse.model_validate({"accessToken": token})


__all__ = ["router"]
```

- [ ] **Step 4: Register the router in `src/main.py`**

Near the existing `from src.api.mcp_servers import oauth_router` import (line 25), add:
```python
from src.api.cloud_storage_oauth import router as cloud_storage_oauth_router
```
Near `app.include_router(oauth_router)` (line 145), add:
```python
    app.include_router(cloud_storage_oauth_router)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/api/test_cloud_storage_oauth.py -v`
Expected: PASS (5 passed)

- [ ] **Step 6: Commit**

```bash
git add src/api/cloud_storage_oauth.py src/main.py tests/unit/api/test_cloud_storage_oauth.py
git commit -m "feat(api): add Google Drive OAuth connect + list-connections routes"
```

---

### Task 7: `GoogleDriveProvider` (`FileStorageProvider` implementation)

**Files:**
- Create: `src/storage_providers/google_drive.py`
- Test: `tests/unit/storage_providers/test_google_drive.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/storage_providers/test_google_drive.py
"""Tests for GoogleDriveProvider against a mocked Drive v3 REST API."""

from datetime import UTC, datetime

import pytest
from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]


async def test_list_new_files_excludes_marked_files(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    from src.storage_providers.google_drive import GoogleDriveProvider

    httpx_mock.add_response(
        url=lambda request: str(request.url).startswith(
            "https://www.googleapis.com/drive/v3/files?"
        ),
        method="GET",
        json={
            "files": [
                {"id": "f1", "name": "report.pdf", "size": "1024", "modifiedTime": "2026-07-16T00:00:00.000Z"}
            ]
        },
    )

    provider = GoogleDriveProvider("at-1")
    refs = await provider.list_new_files("folder123")

    assert len(refs) == 1
    assert refs[0].identifier == "f1"
    assert refs[0].name == "report.pdf"
    assert refs[0].size_bytes == 1024
    assert refs[0].modified_at == datetime(2026, 7, 16, tzinfo=UTC)

    req = httpx_mock.get_request()
    assert req is not None
    assert "not+appProperties" in str(req.url) or "not%20appProperties" in str(req.url)
    assert req.headers.get("authorization") == "Bearer at-1"


async def test_read_file_downloads_media(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    from src.storage_providers.base import FileRef
    from src.storage_providers.google_drive import GoogleDriveProvider

    httpx_mock.add_response(
        url="https://www.googleapis.com/drive/v3/files/f1?alt=media",
        method="GET",
        content=b"file bytes",
    )

    provider = GoogleDriveProvider("at-1")
    ref = FileRef(identifier="f1", name="report.pdf", size_bytes=10, modified_at=datetime.now(UTC))
    content = await provider.read_file(ref)

    assert content == b"file bytes"


async def test_move_file_sets_app_properties(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    from src.storage_providers.base import FileRef
    from src.storage_providers.google_drive import GoogleDriveProvider

    httpx_mock.add_response(
        url="https://www.googleapis.com/drive/v3/files/f1",
        method="PATCH",
        json={"id": "f1"},
    )

    provider = GoogleDriveProvider("at-1")
    ref = FileRef(identifier="f1", name="report.pdf", size_bytes=10, modified_at=datetime.now(UTC))
    await provider.move_file(ref, "processed")

    req = httpx_mock.get_request()
    assert req is not None
    import json as _json

    body = _json.loads(req.content)
    assert body == {"appProperties": {"composerStatus": "processed"}}


async def test_write_file_raises_not_implemented() -> None:
    from src.storage_providers.google_drive import GoogleDriveProvider

    provider = GoogleDriveProvider("at-1")
    with pytest.raises(NotImplementedError):
        await provider.write_file("dest", "name.md", b"content")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/storage_providers/test_google_drive.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.storage_providers.google_drive'`

- [ ] **Step 3: Implement the provider**

```python
# src/storage_providers/google_drive.py
"""GoogleDriveProvider — FileStorageProvider implementation against the
Drive v3 REST API via direct httpx calls (no google-api-python-client —
matches this codebase's manual-HTTP-over-SDK preference, see CLAUDE.md §1
fix #2 and this feature's design doc §C).

Claim mechanism: instead of moving files (the local provider's approach),
this provider marks a claimed file with a `composerStatus` appProperty
("processed" or "error") — that mark IS the state list_new_files excludes
on future polls, permanent regardless of outcome, mirroring local's
never-re-claim semantics.
"""

from datetime import datetime

import httpx

from src.storage_providers.base import FileRef, HealthStatus, FileStorageProvider

DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"


class GoogleDriveProviderError(RuntimeError):
    """Raised when the Drive API rejects or cannot process a request."""


class GoogleDriveProvider(FileStorageProvider):
    name = "google-drive"

    def __init__(self, access_token: str) -> None:
        self.access_token = access_token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}

    async def list_new_files(self, source: str) -> list[FileRef]:
        # Drive query language requires both key AND value inside `has{}` —
        # there is no "key present regardless of value" predicate — so
        # unclaimed means neither status value is set.
        query = (
            f"'{source}' in parents and trashed = false "
            "and not appProperties has { key='composerStatus' and value='processed' } "
            "and not appProperties has { key='composerStatus' and value='error' }"
        )
        params = {"q": query, "fields": "files(id,name,size,modifiedTime)", "pageSize": "20"}
        async with httpx.AsyncClient(
            base_url=DRIVE_API_BASE, headers=self._headers(), timeout=httpx.Timeout(30.0, connect=5.0)
        ) as client:
            resp = await client.get("/files", params=params)
        if resp.status_code >= 400:
            raise GoogleDriveProviderError(f"Drive list failed (HTTP {resp.status_code}): {resp.text[:300]}")
        files = resp.json().get("files", [])
        return [
            FileRef(
                identifier=f["id"],
                name=f["name"],
                size_bytes=int(f.get("size", 0)),
                modified_at=datetime.fromisoformat(f["modifiedTime"].replace("Z", "+00:00")),
            )
            for f in files
        ]

    async def read_file(self, ref: FileRef) -> bytes:
        async with httpx.AsyncClient(
            base_url=DRIVE_API_BASE, headers=self._headers(), timeout=httpx.Timeout(60.0, connect=5.0)
        ) as client:
            resp = await client.get(f"/files/{ref.identifier}", params={"alt": "media"})
        if resp.status_code >= 400:
            raise GoogleDriveProviderError(f"Drive read failed (HTTP {resp.status_code}): {resp.text[:300]}")
        return resp.content

    async def move_file(self, ref: FileRef, dest: str) -> None:
        """`dest` is the composerStatus value ("processed"/"error"), not a
        path — this provider's claim mechanism is a marker, not a move."""
        async with httpx.AsyncClient(
            base_url=DRIVE_API_BASE, headers=self._headers(), timeout=httpx.Timeout(30.0, connect=5.0)
        ) as client:
            resp = await client.patch(
                f"/files/{ref.identifier}", json={"appProperties": {"composerStatus": dest}}
            )
        if resp.status_code >= 400:
            raise GoogleDriveProviderError(
                f"Drive mark-processed failed (HTTP {resp.status_code}): {resp.text[:300]}"
            )

    async def write_file(self, dest: str, filename: str, content: bytes) -> None:
        raise NotImplementedError(
            "GoogleDriveProvider is trigger-only; unrelated to the file-write node"
        )

    async def health_check(self) -> HealthStatus:
        return HealthStatus(ok=True, message="no health check defined")


__all__ = ["GoogleDriveProvider", "GoogleDriveProviderError"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/storage_providers/test_google_drive.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/storage_providers/google_drive.py tests/unit/storage_providers/test_google_drive.py
git commit -m "feat: add GoogleDriveProvider (FileStorageProvider over Drive v3 REST API)"
```

---

### Task 8: `POST /internal/poll-file-triggers`

**Files:**
- Modify: `src/api/internal.py` (add imports + new route, after `sweep`)
- Test: `tests/unit/api/test_internal_poll_file_triggers.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/api/test_internal_poll_file_triggers.py
"""Tests for POST /internal/poll-file-triggers."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.main import create_app


def _client_with_mock_db() -> tuple[TestClient, MagicMock]:
    app = create_app()
    db = MagicMock()
    db.workflow = MagicMock()
    db.cloudstorageconnection = MagicMock()
    db.workflowexecution = MagicMock()
    app.state.db = db
    app.state.checkpointer = MagicMock()
    from src.engine.events_pg import PostgresEventStore

    app.state.event_bus = PostgresEventStore(db)
    return TestClient(app), db


def _workflow_with_drive_trigger() -> SimpleNamespace:
    return SimpleNamespace(
        id="wf1",
        isProduction=True,
        nodes=[
            {
                "id": "ft1",
                "type": "file-trigger",
                "position": {"x": 0, "y": 0},
                "data": {
                    "label": "Watch Folder",
                    "provider": "google-drive",
                    "connectionId": "conn1",
                    "driveFolderId": "folder123",
                    "targetInputVariable": "file_content",
                },
            },
        ],
    )


def test_poll_endpoint_rejects_unauthenticated_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "CLOUD_TASKS_SERVICE_ACCOUNT", "scheduler@test-project.iam.gserviceaccount.com"
    )
    client, db = _client_with_mock_db()
    db.workflow.find_many = AsyncMock(side_effect=AssertionError("should not be called"))

    resp = client.post("/internal/poll-file-triggers")

    assert resp.status_code == 401


def test_poll_endpoint_processes_new_file_and_marks_it(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client_with_mock_db()
    db.workflow.find_many = AsyncMock(return_value=[_workflow_with_drive_trigger()])
    db.cloudstorageconnection.find_unique = AsyncMock(
        return_value=SimpleNamespace(id="conn1", userId="user1")
    )

    from src.storage_providers.base import FileRef
    from datetime import UTC, datetime

    fake_provider = MagicMock()
    fake_provider.list_new_files = AsyncMock(
        return_value=[
            FileRef(identifier="f1", name="notes.txt", size_bytes=5, modified_at=datetime.now(UTC))
        ]
    )
    fake_provider.read_file = AsyncMock(return_value=b"hello")
    fake_provider.move_file = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "src.api.internal.GoogleDriveProvider", MagicMock(return_value=fake_provider)
    )
    monkeypatch.setattr(
        "src.api.internal.get_valid_drive_access_token", AsyncMock(return_value="at-1")
    )

    fake_execution = SimpleNamespace(id="exec1")
    monkeypatch.setattr(
        "src.engine.langgraph_executor.LangGraphExecutor.start_execution",
        AsyncMock(return_value=fake_execution),
    )
    monkeypatch.setattr("src.api.internal.enqueue_execution", AsyncMock(return_value=None))

    resp = client.post("/internal/poll-file-triggers")

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"triggered": 1, "failed": 0}
    fake_provider.move_file.assert_awaited_once_with(
        fake_provider.list_new_files.return_value[0], "processed"
    )


def test_poll_endpoint_isolates_per_file_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """One file's extraction/trigger failure marks it 'error' and continues
    — mirrors composer watch's per-file try/except (design doc §D.4)."""
    client, db = _client_with_mock_db()
    db.workflow.find_many = AsyncMock(return_value=[_workflow_with_drive_trigger()])
    db.cloudstorageconnection.find_unique = AsyncMock(
        return_value=SimpleNamespace(id="conn1", userId="user1")
    )

    from src.storage_providers.base import FileRef
    from datetime import UTC, datetime

    fake_provider = MagicMock()
    fake_provider.list_new_files = AsyncMock(
        return_value=[
            FileRef(identifier="f1", name="image.png", size_bytes=5, modified_at=datetime.now(UTC))
        ]
    )
    fake_provider.read_file = AsyncMock(return_value=b"\x89PNG")  # unsupported extension
    fake_provider.move_file = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "src.api.internal.GoogleDriveProvider", MagicMock(return_value=fake_provider)
    )
    monkeypatch.setattr(
        "src.api.internal.get_valid_drive_access_token", AsyncMock(return_value="at-1")
    )

    resp = client.post("/internal/poll-file-triggers")

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"triggered": 0, "failed": 1}
    fake_provider.move_file.assert_awaited_once_with(
        fake_provider.list_new_files.return_value[0], "error"
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/api/test_internal_poll_file_triggers.py -v`
Expected: FAIL — `test_poll_endpoint_rejects_unauthenticated_call` passes trivially (OIDC guard already exists for the whole router... actually FAILS with 404, since the route doesn't exist yet), the other two FAIL with 404 Not Found.

- [ ] **Step 3: Add the route to `src/api/internal.py`**

Add these imports near the top of `src/api/internal.py`, alongside the existing ones (after the `from src.storage.db import ...` line):

```python
from src.engine.workflow import FileTriggerNode
from src.execution.cloud_tasks import enqueue_execution
from src.integrations.google_drive.oauth import get_valid_drive_access_token
from src.storage_providers.google_drive import GoogleDriveProvider
from src.storage_providers.text_extraction import extract_text
```

Add the new route after `sweep` (after line 478's closing of that function, before `__all__`):

```python
@router.post("/internal/poll-file-triggers", status_code=status.HTTP_200_OK)
async def poll_file_triggers(  # pyright: ignore[reportUnusedFunction]
    request: Request,
    db: Any = Depends(get_db),
    _oidc: None = Depends(_verify_internal_oidc),
) -> dict[str, int]:
    """Cloud-Scheduler-triggered poll of every production workflow's
    google-drive file-trigger node — the server-side replacement for
    `composer watch`, which only works for locally-hosted folders. Fifth
    sweep-style endpoint alongside claim-and-run/sweep: same OIDC auth,
    same isolate-failures-per-item shape as `sweep`. See
    docs/archive/phase-history/specs/2026-07-15-google-drive-oauth-file-trigger-design.md §D.
    """
    workflows = await db.workflow.find_many(where={"isProduction": True})  # pyright: ignore[reportAttributeAccessIssue]
    checkpointer = get_checkpointer(request)
    event_bus = get_event_bus(request)
    executor = LangGraphExecutor(db=db, checkpointer=checkpointer, event_bus=event_bus)

    triggered = 0
    failed = 0
    for wf in workflows:
        for raw_node in wf.nodes or []:
            if raw_node.get("type") != "file-trigger":
                continue
            data = raw_node.get("data", {})
            if data.get("provider") != "google-drive":
                continue
            node = FileTriggerNode.model_validate(raw_node)
            connection_id = node.data.connection_id
            folder_id = node.data.drive_folder_id
            target_var = node.data.target_input_variable
            if not connection_id or not folder_id or not target_var:
                continue

            connection = await db.cloudstorageconnection.find_unique(  # pyright: ignore[reportAttributeAccessIssue]
                where={"id": connection_id}
            )
            if connection is None:
                logger.warning(
                    "poll_file_triggers: workflow %s node %s references missing connection %s",
                    wf.id,
                    node.id,
                    connection_id,
                )
                continue

            try:
                access_token = await get_valid_drive_access_token(connection_id, db)
            except Exception:
                logger.exception(
                    "poll_file_triggers: failed to get access token for connection %s", connection_id
                )
                continue

            provider = GoogleDriveProvider(access_token)
            try:
                refs = await provider.list_new_files(folder_id)
            except Exception:
                logger.exception("poll_file_triggers: list_new_files failed for workflow %s", wf.id)
                continue

            for ref in refs:
                try:
                    raw = await provider.read_file(ref)
                    text = extract_text(ref.name, raw)
                    execution = await executor.start_execution(
                        workflow_id=wf.id,
                        input={target_var: text},
                        user_id=connection.userId,
                    )
                    await enqueue_execution(execution.id, kind="run")
                except Exception:
                    logger.exception(
                        "poll_file_triggers: failed to process file %s (workflow %s)",
                        ref.identifier,
                        wf.id,
                    )
                    await provider.move_file(ref, "error")
                    failed += 1
                    continue
                await provider.move_file(ref, "processed")
                triggered += 1

    return {"triggered": triggered, "failed": failed}
```

Also update `__all__` at the bottom of the file if it lists route function names (check first — the current `__all__ = ["router"]` only exports the router itself, so no change needed there).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/api/test_internal_poll_file_triggers.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Run the full internal + sweep test suite to confirm no regression**

Run: `uv run pytest tests/unit/api/test_internal.py tests/unit/api/test_internal_sweep.py tests/unit/api/test_internal_poll_file_triggers.py -v`
Expected: PASS (all green — the new imports/route didn't disturb `claim_and_run`/`sweep`)

- [ ] **Step 6: Commit**

```bash
git add src/api/internal.py tests/unit/api/test_internal_poll_file_triggers.py
git commit -m "feat(api): add POST /internal/poll-file-triggers (Cloud Scheduler target)"
```

---

### Task 9: Frontend — Google Drive connect flow in the `file-trigger` panel

**Files:**
- Create: `frontend/lib/api/cloud-storage.ts`
- Create: `frontend/components/composer/canvas/node-panels/google-drive-connect.tsx`
- Modify: `frontend/components/composer/canvas/node-panels/file-trigger.tsx`

- [ ] **Step 1: Add the API client**

```typescript
// frontend/lib/api/cloud-storage.ts
import { apiFetch } from "./client";

export interface CloudStorageConnection {
  id: string;
  provider: string;
  accountEmail: string;
}

export async function getGoogleDriveAuthorizeUrl(): Promise<string> {
  const res = await apiFetch<{ authorizeUrl: string }>(
    "/cloud-storage/google-drive/authorize"
  );
  return res.authorizeUrl;
}

export async function listCloudStorageConnections(
  provider: string
): Promise<CloudStorageConnection[]> {
  return apiFetch<CloudStorageConnection[]>(
    `/cloud-storage/connections?provider=${encodeURIComponent(provider)}`
  );
}

export async function getPickerToken(connectionId: string): Promise<string> {
  const res = await apiFetch<{ accessToken: string }>(
    `/cloud-storage/connections/${encodeURIComponent(connectionId)}/picker-token`,
    { method: "POST" }
  );
  return res.accessToken;
}
```

- [ ] **Step 2: Add the Google Picker loader + connect component**

```tsx
// frontend/components/composer/canvas/node-panels/google-drive-connect.tsx
"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  getGoogleDriveAuthorizeUrl,
  getPickerToken,
  listCloudStorageConnections,
  type CloudStorageConnection,
} from "@/lib/api/cloud-storage";

declare global {
  interface Window {
    google?: {
      picker: {
        DocsView: new (viewId: unknown) => {
          setSelectFolderEnabled: (v: boolean) => unknown;
          setIncludeFolders: (v: boolean) => unknown;
        };
        ViewId: { FOLDERS: unknown };
        PickerBuilder: new () => {
          addView: (view: unknown) => unknown;
          setOAuthToken: (token: string) => unknown;
          setDeveloperKey: (key: string) => unknown;
          setCallback: (cb: (data: { action: string; docs?: { id: string }[] }) => void) => unknown;
          build: () => { setVisible: (v: boolean) => void };
        };
        Action: { PICKED: string };
      };
    };
    gapi?: { load: (api: string, opts: { callback: () => void }) => void };
  }
}

function loadGooglePicker(): Promise<void> {
  return new Promise((resolve) => {
    if (window.google?.picker) {
      resolve();
      return;
    }
    const script = document.createElement("script");
    script.src = "https://apis.google.com/js/api.js";
    script.onload = () => {
      window.gapi?.load("picker", { callback: () => resolve() });
    };
    document.body.appendChild(script);
  });
}

export default function GoogleDriveConnect({
  connectionId,
  driveFolderId,
  onChange,
}: {
  connectionId: string | undefined;
  driveFolderId: string | undefined;
  onChange: (patch: Record<string, unknown>) => void;
}) {
  const [connections, setConnections] = useState<CloudStorageConnection[]>([]);
  const [connecting, setConnecting] = useState(false);
  const [pickerBusy, setPickerBusy] = useState(false);

  const refreshConnections = () => {
    listCloudStorageConnections("google-drive").then(setConnections);
  };

  useEffect(() => {
    refreshConnections();
  }, []);

  useEffect(() => {
    function onMessage(event: MessageEvent) {
      if (event.data?.type !== "composer:google-drive-oauth") return;
      setConnecting(false);
      if (event.data.status === "success") refreshConnections();
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  const connected = connections.find((c) => c.id === connectionId);

  async function handleConnect() {
    setConnecting(true);
    const url = await getGoogleDriveAuthorizeUrl();
    window.open(url, "composer-google-drive-oauth", "width=500,height=650");
  }

  async function handlePickFolder() {
    if (!connected) return;
    setPickerBusy(true);
    try {
      const accessToken = await getPickerToken(connected.id);
      await loadGooglePicker();
      const google = window.google;
      if (!google) return;
      const view = new google.picker.DocsView(google.picker.ViewId.FOLDERS)
        .setSelectFolderEnabled(true)
        .setIncludeFolders(true);
      const picker = new google.picker.PickerBuilder()
        .addView(view)
        .setOAuthToken(accessToken)
        .setDeveloperKey(process.env.NEXT_PUBLIC_GOOGLE_PICKER_API_KEY ?? "")
        .setCallback((data) => {
          if (data.action === google.picker.Action.PICKED && data.docs?.[0]) {
            onChange({ connectionId: connected.id, driveFolderId: data.docs[0].id });
          }
        })
        .build();
      picker.setVisible(true);
    } finally {
      setPickerBusy(false);
    }
  }

  return (
    <div className="space-y-2">
      {!connected ? (
        <Button type="button" size="sm" disabled={connecting} onClick={handleConnect}>
          {connecting ? "Connecting…" : "Connect Google Drive"}
        </Button>
      ) : (
        <div className="space-y-2">
          <div className="text-xs text-muted-foreground">
            Connected as {connected.accountEmail}
            {driveFolderId ? ` — watching folder ${driveFolderId}` : " — no folder selected yet"}
          </div>
          <Button type="button" size="sm" variant="outline" disabled={pickerBusy} onClick={handlePickFolder}>
            {pickerBusy ? "Opening…" : driveFolderId ? "Change folder" : "Select folder"}
          </Button>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Wire the provider picker into `file-trigger.tsx`**

In `frontend/components/composer/canvas/node-panels/file-trigger.tsx`, add an import at the top:
```tsx
import GoogleDriveConnect from "./google-drive-connect";
```

Change the `Provider` `NativeSelect` (currently hardcoded to a single `"local"` option, lines 33-38) to:
```tsx
            <NativeSelect
              id="ft-provider"
              value={(data.provider as string) ?? "local"}
              onValueChange={(v) => onChange({ provider: v })}
              options={[
                { value: "local", label: "Local filesystem" },
                { value: "google-drive", label: "Google Drive" },
              ]}
            />
```

Wrap the existing local-only fields (Source path / Destination path / Error path / Poll interval — everything between the Provider select and the "Target Start input variable" field) in a conditional, and add the Drive branch. Replace the block from the `<Label htmlFor="ft-source">` div through the `<Label htmlFor="ft-poll">` div (lines 40-107 of the original file) with:

```tsx
          {(data.provider as string) !== "google-drive" ? (
            <>
              <div className="space-y-1">
                <Label htmlFor="ft-source" className="text-xs">
                  Source path
                </Label>
                <Input
                  id="ft-source"
                  value={(data.sourcePath as string) ?? ""}
                  onChange={(e) => onChange({ sourcePath: e.target.value })}
                  placeholder="/watch/in"
                  className="font-mono text-xs"
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="ft-dest" className="text-xs">
                  Destination path (on successful claim)
                </Label>
                <Input
                  id="ft-dest"
                  value={(data.destPath as string) ?? ""}
                  onChange={(e) => onChange({ destPath: e.target.value })}
                  placeholder="/watch/done"
                  className="font-mono text-xs"
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="ft-error" className="text-xs">
                  Error path (on extraction/trigger failure)
                </Label>
                <Input
                  id="ft-error"
                  value={(data.errorPath as string) ?? ""}
                  onChange={(e) => onChange({ errorPath: e.target.value })}
                  placeholder="/watch/error"
                  className="font-mono text-xs"
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="ft-poll" className="text-xs">
                  Poll interval (seconds)
                </Label>
                <Input
                  id="ft-poll"
                  type="number"
                  min={1}
                  value={
                    typeof data.pollIntervalSeconds === "number"
                      ? String(data.pollIntervalSeconds)
                      : "30"
                  }
                  onChange={(e) => {
                    const n = parseInt(e.target.value, 10);
                    onChange({ pollIntervalSeconds: Number.isFinite(n) ? n : 30 });
                  }}
                  className="font-mono text-xs"
                />
              </div>
            </>
          ) : (
            <GoogleDriveConnect
              connectionId={data.connectionId as string | undefined}
              driveFolderId={data.driveFolderId as string | undefined}
              onChange={onChange}
            />
          )}
```

Update the descriptive paragraph above the `Provider` label (lines 20-27 of the original) to mention both mechanisms:
```tsx
        <p className="mb-3 text-[10px] text-muted-foreground">
          This node is visual-only — it does not run as part of the
          workflow. For Local filesystem, it configures the{" "}
          <code>composer watch</code> CLI, which polls the source folder and
          triggers this workflow when a new file is claimed. For Google
          Drive, Composer's own backend polls the connected account on a
          schedule — no local process needed. Publish this workflow
          (Production toggle) before either mechanism can trigger it.
        </p>
```

- [ ] **Step 4: Type-check the frontend**

Run: `cd frontend && npm run typecheck` (or the project's equivalent — check `frontend/package.json`'s `scripts` block for the exact command name if `typecheck` isn't it)
Expected: no new type errors introduced by `google-drive-connect.tsx`, `cloud-storage.ts`, or the `file-trigger.tsx` edit.

- [ ] **Step 5: Manual smoke test**

Run the frontend dev server (`npm run dev` in `frontend/`) and the backend (`uv run uvicorn src.main:app --reload`), open the Designer, drag a `file-trigger` node onto the canvas, open its property panel, switch the provider dropdown to "Google Drive," and confirm the "Connect Google Drive" button renders (clicking it will 400/fail without real `GOOGLE_OAUTH_CLIENT_ID`/`GOOGLE_OAUTH_CLIENT_SECRET` configured — that's expected until Task 10's manual GCP provisioning is done; this step is checking the UI renders and the request is well-formed, not a full OAuth round-trip).

- [ ] **Step 6: Commit**

```bash
git add frontend/lib/api/cloud-storage.ts frontend/components/composer/canvas/node-panels/google-drive-connect.tsx frontend/components/composer/canvas/node-panels/file-trigger.tsx
git commit -m "feat(frontend): add Google Drive connect + folder picker to file-trigger panel"
```

---

### Task 10: Google Cloud provisioning (manual console steps + env var placeholders)

This task is manual GCP Console work plus documentation — no code changes beyond `.env` (local, gitignored) and a doc note. **Do not run any `gcloud` provisioning commands against production infrastructure without explicit sign-off** — this project's standing rule (per prior session's P1-2/P1-4 deploy-pause guidance) is: code freely, provision/deploy only with explicit go-ahead.

**Files:**
- Modify: `scripts/gcp-bootstrap.ps1` (document the additional manual steps as comments; do not add commands that provision Cloud Scheduler jobs without a follow-up confirmation step)

- [ ] **Step 1: Manual Google Cloud Console steps**

On the same GCP project already used for Cloud Tasks/Scheduler (per ADR-0033):
1. Enable the **Drive API** (APIs & Services → Library → "Google Drive API" → Enable).
2. Configure the **OAuth consent screen**: External, publishing status **Testing**, add each user who will connect a Drive account as a test user (up to 100, no Google verification needed).
3. Create an **OAuth 2.0 Client ID** (APIs & Services → Credentials → Create Credentials → OAuth client ID → Web application), with an **Authorized redirect URI** of `{BACKEND_PUBLIC_URL}/cloud-storage/google-drive/callback` (both a `localhost:8000` entry for local dev and the real production `BACKEND_PUBLIC_URL` once known).
4. Create an **API key** (Credentials → Create Credentials → API key), then restrict it to the **Picker API** only (Edit API key → API restrictions → Picker API).
5. Copy the Client ID, Client Secret, and API key into local `.env`:
   ```
   GOOGLE_OAUTH_CLIENT_ID=<client id from step 3>
   GOOGLE_OAUTH_CLIENT_SECRET=<client secret from step 3>
   GOOGLE_PICKER_API_KEY=<api key from step 4>
   ```
   Also add `NEXT_PUBLIC_GOOGLE_PICKER_API_KEY=<same api key>` to `frontend/.env.local` (the frontend needs it client-side for the Picker embed in Task 9).

- [ ] **Step 2: Add a Cloud Scheduler job for the new poll endpoint (deploy-time, not now)**

Document (do not execute yet) the additional Cloud Scheduler job needed alongside the existing `/internal/sweep` job in `scripts/gcp-bootstrap.ps1` — add a comment block near the existing sweep-job provisioning commands:

```powershell
# Google Drive file-trigger polling (2026-07-16) — NOT yet provisioned;
# run this manually once GOOGLE_OAUTH_CLIENT_ID/SECRET are set in the
# deployed backend's env and Task 10's manual console steps are done.
# Mirrors the existing /internal/sweep Cloud Scheduler job below.
#
# gcloud scheduler jobs create http composer-poll-file-triggers `
#   --schedule="*/5 * * * *" `
#   --uri="$BackendPublicUrl/internal/poll-file-triggers" `
#   --http-method=POST `
#   --oidc-service-account-email=$CloudTasksServiceAccount `
#   --oidc-token-audience="$BackendPublicUrl/internal/poll-file-triggers" `
#   --location=$GcpRegion
```

- [ ] **Step 3: Commit the documentation-only change**

```bash
git add scripts/gcp-bootstrap.ps1
git commit -m "docs: document Google Drive OAuth provisioning + poll-file-triggers scheduler job"
```

---

### Task 11: CHANGELOG entry + design doc status

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `docs/archive/phase-history/specs/2026-07-15-google-drive-oauth-file-trigger-design.md` (status line)

- [ ] **Step 1: Add the CHANGELOG entry**

Insert under `## [Unreleased]`, above the most recent existing entry:

```markdown
### Added — Google Drive OAuth file-trigger (server-side polling, no local agent) (2026-07-16)

Implements the design in `docs/archive/phase-history/specs/2026-07-15-google-drive-oauth-file-trigger-design.md`: a `file-trigger` node can now point at a Google Drive folder instead of a local one. New `CloudStorageConnection` model stores per-user OAuth tokens (mirrors `McpOAuthToken`'s encryption pattern); `src/integrations/google_drive/oauth.py` handles the OAuth dance via direct `httpx` calls (no SDK); `GoogleDriveProvider` implements the existing `FileStorageProvider` ABC, using a Drive `appProperties` marker as its claim mechanism instead of local's move-to-folder. `POST /internal/poll-file-triggers` — a fifth sibling to ADR-0033's `claim-and-run`/`sweep`, same OIDC auth — is the Cloud-Scheduler-triggered poll loop; a flat 5-minute cadence applies to all Drive triggers (per-node `pollIntervalSeconds` stays meaningful for the local/CLI case only). Frontend gains a "Connect Google Drive" + Google Picker folder-select flow in the `file-trigger` node's property panel. No new dependencies — OAuth and Drive API calls go through `httpx`, already a dependency, consistent with the MCP integration's manual-HTTP-over-SDK precedent.

Deliberately out of scope for this pass (see design doc's Non-goals): shared/service-account connections, Dropbox/OneDrive/S3 providers, native Google Docs export, Drive Changes API cursoring, per-node custom poll cadence for cloud triggers.
```

- [ ] **Step 2: Update the design doc's status line**

Change:
```
**Status:** Approved (2026-07-15)
```
to:
```
**Status:** Implemented (2026-07-16)
```

- [ ] **Step 3: Run the full test suite once more before wrapping up**

Run: `uv run pytest`
Expected: all tests pass (existing suite + every new test file added in Tasks 2-8).

Run: `uv run ruff check src tests`
Expected: no errors.

Run: `uv run ruff format --check src tests`
Expected: no reformatting needed for any file this plan touched (pre-existing drift elsewhere, if any, is out of scope — see the 2026-07-15 session's precedent of not fixing unrelated formatting).

Run: `uv run pyright src tests`
Expected: no new errors introduced by this feature's files.

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md docs/archive/phase-history/specs/2026-07-15-google-drive-oauth-file-trigger-design.md
git commit -m "docs: record Google Drive OAuth file-trigger completion in CHANGELOG"
```

---

## Self-Review Notes

**Spec coverage:** §A (data model) → Task 1, 4. §B (OAuth flow) → Task 5, 6. §C (`GoogleDriveProvider`) → Task 7. §D (scheduled polling) → Task 8. §E (frontend) → Task 9. §F (GCP provisioning) → Task 10. §G (error handling) → covered inline in Task 8's per-file try/except + Task 7's permanent-claim marker. §H (testing strategy) → every task carries its own unit tests; no real-Google-account integration test added, matching the design doc's explicit call for a manual smoke test instead (Task 9 Step 5 covers the UI half; a full OAuth-round-trip smoke test happens naturally once Task 10's provisioning is complete and someone clicks "Connect" for real).

**Gap caught and closed during self-review:** the first draft of Task 9's `GoogleDriveConnect` component built a `handlePickFolder` function that was never called and needed an access token it had no way to fetch — the Picker literally could not open. Fixed by adding `POST /cloud-storage/connections/{connection_id}/picker-token` to Task 6 (returns the same `get_valid_drive_access_token()` result already used server-side — not a new or narrower scope, just exposing the existing drive.file-scoped token for the one call the Picker widget requires), a "Select folder" button that calls it, and 404-for-non-owner coverage in that endpoint's tests. The connect flow is now wired end-to-end: connect → list connections → pick folder → node stores `connectionId`/`driveFolderId` → poller (Task 8) uses both.

**Type consistency check:** `connection_id`/`driveFolderId` naming is consistent across every layer — Prisma `CloudStorageConnection.id` → `FileTriggerNodeData.connection_id` (alias `connectionId`) → `POST /internal/poll-file-triggers`'s `node.data.connection_id` → frontend `GoogleDriveConnect`'s `connectionId` prop → `getPickerToken(connectionId)`. `get_valid_drive_access_token` takes `connection_id: str` everywhere it's called (Task 5's definition, Task 6's two call sites, Task 8's poller) — no drift to a `connection` object anywhere.
