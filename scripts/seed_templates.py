"""Seed the workflow catalogue with reference templates.

Idempotent — re-running upserts by `externalSlug` (used here as a stable
template identifier).  Each template is `isTemplate=true` + `isPublic=true`
+ `userId=null` so:

- They show up on the Designer's Templates gallery for every user.
- No one can edit them through the workflow CRUD endpoints (ownership
  check rejects null-owner edits).
- Designers click "Use template" and get a private working copy.

Adapted from OAB's `lib/workflow/templates/examples/*.ts` so users
coming from OAB recognise the same examples, but rewritten to
Composer's idioms:

  - Composer's transform executor uses Python (E2B) — OAB's JS transforms
    don't port directly, so we either rewrite or omit those steps.
  - if-else conditions are simpleeval expressions — `lastOutput.lower()`
    style — not JavaScript.
  - if-else edges use the `branch` field, not `sourceHandle: "if"/"else"`.
  - MCP servers are global admin-managed records; templates can't bake
    in user credentials, so research templates use Composer's built-in
    `tavily` and `firecrawl` tool providers via `selectedTools` instead.
  - Default model is Claude Haiku 4.5 (matches Composer's stack
    decision and is cheap+fast for demos).

Run:
    python -m scripts.seed_templates
"""

from __future__ import annotations

import asyncio
from typing import Any

from prisma import Json, Prisma  # pyright: ignore[reportMissingImports,reportAttributeAccessIssue]

_DEFAULT_MODEL = "anthropic/claude-haiku-4-5-20251001"


def _start_node(
    *,
    label: str,
    inputs: list[dict[str, Any]],
    pos_x: int = 100,
    pos_y: int = 200,
) -> dict[str, Any]:
    return {
        "id": "start-1",
        "type": "start",
        "position": {"x": pos_x, "y": pos_y},
        "data": {
            "label": label,
            "nodeName": label,
            "inputVariables": inputs,
        },
    }


def _end_node(*, pos_x: int, pos_y: int = 200) -> dict[str, Any]:
    return {
        "id": "end-1",
        "type": "end",
        "position": {"x": pos_x, "y": pos_y},
        "data": {"label": "End", "nodeName": "End"},
    }


# ─── Template 1 — Simple Agent ───────────────────────────────────────────


def _simple_agent() -> dict[str, Any]:
    """Most basic workflow: Start → Agent → End.  Single-turn Q&A."""
    return {
        "name": "Example 1: Simple Agent",
        "description": (
            "A basic workflow with one agent that answers questions. "
            "Best starting point for understanding the agent node."
        ),
        "category": "examples",
        "tags": ["example", "beginner", "basic"],
        "difficulty": "beginner",
        "estimatedTime": "1-2 minutes",
        "externalSlug": "template-01-simple-agent",
        "nodes": [
            _start_node(
                label="Start",
                inputs=[
                    {
                        "name": "question",
                        "type": "string",
                        "required": True,
                        "description": "Your question for the AI agent",
                        "defaultValue": "What are the key benefits of using AI agents in workflow automation?",
                    },
                ],
            ),
            {
                "id": "agent-1",
                "type": "agent",
                "position": {"x": 350, "y": 200},
                "data": {
                    "label": "Answer Question",
                    "nodeName": "Answer Question",
                    "instructions": (
                        "You are a helpful AI assistant. Provide a clear, "
                        "concise answer to the following question:\n\n"
                        "{{question}}\n\n"
                        "Structure the response so it's informative and easy "
                        "to understand."
                    ),
                    "model": _DEFAULT_MODEL,
                    "outputFormat": "Text",
                    "selectedTools": [],
                    "mcpServerIds": [],
                },
            },
            _end_node(pos_x=600),
        ],
        "edges": [
            {"id": "e1", "source": "start-1", "target": "agent-1"},
            {"id": "e2", "source": "agent-1", "target": "end-1"},
        ],
    }


# ─── Template 2 — Web Research Agent ─────────────────────────────────────


def _web_research_agent() -> dict[str, Any]:
    """Agent equipped with Tavily search.  Composer ships Tavily as a
    built-in tool provider so templates can reference it without the
    designer having to configure an MCP server."""
    return {
        "name": "Example 2: Web Research Agent",
        "description": (
            "An agent that searches the web using Tavily and synthesises "
            "the results into a research summary. Requires TAVILY_API_KEY "
            "to be set in your deployment."
        ),
        "category": "examples",
        "tags": ["example", "beginner", "tavily", "tools"],
        "difficulty": "beginner",
        "estimatedTime": "2-3 minutes",
        "externalSlug": "template-02-web-research-agent",
        "nodes": [
            _start_node(
                label="Start",
                inputs=[
                    {
                        "name": "search_query",
                        "type": "string",
                        "required": True,
                        "description": "What would you like to research on the web?",
                        "defaultValue": "latest developments in agentic AI in 2026",
                    },
                ],
            ),
            {
                "id": "agent-1",
                "type": "agent",
                "position": {"x": 350, "y": 200},
                "data": {
                    "label": "Web Research Agent",
                    "nodeName": "Web Research Agent",
                    "instructions": (
                        "You are a web research assistant with access to the "
                        "Tavily search tool.\n\n"
                        "1. Use tavily_search to look up: {{search_query}}\n"
                        "2. Review the top results and identify the most "
                        "relevant sources.\n"
                        "3. Synthesise the information into a clear, "
                        "well-organised summary with key findings, organised "
                        "by topic.\n\n"
                        "Cite sources inline as [domain] when you reference "
                        "specific facts."
                    ),
                    "model": _DEFAULT_MODEL,
                    "outputFormat": "Text",
                    "selectedTools": ["tavily.tavily_search"],
                    "mcpServerIds": [],
                },
            },
            _end_node(pos_x=600),
        ],
        "edges": [
            {"id": "e1", "source": "start-1", "target": "agent-1"},
            {"id": "e2", "source": "agent-1", "target": "end-1"},
        ],
    }


# ─── Template 3 — Scrape & Summarise ─────────────────────────────────────


def _scrape_and_summarise() -> dict[str, Any]:
    """Two-agent chain demonstrating handoff via {{lastOutput}}.
    Drops OAB's Arcade Google-Doc step since Arcade requires
    per-user OAuth that templates can't bake in."""
    return {
        "name": "Example 3: Scrape and Summarise",
        "description": (
            "Scrapes a URL with Firecrawl, then a second agent produces a "
            "structured executive summary. Demonstrates chaining agents "
            "via {{lastOutput}}. Requires FIRECRAWL_API_KEY."
        ),
        "category": "examples",
        "tags": ["example", "intermediate", "firecrawl", "chaining"],
        "difficulty": "intermediate",
        "estimatedTime": "3-5 minutes",
        "externalSlug": "template-03-scrape-and-summarise",
        "nodes": [
            _start_node(
                label="Start",
                inputs=[
                    {
                        "name": "url",
                        "type": "string",
                        "required": True,
                        "description": "URL to scrape and summarise",
                        "defaultValue": "https://www.anthropic.com/news",
                    },
                ],
            ),
            {
                "id": "agent-1",
                "type": "agent",
                "position": {"x": 350, "y": 200},
                "data": {
                    "label": "Scrape Website",
                    "nodeName": "Scrape Website",
                    "instructions": (
                        "Use the firecrawl_scrape tool to fetch the content "
                        "from this URL: {{url}}\n\n"
                        "Return the scraped markdown verbatim — the next "
                        "step will summarise it."
                    ),
                    "model": _DEFAULT_MODEL,
                    "outputFormat": "Text",
                    "selectedTools": ["firecrawl.firecrawl_scrape"],
                    "mcpServerIds": [],
                },
            },
            {
                "id": "agent-2",
                "type": "agent",
                "position": {"x": 600, "y": 200},
                "data": {
                    "label": "Summarise Content",
                    "nodeName": "Summarise Content",
                    "instructions": (
                        "Produce a professional summary of the following "
                        "scraped content:\n\n"
                        "{{lastOutput}}\n\n"
                        "Structure:\n"
                        "1. Executive Summary (2-3 sentences)\n"
                        "2. Key Points (bullet list)\n"
                        "3. Detailed Findings (organised by topic)\n"
                        "4. Conclusion"
                    ),
                    "model": _DEFAULT_MODEL,
                    "outputFormat": "Text",
                    "selectedTools": [],
                    "mcpServerIds": [],
                },
            },
            _end_node(pos_x=850),
        ],
        "edges": [
            {"id": "e1", "source": "start-1", "target": "agent-1"},
            {"id": "e2", "source": "agent-1", "target": "agent-2"},
            {"id": "e3", "source": "agent-2", "target": "end-1"},
        ],
    }


# ─── Template 4 — Branching with Guardrails ──────────────────────────────


def _guardrails_branching() -> dict[str, Any]:
    """Demonstrates the four conditional features unique to Composer:
    guardrails (transparent pass-through), branch labels on if-else,
    Mustache references in conditions, and node-name aliasing."""
    return {
        "name": "Example 4: Guardrails + Branching",
        "description": (
            "An agent answers a question; a guardrails node screens for "
            "PII and prompt-injection; an if-else routes the result to "
            "either a downstream consumer or a 'flagged' fallback. "
            "Reference for the conditional/safety pattern."
        ),
        "category": "examples",
        "tags": ["example", "intermediate", "guardrails", "if-else", "branching"],
        "difficulty": "intermediate",
        "estimatedTime": "3-5 minutes",
        "externalSlug": "template-04-guardrails-branching",
        "nodes": [
            _start_node(
                label="Start",
                inputs=[
                    {
                        "name": "question",
                        "type": "string",
                        "required": True,
                        "description": "Question for the agent",
                        "defaultValue": "Summarise the safety policy in plain English.",
                    },
                ],
            ),
            {
                "id": "agent-1",
                "type": "agent",
                "position": {"x": 320, "y": 200},
                "data": {
                    "label": "Answer Agent",
                    "nodeName": "Answer Agent",
                    "instructions": (
                        "You are a helpful assistant. Answer this question:\n\n"
                        "{{question}}\n\n"
                        "Be clear and concise."
                    ),
                    "model": _DEFAULT_MODEL,
                    "outputFormat": "Text",
                    "selectedTools": [],
                    "mcpServerIds": [],
                },
            },
            {
                "id": "guardrails-1",
                "type": "guardrails",
                "position": {"x": 540, "y": 200},
                "data": {
                    "label": "Check Guard",
                    "nodeName": "Check Guard",
                    "piiEnabled": True,
                    "moderationEnabled": True,
                    "jailbreakEnabled": True,
                    "hallucinationEnabled": False,
                    "actionOnViolation": "warn",
                    "model": _DEFAULT_MODEL,
                },
            },
            {
                "id": "if-else-1",
                "type": "if-else",
                "position": {"x": 760, "y": 200},
                "data": {
                    "label": "Route by Verdict",
                    "nodeName": "Route by Verdict",
                    "condition": "{{check_guard.passed}}",
                },
            },
            {
                "id": "agent-deliver",
                "type": "agent",
                "position": {"x": 980, "y": 100},
                "data": {
                    "label": "Deliver Answer",
                    "nodeName": "Deliver Answer",
                    "instructions": (
                        "The original answer passed all safety checks. "
                        "Format it for the end user as a friendly response:\n\n"
                        "{{lastOutput}}"
                    ),
                    "model": _DEFAULT_MODEL,
                    "outputFormat": "Text",
                    "selectedTools": [],
                    "mcpServerIds": [],
                },
            },
            {
                "id": "agent-flagged",
                "type": "agent",
                "position": {"x": 980, "y": 300},
                "data": {
                    "label": "Flagged Response",
                    "nodeName": "Flagged Response",
                    "instructions": (
                        "The answer was flagged by guardrails: "
                        "{{check_guard.violations}}\n\n"
                        "Politely tell the user we can't return this content "
                        "and explain why in one sentence."
                    ),
                    "model": _DEFAULT_MODEL,
                    "outputFormat": "Text",
                    "selectedTools": [],
                    "mcpServerIds": [],
                },
            },
            _end_node(pos_x=1200),
        ],
        "edges": [
            {"id": "e1", "source": "start-1", "target": "agent-1"},
            {"id": "e2", "source": "agent-1", "target": "guardrails-1"},
            {"id": "e3", "source": "guardrails-1", "target": "if-else-1"},
            {
                "id": "e4-true",
                "source": "if-else-1",
                "target": "agent-deliver",
                "sourceHandle": "true",
                "branch": "true",
                "label": "true",
            },
            {
                "id": "e4-false",
                "source": "if-else-1",
                "target": "agent-flagged",
                "sourceHandle": "false",
                "branch": "false",
                "label": "false",
            },
            {"id": "e5", "source": "agent-deliver", "target": "end-1"},
            {"id": "e6", "source": "agent-flagged", "target": "end-1"},
        ],
    }


# ─── Template 5 — Multi-company Stock Analysis ──────────────────────────


def _multi_company_stock_analysis() -> dict[str, Any]:
    """Three explicit HTTP fetches against Yahoo Finance's public chart
    endpoint, then an agent synthesises a comparative report.  Hardcoded
    tickers keep the template robust — designers swap them in the
    `httpUrl` field after cloning.

    Why not a true loop: Composer's `while` + counter pattern needs
    set-state + transform nodes to maintain an index, and accumulating
    per-iteration results into a list requires careful simpleeval
    arithmetic that's brittle to template.  Three explicit calls in a
    chain demonstrate the multi-source synthesis pattern more
    reliably and without sacrificing clarity."""
    yahoo = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=5d"
    return {
        "name": "Example 5: Multi-Company Stock Analysis",
        "description": (
            "Fetches 5-day price data for three tickers from Yahoo Finance "
            "and produces a comparative report. Demonstrates the HTTP node "
            "with public APIs and multi-source agent synthesis."
        ),
        "category": "examples",
        "tags": ["example", "advanced", "http", "finance", "multi-source"],
        "difficulty": "advanced",
        "estimatedTime": "3-5 minutes",
        "externalSlug": "template-05-multi-company-stock-analysis",
        "nodes": [
            _start_node(
                label="Start",
                inputs=[
                    {
                        "name": "report_focus",
                        "type": "string",
                        "required": False,
                        "description": "What angle should the comparison emphasise?",
                        "defaultValue": "5-day price momentum and which stock looks strongest",
                    },
                ],
            ),
            {
                "id": "fetch-aapl",
                "type": "http",
                "position": {"x": 320, "y": 100},
                "data": {
                    "label": "Fetch AAPL",
                    "nodeName": "Fetch AAPL",
                    "httpUrl": yahoo.format(ticker="AAPL"),
                    "httpMethod": "GET",
                    "httpHeaders": {"User-Agent": "composer-template/1.0"},
                    "responsePath": "chart.result[0]",
                },
            },
            {
                "id": "fetch-msft",
                "type": "http",
                "position": {"x": 320, "y": 230},
                "data": {
                    "label": "Fetch MSFT",
                    "nodeName": "Fetch MSFT",
                    "httpUrl": yahoo.format(ticker="MSFT"),
                    "httpMethod": "GET",
                    "httpHeaders": {"User-Agent": "composer-template/1.0"},
                    "responsePath": "chart.result[0]",
                },
            },
            {
                "id": "fetch-goog",
                "type": "http",
                "position": {"x": 320, "y": 360},
                "data": {
                    "label": "Fetch GOOG",
                    "nodeName": "Fetch GOOG",
                    "httpUrl": yahoo.format(ticker="GOOG"),
                    "httpMethod": "GET",
                    "httpHeaders": {"User-Agent": "composer-template/1.0"},
                    "responsePath": "chart.result[0]",
                },
            },
            {
                "id": "agent-1",
                "type": "agent",
                "position": {"x": 600, "y": 230},
                "data": {
                    "label": "Comparative Analyst",
                    "nodeName": "Comparative Analyst",
                    "instructions": (
                        "You have 5-day Yahoo Finance chart data for three tickers:\n\n"
                        "AAPL: {{fetch_aapl}}\n\n"
                        "MSFT: {{fetch_msft}}\n\n"
                        "GOOG: {{fetch_goog}}\n\n"
                        "Each payload contains `meta` (regularMarketPrice, "
                        "previousClose, fiftyTwoWeekHigh/Low, etc.) and "
                        "`indicators.quote[0]` (close, open, high, low, "
                        "volume arrays).\n\n"
                        "Produce a short comparative report focused on: "
                        "{{report_focus}}\n\n"
                        "Structure:\n"
                        "1. Snapshot — current price + 5-day % change per ticker\n"
                        "2. Momentum ranking — strongest to weakest\n"
                        "3. Notable observations (volume spikes, gaps, etc.)\n"
                        "4. One-line bottom line for each ticker"
                    ),
                    "model": _DEFAULT_MODEL,
                    "outputFormat": "Text",
                    "selectedTools": [],
                    "mcpServerIds": [],
                },
            },
            _end_node(pos_x=900, pos_y=230),
        ],
        "edges": [
            {"id": "e1", "source": "start-1", "target": "fetch-aapl"},
            {"id": "e2", "source": "fetch-aapl", "target": "fetch-msft"},
            {"id": "e3", "source": "fetch-msft", "target": "fetch-goog"},
            {"id": "e4", "source": "fetch-goog", "target": "agent-1"},
            {"id": "e5", "source": "agent-1", "target": "end-1"},
        ],
    }


# ─── Template 6 — Yahoo Finance Stock Report ─────────────────────────────


def _yahoo_finance_stock_report() -> dict[str, Any]:
    """Single-ticker deep dive: HTTP → extract (structured) → agent narrative.

    Demonstrates the extract node, which uses the LLM with a JSON schema
    to pull structured data from messy HTTP responses without prompt
    fragility."""
    return {
        "name": "Example 6: Yahoo Finance Stock Report",
        "description": (
            "Fetches a single ticker from Yahoo Finance, extracts structured "
            "fields with a JSON schema, then produces a narrative report. "
            "Reference for the HTTP → Extract → Agent pattern."
        ),
        "category": "examples",
        "tags": ["example", "intermediate", "http", "extract", "finance"],
        "difficulty": "intermediate",
        "estimatedTime": "2-3 minutes",
        "externalSlug": "template-06-yahoo-finance-stock-report",
        "nodes": [
            _start_node(
                label="Start",
                inputs=[
                    {
                        "name": "ticker",
                        "type": "string",
                        "required": True,
                        "description": "Stock ticker symbol (e.g. AAPL, NVDA)",
                        "defaultValue": "NVDA",
                    },
                ],
            ),
            {
                "id": "fetch-1",
                "type": "http",
                "position": {"x": 320, "y": 200},
                "data": {
                    "label": "Yahoo Finance",
                    "nodeName": "Yahoo Finance",
                    "httpUrl": (
                        "https://query1.finance.yahoo.com/v8/finance/chart/"
                        "{{ticker}}?interval=1d&range=1mo"
                    ),
                    "httpMethod": "GET",
                    "httpHeaders": {"User-Agent": "composer-template/1.0"},
                    "responsePath": "chart.result[0].meta",
                },
            },
            {
                "id": "extract-1",
                "type": "extract",
                "position": {"x": 580, "y": 200},
                "data": {
                    "label": "Extract Key Metrics",
                    "nodeName": "Extract Key Metrics",
                    "input": "{{lastOutput}}",
                    "model": _DEFAULT_MODEL,
                    "jsonSchema": {
                        "type": "object",
                        "properties": {
                            "ticker": {"type": "string"},
                            "exchangeName": {"type": "string"},
                            "currency": {"type": "string"},
                            "regularMarketPrice": {"type": "number"},
                            "previousClose": {"type": "number"},
                            "fiftyTwoWeekHigh": {"type": "number"},
                            "fiftyTwoWeekLow": {"type": "number"},
                            "regularMarketDayHigh": {"type": "number"},
                            "regularMarketDayLow": {"type": "number"},
                        },
                        "required": ["ticker", "regularMarketPrice"],
                    },
                },
            },
            {
                "id": "agent-1",
                "type": "agent",
                "position": {"x": 840, "y": 200},
                "data": {
                    "label": "Stock Analyst",
                    "nodeName": "Stock Analyst",
                    "instructions": (
                        "Write a short stock report for the extracted metrics:\n\n"
                        "{{lastOutput}}\n\n"
                        "Cover:\n"
                        "1. Current price vs previous close (% change, direction)\n"
                        "2. Position within the 52-week range (near high, near low, mid)\n"
                        "3. Today's intraday range tightness\n"
                        "4. One-sentence read-through for an investor\n\n"
                        "Be concise — 4 short paragraphs at most."
                    ),
                    "model": _DEFAULT_MODEL,
                    "outputFormat": "Text",
                    "selectedTools": [],
                    "mcpServerIds": [],
                },
            },
            _end_node(pos_x=1100),
        ],
        "edges": [
            {"id": "e1", "source": "start-1", "target": "fetch-1"},
            {"id": "e2", "source": "fetch-1", "target": "extract-1"},
            {"id": "e3", "source": "extract-1", "target": "agent-1"},
            {"id": "e4", "source": "agent-1", "target": "end-1"},
        ],
    }


# ─── Template 7 — Amazon Product Research ────────────────────────────────


def _amazon_product_research() -> dict[str, Any]:
    """Firecrawl scrape Amazon search results → extract structured products
    → agent recommendation.  Demonstrates the scrape + extract pipeline
    against a site that blocks naive HTTP scrapers."""
    return {
        "name": "Example 7: Amazon Product Research",
        "description": (
            "Scrapes an Amazon search results page with Firecrawl, extracts "
            "structured product data with a JSON schema, and an agent picks "
            "a recommendation. Shows the scrape → extract → agent pipeline. "
            "Requires FIRECRAWL_API_KEY."
        ),
        "category": "examples",
        "tags": ["example", "advanced", "firecrawl", "extract", "ecommerce"],
        "difficulty": "advanced",
        "estimatedTime": "3-5 minutes",
        "externalSlug": "template-07-amazon-product-research",
        "nodes": [
            _start_node(
                label="Start",
                inputs=[
                    {
                        "name": "search_query",
                        "type": "string",
                        "required": True,
                        "description": "What product are you researching?",
                        "defaultValue": "noise cancelling headphones under 200",
                    },
                ],
            ),
            {
                "id": "agent-scrape",
                "type": "agent",
                "position": {"x": 320, "y": 200},
                "data": {
                    "label": "Scrape Search Results",
                    "nodeName": "Scrape Search Results",
                    "instructions": (
                        "Use the firecrawl_scrape tool on this Amazon search URL "
                        "(URL-encode the query, replacing spaces with +):\n\n"
                        "https://www.amazon.com/s?k={{search_query}}\n\n"
                        "Return the markdown response verbatim — the next step "
                        "will extract structured products."
                    ),
                    "model": _DEFAULT_MODEL,
                    "outputFormat": "Text",
                    "selectedTools": ["firecrawl.firecrawl_scrape"],
                    "mcpServerIds": [],
                },
            },
            {
                "id": "extract-1",
                "type": "extract",
                "position": {"x": 600, "y": 200},
                "data": {
                    "label": "Extract Products",
                    "nodeName": "Extract Products",
                    "input": "{{lastOutput}}",
                    "model": _DEFAULT_MODEL,
                    "jsonSchema": {
                        "type": "object",
                        "properties": {
                            "products": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "title": {"type": "string"},
                                        "brand": {"type": "string"},
                                        "price_usd": {"type": "number"},
                                        "rating": {"type": "number"},
                                        "review_count": {"type": "number"},
                                        "url": {"type": "string"},
                                    },
                                    "required": ["title"],
                                },
                            },
                        },
                        "required": ["products"],
                    },
                },
            },
            {
                "id": "agent-recommend",
                "type": "agent",
                "position": {"x": 880, "y": 200},
                "data": {
                    "label": "Recommendation Agent",
                    "nodeName": "Recommendation Agent",
                    "instructions": (
                        "Given these extracted products:\n\n"
                        "{{lastOutput}}\n\n"
                        "Original query: {{search_query}}\n\n"
                        "Pick the top 3 candidates and explain why each made "
                        "the cut. Consider price-to-rating ratio, brand "
                        "reputation, and review depth (high review counts "
                        "signal more reliable ratings).\n\n"
                        "Format: ranked list with one paragraph rationale per pick."
                    ),
                    "model": _DEFAULT_MODEL,
                    "outputFormat": "Text",
                    "selectedTools": [],
                    "mcpServerIds": [],
                },
            },
            _end_node(pos_x=1140),
        ],
        "edges": [
            {"id": "e1", "source": "start-1", "target": "agent-scrape"},
            {"id": "e2", "source": "agent-scrape", "target": "extract-1"},
            {"id": "e3", "source": "extract-1", "target": "agent-recommend"},
            {"id": "e4", "source": "agent-recommend", "target": "end-1"},
        ],
    }


# ─── Template 8 — Human-in-the-Loop Approval ─────────────────────────────


def _human_approval() -> dict[str, Any]:
    """Agent drafts content → user-approval gate pauses execution →
    branched routing on approve/reject.  Showcases the approval +
    interrupt/resume pattern."""
    return {
        "name": "Example 8: Human-in-the-Loop Approval",
        "description": (
            "An agent drafts a piece of content; execution pauses at a "
            "user-approval gate. On approve, a finalisation agent polishes "
            "and ships it. On reject, a revision agent rewrites with the "
            "reviewer's notes. Reference for the HITL pattern."
        ),
        "category": "examples",
        "tags": ["example", "intermediate", "user-approval", "branching", "hitl"],
        "difficulty": "intermediate",
        "estimatedTime": "5-10 minutes (waits for approval)",
        "externalSlug": "template-08-human-approval",
        "nodes": [
            _start_node(
                label="Start",
                inputs=[
                    {
                        "name": "topic",
                        "type": "string",
                        "required": True,
                        "description": "What to write about",
                        "defaultValue": "the impact of agentic AI on knowledge work in 2026",
                    },
                    {
                        "name": "format",
                        "type": "string",
                        "required": False,
                        "description": "Output format (e.g. tweet, LinkedIn post, blog intro)",
                        "defaultValue": "LinkedIn post (200 words, professional tone)",
                    },
                ],
            ),
            {
                "id": "agent-draft",
                "type": "agent",
                "position": {"x": 300, "y": 250},
                "data": {
                    "label": "Draft Writer",
                    "nodeName": "Draft Writer",
                    "instructions": (
                        "Write a draft on this topic: {{topic}}\n\n"
                        "Format: {{format}}\n\n"
                        "Write the draft directly — no preamble, no "
                        "'here is a draft' framing. The reviewer will see "
                        "this verbatim."
                    ),
                    "model": _DEFAULT_MODEL,
                    "outputFormat": "Text",
                    "selectedTools": [],
                    "mcpServerIds": [],
                },
            },
            {
                "id": "approval-1",
                "type": "user-approval",
                "position": {"x": 560, "y": 250},
                "data": {
                    "label": "Reviewer Gate",
                    "nodeName": "Reviewer Gate",
                    "approvalMessage": (
                        "Review the draft above. Approve to publish as-is. "
                        "Reject (with notes in the comment) to send it back "
                        "for revision."
                    ),
                },
            },
            {
                "id": "agent-finalize",
                "type": "agent",
                "position": {"x": 820, "y": 130},
                "data": {
                    "label": "Final Polish",
                    "nodeName": "Final Polish",
                    "instructions": (
                        "The draft was approved. Apply final polish — tighten "
                        "any wordy phrases, fix punctuation, ensure it reads "
                        "smoothly. Do NOT change the substance.\n\n"
                        "Approved draft:\n{{lastOutput}}"
                    ),
                    "model": _DEFAULT_MODEL,
                    "outputFormat": "Text",
                    "selectedTools": [],
                    "mcpServerIds": [],
                },
            },
            {
                "id": "agent-revise",
                "type": "agent",
                "position": {"x": 820, "y": 370},
                "data": {
                    "label": "Revise on Rejection",
                    "nodeName": "Revise on Rejection",
                    "instructions": (
                        "The reviewer rejected the draft. Their feedback is in "
                        "the approval node's `note` field — read it carefully "
                        "and rewrite the draft addressing each point.\n\n"
                        "Original draft:\n{{lastOutput}}\n\n"
                        "Reviewer notes are accessible via "
                        "{{node_results.approval_1.output.note}}.\n\n"
                        "Return the revised draft only — no explanation of "
                        "what you changed."
                    ),
                    "model": _DEFAULT_MODEL,
                    "outputFormat": "Text",
                    "selectedTools": [],
                    "mcpServerIds": [],
                },
            },
            _end_node(pos_x=1080, pos_y=250),
        ],
        "edges": [
            {"id": "e1", "source": "start-1", "target": "agent-draft"},
            {"id": "e2", "source": "agent-draft", "target": "approval-1"},
            {
                "id": "e3-approved",
                "source": "approval-1",
                "target": "agent-finalize",
                "sourceHandle": "approved",
                "branch": "approved",
                "label": "approved",
            },
            {
                "id": "e3-rejected",
                "source": "approval-1",
                "target": "agent-revise",
                "sourceHandle": "rejected",
                "branch": "rejected",
                "label": "rejected",
            },
            {"id": "e4", "source": "agent-finalize", "target": "end-1"},
            {"id": "e5", "source": "agent-revise", "target": "end-1"},
        ],
    }


# ─── Template 9 — Zillow Property Finder ─────────────────────────────────


def _zillow_property_finder() -> dict[str, Any]:
    """Firecrawl scrape Zillow → extract listings → data-transform filter
    → agent recommendation.  Demonstrates the data-transform `filter`
    operation against a structured collection."""
    return {
        "name": "Example 9: Zillow Property Finder",
        "description": (
            "Scrapes a Zillow search URL with Firecrawl, extracts listings as "
            "structured data, filters by your max price + minimum beds, and "
            "an agent picks top candidates. Demonstrates the filter pipeline. "
            "Requires FIRECRAWL_API_KEY."
        ),
        "category": "examples",
        "tags": ["example", "advanced", "firecrawl", "extract", "data-transform", "real-estate"],
        "difficulty": "advanced",
        "estimatedTime": "3-5 minutes",
        "externalSlug": "template-09-zillow-property-finder",
        "nodes": [
            _start_node(
                label="Start",
                inputs=[
                    {
                        "name": "zillow_url",
                        "type": "string",
                        "required": True,
                        "description": "A Zillow search results URL",
                        "defaultValue": "https://www.zillow.com/homes/Austin,-TX_rb/",
                    },
                    {
                        "name": "max_price",
                        "type": "number",
                        "required": False,
                        "description": "Maximum listing price (USD)",
                        "defaultValue": 750000,
                    },
                    {
                        "name": "min_beds",
                        "type": "number",
                        "required": False,
                        "description": "Minimum bedrooms",
                        "defaultValue": 3,
                    },
                ],
            ),
            {
                "id": "agent-scrape",
                "type": "agent",
                "position": {"x": 300, "y": 250},
                "data": {
                    "label": "Scrape Zillow",
                    "nodeName": "Scrape Zillow",
                    "instructions": (
                        "Use firecrawl_scrape on this URL: {{zillow_url}}\n\n"
                        "Return the markdown verbatim — the extract step will "
                        "parse the listings."
                    ),
                    "model": _DEFAULT_MODEL,
                    "outputFormat": "Text",
                    "selectedTools": ["firecrawl.firecrawl_scrape"],
                    "mcpServerIds": [],
                },
            },
            {
                "id": "extract-1",
                "type": "extract",
                "position": {"x": 580, "y": 250},
                "data": {
                    "label": "Extract Listings",
                    "nodeName": "Extract Listings",
                    "input": "{{lastOutput}}",
                    "model": _DEFAULT_MODEL,
                    "jsonSchema": {
                        "type": "object",
                        "properties": {
                            "listings": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "address": {"type": "string"},
                                        "price": {"type": "number"},
                                        "beds": {"type": "number"},
                                        "baths": {"type": "number"},
                                        "sqft": {"type": "number"},
                                        "url": {"type": "string"},
                                    },
                                    "required": ["address", "price"],
                                },
                            },
                        },
                        "required": ["listings"],
                    },
                },
            },
            {
                "id": "filter-1",
                "type": "data-transform",
                "position": {"x": 860, "y": 250},
                "data": {
                    "label": "Filter Listings",
                    "nodeName": "Filter Listings",
                    "operation": "filter",
                    # `extract_listings` is the events_wrapper alias for the
                    # extract node above — reaches into its output's
                    # `listings` array.  Mustache form rewrites to subscript
                    # form before simpleeval evaluates.
                    "collection": "{{extract_listings.listings}}",
                    "expression": ('item["price"] <= max_price and item["beds"] >= min_beds'),
                    "itemVar": "item",
                },
            },
            {
                "id": "agent-recommend",
                "type": "agent",
                "position": {"x": 1140, "y": 250},
                "data": {
                    "label": "Pick Favorites",
                    "nodeName": "Pick Favorites",
                    "instructions": (
                        "These listings match the filter "
                        "(under ${{max_price}} with at least {{min_beds}} beds):\n\n"
                        "{{lastOutput}}\n\n"
                        "Pick the top 3 by price-per-sqft and explain the "
                        "trade-offs of each. If fewer than 3 listings match, "
                        "say so and rank what's available."
                    ),
                    "model": _DEFAULT_MODEL,
                    "outputFormat": "Text",
                    "selectedTools": [],
                    "mcpServerIds": [],
                },
            },
            _end_node(pos_x=1400, pos_y=250),
        ],
        "edges": [
            {"id": "e1", "source": "start-1", "target": "agent-scrape"},
            {"id": "e2", "source": "agent-scrape", "target": "extract-1"},
            {"id": "e3", "source": "extract-1", "target": "filter-1"},
            {"id": "e4", "source": "filter-1", "target": "agent-recommend"},
            {"id": "e5", "source": "agent-recommend", "target": "end-1"},
        ],
    }


_TEMPLATES: list[dict[str, Any]] = [
    _simple_agent(),
    _web_research_agent(),
    _scrape_and_summarise(),
    _guardrails_branching(),
    _multi_company_stock_analysis(),
    _yahoo_finance_stock_report(),
    _amazon_product_research(),
    _human_approval(),
    _zillow_property_finder(),
]


async def _upsert_template(
    db: Prisma,  # pyright: ignore[reportUnknownParameterType]
    tmpl: dict[str, Any],
) -> str:
    """Idempotent upsert by `externalSlug`.  Returns "created" / "updated"
    so the caller can log per-template outcomes."""
    slug = tmpl["externalSlug"]
    existing = await db.workflow.find_unique(  # pyright: ignore[reportAttributeAccessIssue]
        where={"externalSlug": slug}
    )
    common: dict[str, Any] = {
        "name": tmpl["name"],
        "description": tmpl["description"],
        "category": tmpl["category"],
        "tags": tmpl["tags"],
        "difficulty": tmpl["difficulty"],
        "estimatedTime": tmpl["estimatedTime"],
        # Prisma needs the Json() wrapper for JSON columns — the
        # workflow CRUD route does the same in src/api/workflows.py.
        "nodes": Json(tmpl["nodes"]),
        "edges": Json(tmpl["edges"]),
        "isTemplate": True,
        "isPublic": True,
        "isProduction": False,
        "externalSlug": slug,
        "userId": None,
    }
    if existing is None:
        await db.workflow.create(data=common)  # pyright: ignore[reportAttributeAccessIssue,reportArgumentType]
        return "created"
    update_data = {k: v for k, v in common.items() if k != "externalSlug"}
    await db.workflow.update(  # pyright: ignore[reportAttributeAccessIssue]
        where={"externalSlug": slug},
        data=update_data,  # pyright: ignore[reportArgumentType]
    )
    return "updated"


async def main() -> None:
    db = Prisma()
    await db.connect()
    try:
        for tmpl in _TEMPLATES:
            outcome = await _upsert_template(db, tmpl)
            print(f"  {outcome:7s}  {tmpl['externalSlug']:40s}  {tmpl['name']}")
    finally:
        await db.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
