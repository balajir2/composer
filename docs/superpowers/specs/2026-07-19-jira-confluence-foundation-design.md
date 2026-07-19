# Design: Jira Extraction + Confluence Node Foundation

**Date:** 2026-07-19
**Status:** Approved (design phase) — implementation plan not yet written
**Origin:** Macy's "Jira-Driven Weekly Tracking & Executive Project Reporting" BRD
(`D:\Factspan\FlowComposer\Macys_JIRA_Weekly_Tracking_and_Executive_Reporting_BRD.pdf`) and its
two master prompts (`D:\Factspan\FlowComposer\Master Prompts.pdf`). This is **not** OAB-parity
work and does not touch `D:/GitHub/open-agent-builder` — it is new platform capability built on
top of the already-complete Composer engine (Phases 0–10), in service of a real customer
workflow request.

## Why this spec exists

The BRD asks for two flows (Feature 1: Jira → Confluence weekly report; Feature 2: Jira →
executive HTML/PDF report). Analysis of the BRD against Composer's current 22 node types
surfaced four platform gaps rather than pure workflow-authoring problems:

1. The `jira` node is agentic (LLM picks from 6 narrow tools) and its search tool returns a lossy
   text digest capped at `maxResults`, with no pagination, story points, epic, sprint, worklogs,
   comments, changelog, or blockers. Unsuitable for the BRD's deterministic, reconciled,
   audit-traceable extraction of up to 5,000 issues (BR-01–BR-12, §12.3).
2. No Confluence node/executor/provider exists anywhere in the codebase.
3. No mechanism persists state across scheduled runs, but the BRD needs week-over-week baseline
   comparison (BR-01, BR-04, BR-05, FR-018).
4. `file-write` only supports Markdown-derived `md`/`docx`/`pdf`, not raw HTML — deferred to a
   later sub-project, out of scope here.

This spec covers gaps 1–3 only, decomposed and prioritized per the user's explicit direction:
**foundation first** (extraction + baseline), because both Feature 1 and Feature 2 depend on it
and it is the riskiest technical gap. The Confluence node is pulled in here too because the
chosen baseline mechanism (see below) depends on it.

Out of scope for this spec, deliberately deferred to their own design cycles: the `file-write`
`html` format, and the actual Feature 1 / Feature 2 workflow graphs (which consume these
capabilities but are workflow configuration, not platform code).

## Decisions locked by user Q&A during brainstorming

- Target environment: **local dev only** for now (no live deployment to point at).
- Build order: **foundation (Jira extraction + baseline) before Confluence-node-only or a
  combined mega-spec.**
- Jira extraction shape: **add an `operation` mode to the existing `jira` node** (`agent` |
  `extract`), mirroring the `vector-db` node's existing `operation: query | upsert` precedent —
  not a new node type, not a hand-authored `http`+`while` recipe.
- Confluence node: **new node type, fully deterministic (operation selector, no LLM)** — not
  agentic like `jira`'s default mode, because Feature 1's publish behavior is mechanical and
  rule-driven and the BRD explicitly treats narrative hallucination as a named risk to avoid.
- Cross-run baseline: **store it inside Confluence itself**, via native content properties on
  the page — not a new Postgres table, not scraping rendered page HTML, not a filesystem write.

## Component 1 — `jira` node: `operation: "extract"`

New field on `JiraNodeData`: `operation: Literal["agent", "extract"] = "agent"` — default
preserves current behavior for every existing saved workflow.

When `operation == "extract"`, the node runs with **no LLM call at all**. New fields, only
relevant in this mode:

| Field | Purpose |
|---|---|
| `jql` | Required. The search query. |
| `fields` | List of Jira field IDs/names to request (e.g. `summary`, `status`, `assignee`, `priority`, `duedate`, `labels`, `components`, `issuetype`, `parent`, `resolutiondate`, plus whatever the caller's org-specific story-points field turns out to be). |
| `expandChangelog` | bool, default `true`. Passes `expand=changelog` so blocker-age and meaningful-update detection (BR-07, BR-08) have status-transition history to work from. |
| `maxIssues` | Cap on total issues fetched, default 1000, hard ceiling 5000 (matches the BRD's NFR of "up to 5,000 issues within 15 minutes"). |

Execution: loop `POST /rest/api/3/search` with `startAt` advancing by a fixed page size (e.g.
100) until Jira's reported `total` is satisfied or `maxIssues` is hit (whichever first) —
`truncated: true` is set in the latter case so downstream logic can flag "results incomplete"
rather than silently under-counting. Transient 429/5xx responses retry with bounded exponential
backoff (small fixed attempt cap) before failing the node — no silent infinite retry.

Output shape (deliberately un-opinionated — the executor doesn't encode BRD business rules,
only fetches completely and deterministically):

```json
{
  "issues": [
    {"key": "MB-101", "fields": { /* raw Jira field object, whatever was requested */ }, "changelog": { /* raw, if expandChangelog */ }}
  ],
  "total": 342,
  "fetched": 342,
  "truncated": false
}
```

Downstream `transform`/`data-transform` nodes are responsible for canonical-schema mapping,
metric calculation, and diligence scoring (per the earlier user decision: deterministic math,
LLM only for narrative prose) — that logic belongs to the Feature 1/2 workflow specs, not to
this executor.

**Story Points field, confirmed:** despite `MB` being a Jira Core / Work Management project
(where a points field is not a given), the Factspan Jira instance does have one:
`customfield_10026` ("Story Points", type `float`), confirmed via
`GET /rest/api/3/search?jql=project=MB&fields=*all` against the real instance. Feature 1/2's
"planned vs. completed points" sections (BR-01–BR-03) can use it directly. The `fields` default
list on the `extract` operation stays a designer-editable field (not hardcoded to this one
instance's field ID) so other projects/customers can supply their own field ID, but for `MB`
specifically the default list should include `customfield_10026`.

## Component 2 — new `confluence` node

Credentials: `domain`, `email`, `apiToken` — identical encrypted-at-rest pattern to the `jira`
node (AES-256-GCM, redacted to `••••••••` on every API response, decrypted only in-memory at
execution time). Reuses the existing encryption helper rather than a parallel implementation.

`operation: Literal["create_or_update_page", "get_page", "get_property", "set_property"]`

| Operation | Fields | Behavior |
|---|---|---|
| `create_or_update_page` | `spaceKey`, `parentPageId`, `title`, `bodyStorageHtml`, `labels[]` | Looks up an existing page by `spaceKey` + exact `title` first (Confluence CQL search). If found: `PUT` with the version number incremented by 1 (idempotent reruns — same input/run-week updates the same page rather than creating a duplicate, per FR-017). If not found: `POST` to create as a child of `parentPageId`. Reconciles labels to exactly the requested set. Returns `{pageId, version, url, created: bool}`. |
| `get_page` | `spaceKey`, `title` | Returns `{pageId, bodyStorageHtml, version, found: bool}`. `found: false` (not an error) when no match — the calling workflow decides what "no prior page" means (e.g. first-ever run). |
| `get_property` | `pageId`, `propertyKey` | Confluence v2 content-properties API (`GET /wiki/api/v2/pages/{id}/properties`). Returns `{value, found: bool}`. |
| `set_property` | `pageId`, `propertyKey`, `value` | `PUT`/`POST` to the same content-properties API. `value` is arbitrary JSON, substituted from workflow state like any other node field. |

Content properties are invisible in the rendered page — they don't clutter the human-readable
report and don't require parsing rendered HTML back into data, which is the reason this
mechanism was chosen for baseline storage (see Component 3) over scraping page bodies.

## Component 3 — baseline pattern (no new platform code)

This is a *usage pattern* for Components 1+2 together, not new executor code. Recorded here so
the eventual Feature 1/2 workflow spec doesn't have to re-derive it:

1. Compute last week's expected page title deterministically (`transform` node: same naming
   convention as this week's page, `YYYY-MM-DD` shifted back one reporting period).
2. `confluence.get_page` for that title. `found: false` → treat baseline as empty (BR-03's
   "Not Available" path when the comparison denominator doesn't exist yet — e.g. very first run).
3. If found: `confluence.get_property(pageId, "metrics_snapshot")` to retrieve last week's
   canonical metrics JSON (whatever shape the Feature 1/2 metrics-computation step defines).
4. After this week's metrics are computed and this week's page is created/updated:
   `confluence.set_property(thisWeeksPageId, "metrics_snapshot", <this week's computed JSON>)` —
   so next week's run finds it without any additional bookkeeping.

This keeps the whole baseline mechanism self-contained inside Feature 1's own Confluence
footprint: auditable (sits on the same page as the human-readable report, inside Confluence's
own page-history), and requires no new database table or filesystem dependency.

## Testing

- **Jira `extract` operation**: unit tests with mocked `httpx` responses — multi-page pagination
  advances `startAt` correctly; `maxIssues` cap truncates and sets `truncated: true`;
  `expandChangelog` is passed through as `expand=changelog`; a 429/5xx response triggers bounded
  retry before eventually failing; `operation: "agent"` (omitted/default) is unaffected —
  existing `test_jira_executor.py` cases must still pass unmodified.
- **`confluence` node**: unit tests per operation — `create_or_update_page` creates when no
  matching title exists, updates with incremented version when one does; `get_page` returns
  `found: false` on no match rather than raising; `get_property`/`set_property` round-trip
  arbitrary JSON; credential encryption/redaction tests mirroring the existing Jira
  credential-encryption test pattern (`tests/unit/api/test_workflows_jira_tokens.py`).
- **Real end-to-end verification**: happens against the user's actual Factspan Jira/Confluence
  once credentials are entered locally (in their own `.env` or through the Designer UI once
  it's running) — never shared with or read by the implementing agent.

## Explicitly deferred to later sub-projects

- `file-write` gaining a raw `html` output format (needed for Feature 2, not this spec).
- The actual Feature 1 workflow graph (Jira extract → metrics/diligence transform → narrative
  agent → Confluence publish → baseline write-back).
- The actual Feature 2 workflow graph (Jira extract → metrics/diligence transform → executive
  narrative agent → HTML file-write).
- Resolving the story-points field question for project `MB` (pending user's own field-list
  lookup against their Jira instance).
