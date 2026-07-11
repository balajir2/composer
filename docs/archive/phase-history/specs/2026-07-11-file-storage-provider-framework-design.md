# File Storage Provider Framework — Design

**Status:** Approved (2026-07-11)
**Context:** Two new capabilities the user wants, both needing the same underlying abstraction: (1) a node that detects a new file appearing in a folder and triggers a workflow, moving the file once claimed; (2) a node that writes generated content (e.g. a drafted BRD) out to a file — as PDF, Word, or Markdown — before the flow continues. Both are designed against a shared, pluggable `FileStorageProvider` interface: local filesystem now, S3/Google Drive/OneDrive as documented future providers behind the same interface.

## What already exists (do not re-derive)

- `src/tools/base.py` — the `ToolProvider` ABC pattern this framework is modeled on: abstract methods, a `category` discriminator, `@register_tool_provider` registration, a default `health_check()`. `FileStorageProvider` follows the same shape.
- `src/api/uploads.py` — `/uploads/extract-text` already extracts plain text from `.txt`/`.md`/`.pdf`/`.docx` using **`pypdf`** (PDF) and **`python-docx`** (DOCX) — both **already project dependencies**, used today for reading. The file-write node's DOCX path reuses `python-docx` directly (already installed); only DOCX/MD get this "free" benefit — PDF *generation* (as opposed to the existing PDF *reading*) still needs a new library, since `pypdf` isn't a generation tool.
- `src/api/run.py` — `POST /api/run/{slug}` external-invoke: takes a `ck_...` bearer-token (`ApiKey` Prisma model, `src/security/api_key_auth.py`), resolves a **production** workflow (`isProduction=true` + `externalSlug`), accepts a JSON `{input: {...}}` body, returns async-by-default (matches "no direct human login" triggering — exactly this use case). This design reuses this endpoint as-is for the file-trigger's actual "start the workflow" call — **no new trigger endpoint is needed**.
- `src/engine/workflow.py` — `NoteNodeData`/`NoteNode`: the precedent for a node type that is **visual-only** — "note (visual-only; executor is a no-op; graph_builder skips)". The new `file-trigger` node type follows this exact precedent.
- `src/cli/main.py` — the `composer <subcommand>` argparse dispatcher (`migrate`, `reconcile`, `keys`). The new `composer watch` subcommand follows this identical structure.
- Node-type wiring convention established across every prior node (Jira most recently): `src/engine/workflow.py` (Pydantic data class), `src/executors/base.py` registry, `src/engine/graph_builder.py`, frontend `node-visuals.ts` / `tools-palette.tsx` (`COMPOSER_NODE_PALETTE`) / `property-panel.tsx` / `workflow-canvas.tsx` (`COMPOSER_NODE_TYPES`) / a new `node-panels/*.tsx` panel. Both new node types (`file-trigger`, `file-write`) go through every one of these exact same registries — this is precisely the class of gap the 2026-07-11 Jira-node review caught (missing from `COMPOSER_NODE_TYPES`) and must not repeat here.

## A. `FileStorageProvider` framework

New module `src/storage_providers/base.py` (new top-level package, parallel to `src/tools/` and `src/vectordb/`):

```python
class FileRef(NamedTuple):
    """Opaque handle a provider hands back — path/key semantics are provider-specific."""
    identifier: str      # local: absolute path; s3 (future): object key; etc.
    name: str             # display filename
    size_bytes: int
    modified_at: datetime

class FileStorageProvider(ABC):
    name: str                    # "local" | "s3" | "google-drive" | "onedrive"

    @abstractmethod
    async def list_new_files(self, source: str) -> list[FileRef]: ...

    @abstractmethod
    async def read_file(self, ref: FileRef) -> bytes: ...

    @abstractmethod
    async def move_file(self, ref: FileRef, dest: str) -> None: ...

    @abstractmethod
    async def write_file(self, dest: str, filename: str, content: bytes) -> None: ...

    async def health_check(self) -> HealthStatus: ...   # default: provider-specific override
```

Read-side methods (`list_new_files`, `read_file`, `move_file`) serve the `file-trigger` node/watcher; the write-side method (`write_file`) serves the `file-write` node. One interface, one registry, shared by both node types — deliberate, since a future S3/Drive/OneDrive implementation only needs to be written once to serve both.

### `LocalFilesystemProvider` (first, only implementation built now)

- `list_new_files(source)`: lists files in `source`, applying the **partial-write safety** rule — a file is only "new" once its `(size_bytes, modified_at)` is unchanged across two consecutive polls (guards against claiming a file mid-copy/mid-write). Requires the provider to retain small in-memory state between polls (a dict of `path → (size, mtime, first_seen_stable_at)`), scoped to one watcher-process run.
- `read_file(ref)`: plain `open(ref.identifier, "rb").read()`.
- `move_file(ref, dest)`: `shutil.move`, creating `dest` if it doesn't exist.
- `write_file(dest, filename, content)`: creates `dest` if needed, writes `content` bytes to `dest/filename`.

### Future providers (stubbed, documented, not built now)

- `S3Provider` — `list_new_files` via `list_objects_v2` + a persisted "last seen" marker (or S3 event notifications + SQS for a push model later); `move_file` via copy+delete (S3 has no native rename); credentials via inline access-key/secret fields on the node (per the "inline per-node" decision) or instance-role credentials when running as a Cloud Run job near the bucket.
- `GoogleDriveProvider` / `OneDriveProvider` — both are OAuth-based; would follow the MCP OAuth precedent (`src/mcp/oauth.py`, the six MCP fixes in CLAUDE.md §1) for token handling rather than inventing a new pattern. Left as a documented extension point only.

## B. `file-trigger` node (read-side, visual-only)

**This is not a mid-graph execution step — no changes to the execution engine are needed.** It is a canvas-visible **configuration surface**, exactly like `note`: draggable, has a property panel, its config is saved as ordinary node data in the workflow JSON — but its executor is a no-op that `graph_builder` skips, the same way `note` is skipped today.

`src/engine/workflow.py`:

```python
class FileTriggerNodeData(BaseNodeData):
    provider: Literal["local"] = Field(default="local")   # widens as providers are added
    source_path: str | None = Field(default=None, alias="sourcePath")
    dest_path: str | None = Field(default=None, alias="destPath")
    error_path: str | None = Field(default=None, alias="errorPath")
    target_input_variable: str | None = Field(default=None, alias="targetInputVariable")
    poll_interval_seconds: int = Field(default=30, alias="pollIntervalSeconds")

class FileTriggerNode(BaseModel):
    id: str
    type: Literal["file-trigger"]
    position: Position
    data: FileTriggerNodeData
```

Registered in `graph_builder.py`'s skip-list alongside `note`. Frontend: new `node-panels/file-trigger.tsx` (Source path / Destination path / Error path / Target input variable name / Poll interval, provider picker defaulting to and currently limited to "Local"), wired into `COMPOSER_NODE_PALETTE`, `COMPOSER_NODE_TYPES` (→ `InnerNode`, the standard single-in/single-out chip — this is the exact class of registry the Jira-node review caught missing), `node-visuals.ts`, `property-panel.tsx`.

### The `composer watch` CLI

New `src/cli/watch.py`, wired into `src/cli/main.py`'s dispatcher (`composer watch --workflow-id <id> [--api-key ...]`):

1. Fetches the workflow via `GET /workflows/{id}` (needs the workflow to be readable by whatever credential the CLI is configured with — a normal JWT login for the operator setting this up, distinct from the `ck_` key used for the actual per-file trigger calls).
2. Finds the `file-trigger` node in `workflow.nodes`, reads its config.
3. Instantiates the matching `FileStorageProvider` (`local` for now).
4. Loop, every `poll_interval_seconds`:
   - `list_new_files(source_path)` (stability-checked, per above).
   - For each newly-stable file: `read_file(ref)` → extract plain text **locally** (reusing the same `pypdf`/`python-docx`/plain-text logic `/uploads/extract-text` already uses — as a shared helper function, not a network round-trip to that endpoint, since the CLI already has the bytes locally and needs to produce plain JSON, not a multipart upload).
   - On successful extraction: call `POST /api/run/{externalSlug}` (the **existing**, unmodified external-invoke endpoint) with `Authorization: Bearer ck_...` and body `{"input": {target_input_variable: extracted_text}}`. This requires the workflow to be published (`isProduction=true`, has an `externalSlug`) — the natural fit, since this is precisely what that mechanism already exists for (automated, non-interactive triggering).
   - On trigger-call success (regardless of what the workflow itself later does): `move_file(ref, dest_path)`.
   - On extraction failure or trigger-call failure: `move_file(ref, error_path)`. One file's failure never blocks the loop from processing the next file (per-file try/except, logged).
5. Runs until interrupted (Ctrl+C) — a long-lived local process on the operator's machine for the local-filesystem case; for a future cloud-storage provider, the same module could instead run as a scheduled Cloud Run job (poll-once-and-exit mode) rather than a long-lived loop — noted as a future run-mode flag, not built now.

## C. `file-write` node (write-side)

A genuine graph node — sits mid-flow like Email/Jira/HTTP, palette-draggable, standard single-in/single-out (`InnerNode`).

`src/engine/workflow.py`:

```python
class FileWriteNodeData(BaseNodeData):
    provider: Literal["local"] = Field(default="local")
    destination_path: str | None = Field(default=None, alias="destinationPath")
    filename: str | None = None                                    # WITHOUT extension
    format: Literal["md", "docx", "pdf"] = Field(default="md")
    content: str | None = None

class FileWriteNode(BaseModel):
    id: str
    type: Literal["file-write"]
    position: Position
    data: FileWriteNodeData
```

`destination_path`, `filename`, and `content` all support `{{variable}}` substitution (e.g. `content={{draft_brd}}`, `filename={{project_name}}-BRD`). The file extension is **always derived from `format`**, never typed by the designer — avoids a mismatched-extension footgun (e.g. typing `report.pdf` while `format="docx"`).

### `src/executors/file_write.py` — `FileWriteExecutor`

1. Substitute `destination_path`, `filename`, `content`.
2. Convert `content` (assumed Markdown — matches how BRD-drafting and similar agent prompts are designed to produce output) according to `format`:
   - `format="md"` — write the substituted text as UTF-8 bytes directly. No conversion.
   - `format="docx"` — parse Markdown via **`markdown-it-py`** (new dependency; CommonMark-compliant, actively maintained), walk the resulting token stream, and render into a `.docx` via **`python-docx`** (already a dependency) — mapping headings → `add_heading`, paragraphs → `add_paragraph`, bullet/numbered lists → list-styled paragraphs, pipe-tables → `add_table`, bold/italic → run formatting. A small, purpose-built renderer (new module `src/conversion/markdown_to_docx.py`), not a full-fidelity general-purpose converter — sufficient for the structured, table-heavy BRD-style output this is built for.
   - `format="pdf"` — Markdown → HTML (`markdown-it-py` renders HTML directly) → PDF via **`xhtml2pdf`** (new dependency; pure-Python, no system packages/binaries — keeps the existing multi-stage Docker image lean, per its stated goal of avoiding bloat, and was the explicit trade-off decision over a `pandoc`-based approach).
3. `provider.write_file(destination_path, f"{filename}.{ext}", converted_bytes)`.
4. Returns `{variables: {lastOutput: <the full destination path written>}, node_results: {...}}` — same shape convention as every other executor, so `{{write_brd.output}}` (or `{{lastOutput}}`) downstream can reference exactly where the file landed (e.g. to mention it in a follow-up notification email).

**New dependencies to add** (flagged per CLAUDE.md's "ask before adding a dependency not in the design doc" — now *in* the design doc, so this is the confirmation): `markdown-it-py`, `xhtml2pdf`. `python-docx` and `pypdf` are already present.

Frontend: new `node-panels/file-write.tsx` (Destination path / Filename (no extension) / Format picker (MD/Word/PDF) / Content — likely a `PromptField`-style textarea supporting `{{variable}}` insertion, matching the Jira/Email panels' pattern), wired into the same five registries as `file-trigger`.

## Non-goals (explicitly out of scope for this pass)

- S3/Google Drive/OneDrive concrete implementations — interface + local implementation only; others are documented extension points.
- A shared/registered "storage connection" entity — deliberately rejected in favor of inline-per-node config, consistent with the Jira node and every other credentialed node.
- General-purpose, pixel-perfect Markdown→DOCX/PDF fidelity (footnotes, nested blockquotes, embedded images, etc.) — the renderer targets the structured, table-heavy document shape this was built for (BRDs and similar), not arbitrary Markdown.
- A push-based (webhook/event-notification) trigger model for the local-filesystem provider — polling only, for now; push-based models (S3 event notifications, Drive push channels) are noted as a future provider-specific optimization, not required by the interface.
