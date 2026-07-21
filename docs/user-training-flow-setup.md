# User Training: Setting Up Example Flows

A parameter-by-parameter walkthrough of the 20 reference templates seeded by `scripts/seed_templates.py`. Use this doc when you've already skimmed [getting-started.md](getting-started.md) and want to know exactly what to fill in on a specific template rather than build a workflow from a blank canvas.

For what each node type does in general (every field, not just the ones a given template happens to use), see [designer-guide.md](designer-guide.md)'s [Node reference](designer-guide.md#node-reference) — this doc assumes that one as background and only calls out the fields that matter for getting each specific example running.

**How to use a template:** open [/designer/templates](http://localhost:3000/designer/templates), click "Use template" on the one you want — this clones it into a private, editable copy under your account — then follow that template's section below.

**Conventions in this doc:**
- **Start inputs** — the form fields you (or whoever runs the workflow) fill in every time it runs. These are always yours to set; no cloning step required beyond picking sensible values.
- **One-time node configuration** — fields baked into a node that you set once after cloning, before the workflow will run at all (API keys, connection details, domains). Templates ship these blank or with placeholder values deliberately — a template can't bake in your credentials.
- **Required external services** — what has to be configured in your Composer deployment (via Admin → LLM keys, or your own accounts) before the template will run end to end.

---

## Quick reference

| # | Template | Difficulty | Needs deployment-level API keys? | Needs one-time node config? |
|---|---|---|---|---|
| 1 | [Simple Agent](#1-simple-agent) | Beginner | LLM only | No |
| 2 | [Web Research Agent](#2-web-research-agent) | Beginner | LLM + Tavily | No |
| 3 | [Scrape and Summarise](#3-scrape-and-summarise) | Intermediate | LLM + Firecrawl | No |
| 4 | [Guardrails + Branching](#4-guardrails--branching) | Intermediate | LLM only | No |
| 5 | [Multi-Company Stock Analysis](#5-multi-company-stock-analysis) | Advanced | LLM only | No |
| 6 | [Yahoo Finance Stock Report](#6-yahoo-finance-stock-report) | Intermediate | LLM only | No |
| 7 | [Amazon Product Research](#7-amazon-product-research) | Advanced | LLM + Firecrawl | No |
| 8 | [Human-in-the-Loop Approval](#8-human-in-the-loop-approval) | Intermediate | LLM only | No |
| 9 | [Zillow Property Finder](#9-zillow-property-finder) | Advanced | LLM + Firecrawl | No |
| 10 | [While-loop with Accumulator](#10-while-loop-with-accumulator) | Advanced | LLM only | No |
| 11 | [RAG — Vector DB + Join Chunks](#11-rag--vector-db--join-chunks) | Advanced | LLM + OpenAI (embeddings) | **Yes** — vector DB connection |
| 12 | [Document Ingestion (Vector DB Upsert)](#12-document-ingestion-vector-db-upsert) | Advanced | LLM + OpenAI (embeddings) | **Yes** — vector DB connection |
| 13 | [Meeting Transcript to Action Items](#13-meeting-transcript-to-action-items) | Intermediate | LLM only | No |
| 14 | [Customer Support Triage](#14-customer-support-triage) | Intermediate | LLM only | No |
| 15 | [Gamma AI Presentation Generator](#15-gamma-ai-presentation-generator) | Advanced | LLM + Tavily + Gamma | No |
| 16 | [Code Review Assistant](#16-code-review-assistant) | Advanced | LLM only | No |
| 17 | [Lead Enrichment](#17-lead-enrichment) | Advanced | LLM + Tavily + Firecrawl | No |
| 18 | [Approved Email Distribution](#18-approved-email-distribution) | Intermediate | LLM + Resend | No (sender domain must be verified in Resend) |
| 19 | [Arcade Tool Call](#19-arcade-tool-call) | Intermediate | LLM + Arcade | No (but requires one-time OAuth authorization on first run) |
| 20 | [File Watch to Summarize and Email](#20-file-watch-to-summarize-and-email) | Intermediate | LLM + Resend | **Yes** — `composer watch` CLI setup |

Deployment-level API keys (Anthropic/OpenAI/Google/Groq, Tavily, Firecrawl, Gamma, Resend, Arcade, OpenAI-for-embeddings) are set once by an admin under **Admin → LLM keys** — see [admin-guide.md](admin-guide.md). They apply to every workflow; individual templates don't carry their own copies. The two exceptions are **Jira** and **Confluence** node credentials, which are entered per-node (see designer-guide.md's [`jira`](designer-guide.md#jira)/[`confluence`](designer-guide.md#confluence) sections) — no example template currently uses either node, but if you add one to a cloned template, expect to fill in domain/email/API token yourself.

---

## 1. Simple Agent

The most basic workflow: `start → agent → end`. Good first template — one node to look at, nothing to configure beyond the question you ask.

**Nodes:** `start` → `agent` → `end`

**Start inputs:**

| Name | Type | Required | Default |
|---|---|---|---|
| `question` | text | Yes | "What are the key benefits of using AI agents in workflow automation?" |

**One-time node configuration:** none. Clone it and click Run Draft.

---

## 2. Web Research Agent

An agent with web search via Composer's built-in Tavily tool provider.

**Nodes:** `start` → `agent` (tool: `tavily.tavily_search`) → `end`

**Start inputs:**

| Name | Type | Required | Default |
|---|---|---|---|
| `search_query` | text | Yes | "latest developments in agentic AI in 2026" |

**Required external services:** `TAVILY_API_KEY` must be set in your deployment (Admin → LLM keys). Without it the agent's `tavily_search` tool call fails.

**One-time node configuration:** none — the tool is already selected on the agent node (`selectedTools: ["tavily.tavily_search"]`).

---

## 3. Scrape and Summarise

Two chained agents: one scrapes a URL with Firecrawl, the second turns the raw markdown into a structured executive summary. Demonstrates handing output from one agent to the next via `{{lastOutput}}`.

**Nodes:** `start` → `agent` (Scrape Website, tool: `firecrawl.firecrawl_scrape`) → `agent` (Summarise Content) → `end`

**Start inputs:**

| Name | Type | Required | Default |
|---|---|---|---|
| `url` | text | Yes | `https://www.anthropic.com/news` |

**Required external services:** `FIRECRAWL_API_KEY`.

**One-time node configuration:** none.

---

## 4. Guardrails + Branching

An agent answers a question; a `guardrails` node screens the answer for PII/moderation/jailbreak issues; an `if-else` routes to either a "deliver" agent or a "flagged" fallback agent based on the guardrails verdict. Reference for the conditional-safety pattern.

**Nodes:** `start` → `agent` (Answer Agent) → `guardrails` (Check Guard) → `if-else` (Route by Verdict) → `agent` (Deliver Answer) **or** `agent` (Flagged Response) → `end`

**Start inputs:**

| Name | Type | Required | Default |
|---|---|---|---|
| `question` | text | Yes | "Summarise the safety policy in plain English." |

**One-time node configuration:** none. Worth opening the `guardrails` node to see its toggles (`piiEnabled`/`moderationEnabled`/`jailbreakEnabled` all on, `hallucinationEnabled` off, `actionOnViolation: warn`) and the `if-else` node's condition (`{{check_guard.passed}}`) — these are the two fields you'd change to build your own guardrailed flow.

---

## 5. Multi-Company Stock Analysis

Three explicit `http` calls to Yahoo Finance's public chart endpoint (AAPL, MSFT, GOOG), then an agent synthesises a comparative report. No API key required — Yahoo's chart endpoint is public.

**Nodes:** `start` → `http` (Fetch AAPL) → `http` (Fetch MSFT) → `http` (Fetch GOOG) → `agent` (Comparative Analyst) → `end`

**Start inputs:**

| Name | Type | Required | Default |
|---|---|---|---|
| `report_focus` | text | No | "5-day price momentum and which stock looks strongest" |

**One-time node configuration:** the three tickers are hardcoded in each `http` node's `httpUrl` field (`.../chart/AAPL?...`, `.../MSFT?...`, `.../GOOG?...`). To analyze different companies, open each `http` node and swap the ticker symbol in the URL — there's no separate ticker input field on this template.

---

## 6. Yahoo Finance Stock Report

Single-ticker deep dive: `http` fetches the raw chart JSON, `extract` pulls specific fields out with a JSON schema, `agent` writes the narrative. Reference for the HTTP → Extract → Agent pattern — a good template to study before building your own "fetch messy API, extract clean fields" flow.

**Nodes:** `start` → `http` (Yahoo Finance) → `extract` (Extract Key Metrics) → `agent` (Stock Analyst) → `end`

**Start inputs:**

| Name | Type | Required | Default |
|---|---|---|---|
| `ticker` | text | Yes | "NVDA" |

**One-time node configuration:** none — `ticker` flows into the `http` node's URL via `{{ticker}}` substitution, unlike Template 5's hardcoded version.

---

## 7. Amazon Product Research

Firecrawl scrapes an Amazon search-results page, `extract` pulls structured product records (title/brand/price/rating/review count/url) out of the scraped markdown, an agent picks the top 3.

**Nodes:** `start` → `agent` (Scrape Search Results, tool: `firecrawl.firecrawl_scrape`) → `extract` (Extract Products) → `agent` (Recommendation Agent) → `end`

**Start inputs:**

| Name | Type | Required | Default |
|---|---|---|---|
| `search_query` | text | Yes | "noise cancelling headphones under 200" |

**Required external services:** `FIRECRAWL_API_KEY`.

**One-time node configuration:** none.

---

## 8. Human-in-the-Loop Approval

An agent drafts content; execution pauses at a `user-approval` gate; on approve, a finalisation agent polishes it; on reject, a revision agent rewrites using the reviewer's note. Reference for the pause/resume approval pattern.

**Nodes:** `start` → `agent` (Draft Writer) → `user-approval` (Reviewer Gate) → `agent` (Final Polish) **[approved]** or `agent` (Revise on Rejection) **[rejected]** → `end`

**Start inputs:**

| Name | Type | Required | Default |
|---|---|---|---|
| `topic` | text | Yes | "the impact of agentic AI on knowledge work in 2026" |
| `format` | text | No | "LinkedIn post (200 words, professional tone)" |

**One-time node configuration:** none required to run it, but two optional fields on the `user-approval` node are worth knowing about if you want the reviewer to be notified by email rather than watching the Runs page: **Approver email** and **Approver CC** (see [designer-guide.md](designer-guide.md#user-approval)). Left blank, the workflow still pauses correctly — you just have to go check the Runs page yourself to approve/reject.

**How to run it:** after submitting the form, the execution pauses at `waiting_approval`. Go to **Runs → History**, open the execution, and click Approve or Reject (with an optional note) to let it continue.

---

## 9. Zillow Property Finder

Firecrawl scrapes a Zillow search URL, `extract` pulls structured listings, `data-transform` filters by your price/bed criteria, an agent picks favorites. Demonstrates the `filter` operation on a structured collection.

**Nodes:** `start` → `agent` (Scrape Zillow, tool: `firecrawl.firecrawl_scrape`) → `extract` (Extract Listings) → `data-transform` (Filter Listings) → `agent` (Pick Favorites) → `end`

**Start inputs:**

| Name | Type | Required | Default |
|---|---|---|---|
| `zillow_url` | text | Yes | `https://www.zillow.com/homes/Austin,-TX_rb/` |
| `max_price` | number | No | 750000 |
| `min_beds` | number | No | 3 |

**Required external services:** `FIRECRAWL_API_KEY`.

**One-time node configuration:** none — `max_price`/`min_beds` flow straight into the `data-transform` node's filter expression (`item["price"] <= max_price and item["beds"] >= min_beds`).

---

## 10. While-loop with Accumulator

A real iterating `while` loop over a hardcoded ticker list (AAPL/MSFT/GOOG), fetching each from Yahoo Finance and accumulating results into a list, then a closing agent writes the comparative report. The canonical reference for building loops with a running accumulator.

**Nodes:** `start` → `set-state` ×3 (init `tickers`/`index`/`results`) → `while` (loop) → body: `transform` (Pick ticker) → `http` (Fetch chart) → `transform` (Append result, `outputKey: results`) → `transform` (Increment index, `outputKey: index`) → loops back → exit: `agent` (Comparative Report) → `end`

**Start inputs:**

| Name | Type | Required | Default |
|---|---|---|---|
| `report_focus` | text | No | "5-day price momentum and which stock looks strongest" |

**One-time node configuration:** the ticker list itself is set on the **Init: tickers** `set-state` node's `stateValue` field (`["AAPL", "MSFT", "GOOG"]`) — edit that array to analyze a different set of companies. The loop's **Max iterations** cap (on the `while` node, default 10) should stay at or above your ticker count.

---

## 11. RAG — Vector DB + Join Chunks

Retrieval-augmented Q&A: a `vector-db` node retrieves the top-k relevant chunks for a question, `join-chunks` stitches them into one context block, an agent answers grounded in those chunks only.

**Nodes:** `start` → `vector-db` (Retrieve Chunks, query mode) → `transform` (Extract Chunks, `outputKey: chunks`) → `join-chunks` (Stitch Context) → `agent` (RAG Answer) → `end`

**Start inputs:**

| Name | Type | Required | Default |
|---|---|---|---|
| `question` | text | Yes | "What are the key principles of agentic AI design?" |

**Required external services:** `OPENAI_API_KEY` (used for embeddings even if your chat model is Anthropic/Google/Groq — the template's embedding provider is fixed to OpenAI).

**One-time node configuration — required before this template will run at all.** Open the `vector-db` node ("Retrieve Chunks") and fill in:

| Field | What to set |
|---|---|
| `vectorDbProvider` | Which DB you're using — `pinecone` (the template's default), `qdrant`, `chroma`, `weaviate`, or `milvus` |
| `vectorDbEndpoint` | Your DB's connection endpoint |
| `vectorDbApiKey` | Your DB's API key |
| `vectorDbCollection` | The collection/index/namespace to query |

Everything else is pre-set and safe to leave alone: `vectorDbQueryPrompt: {{question}}`, `vectorDbTopK: 5`, `vectorDbEmbeddingProvider: openai`, `vectorDbEmbeddingModel: text-embedding-3-small`, `vectorDbDimension: 1536`. This template pairs with Template 12 — ingest documents with that one into the same collection, then query them here. Both must use the same embedding model/dimension (already true out of the box) or retrieval silently returns nothing useful.

---

## 12. Document Ingestion (Vector DB Upsert)

Upload a document; Composer extracts the text, chunks it, embeds each chunk via OpenAI, and inserts into your vector DB. The ingestion companion to Template 11.

**Nodes:** `start` (with a `document` file-upload input) → `vector-db` (Ingest to Vector DB, upsert mode) → `agent` (Ingestion Summary) → `end`

**Start inputs:**

| Name | Type | Required | Default |
|---|---|---|---|
| `document` | document (file upload) | Yes | — |
| `source_label` | text | No | "uploaded-doc" — a tag stored in each chunk's metadata for later filtering |

**Required external services:** `OPENAI_API_KEY` (embeddings).

**One-time node configuration — required.** Same four fields as Template 11, on the `vector-db` node ("Ingest to Vector DB"): `vectorDbProvider`, `vectorDbEndpoint`, `vectorDbApiKey`, `vectorDbCollection`. Use the **same collection name** as your Template 11 clone if you want the two to work together. Chunking defaults (`vectorDbChunkSize: 1000`, `vectorDbChunkOverlap: 100`) and embedding settings match Template 11's out of the box — leave them unless you have a reason to change them.

**Note:** re-running ingestion with the same document is safe — chunk IDs are derived from a hash of the chunk text, so re-running overwrites the same vectors rather than duplicating them.

---

## 13. Meeting Transcript to Action Items

Upload a meeting transcript; the first agent extracts action items as structured JSON (owner/task/due date/priority), the second drafts a follow-up email referencing them.

**Nodes:** `start` (with a `document` upload) → `agent` (Extract Action Items, JSON mode) → `agent` (Draft Follow-up Email) → `end`

**Start inputs:**

| Name | Type | Required | Default |
|---|---|---|---|
| `transcript` | document (file upload) | Yes | — |
| `meeting_date` | text | No | "today" |
| `team_name` | text | No | "the team" |

**One-time node configuration:** none.

---

## 14. Customer Support Triage

Classify an incoming support ticket as urgent or standard, then a specialist agent drafts the appropriate response. Pure LLM — no external services needed at all, good for testing a fresh deployment with zero third-party keys configured.

**Nodes:** `start` → `agent` (Triage Classifier, JSON mode) → `if-else` (Urgent?) → `agent` (Urgent Specialist) **or** `agent` (Standard Reply) → `end`

**Start inputs:**

| Name | Type | Required | Default |
|---|---|---|---|
| `ticket` | text | Yes | A sample urgent-sounding support message |
| `customer_name` | text | No | "there" |

**One-time node configuration:** none.

---

## 15. Gamma AI Presentation Generator

Type in a topic, get back a Gamma-hosted slide deck. A research agent grounds the outline in current information via Tavily; the `gamma-ai` node renders slides from that outline.

**Nodes:** `start` → `agent` (Research + Outline, tool: `tavily.tavily_search`) → `gamma-ai` (Generate Deck) → `agent` (Summarise Output) → `end`

**Start inputs:**

| Name | Type | Required | Default |
|---|---|---|---|
| `topic` | text | Yes | "How agentic AI changes knowledge work in 2026" |
| `audience` | text | No | "executives at a mid-size tech company" |
| `num_slides` | number | No | 8 |

**Required external services:** `GAMMA_API_KEY` and `TAVILY_API_KEY`.

**One-time node configuration:** none — `gamma-ai` node fields (`format: presentation`, `textMode: preserve`, `textAmount: medium`, `imageSource: aiGenerated`, `exportAs: web`) are pre-set sensibly. Change `exportAs` to `pptx` or `pdf` on your clone if you want a downloadable file instead of just a hosted URL.

**Note:** Gamma generation itself takes 60-90 seconds — this template's estimated run time reflects that, it's not a stall.

---

## 16. Code Review Assistant

Paste a diff, get a structured, severity-tagged review. A `guardrails` layer screens the review's own output for PII/credential leaks (e.g. the diff itself contained a secret the review agent quoted back) before delivery.

**Nodes:** `start` → `agent` (Code Reviewer, JSON mode) → `guardrails` (PII / Secret Screen) → `if-else` (Safe to deliver?) → `agent` (Format Review) **or** `agent` (Redacted Warning) → `end`

**Start inputs:**

| Name | Type | Required | Default |
|---|---|---|---|
| `diff` | text | Yes | A sample unified diff with an intentional security bug |
| `language` | text | No | "Python" |

**One-time node configuration:** none.

---

## 17. Lead Enrichment

Company name in, structured CRM-ready profile out. Multi-source research (Tavily for news, Firecrawl for the company's own site) feeds a structured-extraction agent.

**Nodes:** `start` → `agent` (Multi-source Researcher, tools: `tavily.tavily_search` + `firecrawl.firecrawl_scrape`) → `agent` (Shape into CRM Record, JSON mode) → `end`

**Start inputs:**

| Name | Type | Required | Default |
|---|---|---|---|
| `company_name` | text | Yes | "Anthropic" |
| `company_url` | text | No | `https://www.anthropic.com` — leave blank to let the researcher infer it |

**Required external services:** `TAVILY_API_KEY` and `FIRECRAWL_API_KEY`.

**One-time node configuration:** none.

---

## 18. Approved Email Distribution

Draft a report with an agent, require human approval, then send it by email through Resend. The canonical safe pattern for any workflow that ends in a real side effect (sending mail) — draft → human review → deterministic send, never LLM-triggered send.

**Nodes:** `start` → `agent` (Draft Report) → `user-approval` (Approve Send) → `email` (Send Email) **[approved]**, or straight to `end` **[rejected]**

**Start inputs:**

| Name | Type | Required | Default |
|---|---|---|---|
| `recipient_email` | text | Yes | `recipient@example.com` |
| `from_email` | text | Yes | `Composer <reports@example.com>` |
| `topic` | text | Yes | "weekly product adoption highlights" |
| `source_notes` | text | No | Sample bullet notes for the agent to write from |

**Required external services:** a Resend account with `RESEND_API_KEY` configured, **and** the domain in `from_email` must be verified in that Resend account. This is the one template where a default Start-input value (`from_email`) will not work as-is in a real run — you must replace `reports@example.com` with an address on a domain you've verified in Resend, every time you run it (or change the default value on your cloned copy so you don't have to retype it).

**One-time node configuration:** none on the nodes themselves — the `email` node's `emailFrom`/`emailTo` are wired to the Start inputs already (`{{from_email}}`/`{{recipient_email}}`).

---

## 19. Arcade Tool Call

Draft an outline with an agent, then hand it to Arcade.dev's `Google.CreateDocument` tool via the `arcade` node. Reference for the arcade node's authorize/execute protocol — the same pause/resume mechanism as the `user-approval` gate, but pausing for OAuth instead of a human decision.

**Nodes:** `start` → `agent` (Draft Outline) → `arcade` (Create Google Doc) → `agent` (Summarise Result) → `end`

**Start inputs:**

| Name | Type | Required | Default |
|---|---|---|---|
| `topic` | text | Yes | "Q3 product roadmap outline" |
| `arcade_user_id` | text | No | "workflow-builder" — the Arcade end-user identity used to resolve which OAuth connection to use |

**Required external services:** `ARCADE_API_KEY`.

**One-time node configuration:** none on the node itself, but expect a one-time interactive step:

**How to run it:** on first run, the `arcade` node pauses the execution (status `waiting_approval`) with an authorization URL. Open that URL, complete Google OAuth for the requested scope in Arcade's UI, then go back to Composer (Run Draft panel, or Runs → History) and click Resume. Execution continues and actually creates the Google Doc. Subsequent runs under the same `arcade_user_id` won't need to re-authorize.

---

## 20. File Watch to Summarize and Email

Point the `composer watch` CLI at a local folder; when a file lands there, its text is extracted and sent into this workflow, which summarizes it and emails the summary. Reference for the `file-trigger` node (visual-only — it documents the watch configuration but doesn't execute) plus the separate `composer watch` CLI process that does the actual watching.

**Nodes:** `file-trigger` (Watch Folder, visual-only) → `start` → `agent` (Summarize File) → `email` (Email Summary) → `end`

**Start inputs:**

| Name | Type | Required | Default |
|---|---|---|---|
| `file_content` | text | Yes | Placeholder text — paste real content here to test without running the watcher |
| `recipient_email` | text | Yes | `recipient@example.com` |
| `from_email` | text | Yes | `Composer <reports@example.com>` |

**Required external services:** a Resend account with a verified sending domain (same caveat as Template 18 — replace the placeholder `from_email`).

**One-time node configuration — required only if you want the watcher itself to run** (you can otherwise just paste text into `file_content` and run it like any other template, no watcher needed). On the `file-trigger` node, set:

| Field | What it controls |
|---|---|
| `sourcePath` | Folder to watch for new files |
| `destPath` | Folder a file moves to after a successful trigger |
| `errorPath` | Folder a file moves to if extraction or the trigger call fails |
| `pollIntervalSeconds` | How often to poll `sourcePath` (default 30s) |

**How to run the live watcher:** first publish the workflow (Settings → Publish, pick a slug) and generate an API key (see [designer-guide.md's Publishing workflows](designer-guide.md#publishing-workflows)), then on a machine that can see the target folder, run:

```bash
composer watch --api-url <backend-url> --slug <your-slug> --api-key ck_... \
  --source <sourcePath> --dest <destPath> --error <errorPath> \
  --target-var file_content --interval 30
```

`composer watch` is a local process — it has to run on whatever machine has filesystem access to the watched folder, since a hosted backend has no visibility into your local disk. (For a folder you'd rather have Composer poll server-side with no local process, see the `file-trigger` node's Google Drive OAuth mode in [designer-guide.md](designer-guide.md#file-trigger) — a separate configuration path not used by this template.)

---

## Where to go next

- Build your own workflow from scratch: [getting-started.md](getting-started.md)
- Full field-by-field reference for every node type: [designer-guide.md](designer-guide.md#node-reference)
- Publishing a workflow so it's callable from outside Composer: [designer-guide.md's Publishing workflows](designer-guide.md#publishing-workflows)
- Setting up the deployment-level API keys referenced throughout this doc (Anthropic/OpenAI/Google/Groq/Tavily/Firecrawl/Gamma/Resend/Arcade): [admin-guide.md](admin-guide.md)
