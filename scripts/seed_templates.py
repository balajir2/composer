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


# ─── Template 10 — While-loop with accumulator (compute-and-name demo) ──


def _while_loop_demo() -> dict[str, Any]:
    """A real while-loop iterating over a list, accumulating results.

    Demonstrates the `outputKey` field on the transform node — without
    it, every "compute and persist" step needs a transform → set-state
    pair, doubling the body's node count.  With it, the loop body fits
    in 4 nodes: pick element, fetch, append to results, increment index.

    The condition `index < len(tickers)` reads `index` directly from the
    eval scope (top-level variable spread).  The accumulator
    `results + [{...}]` produces a new list each iteration which the
    transform writes back to `results` via outputKey, replacing the
    old value via the merge_dict reducer.

    Yahoo Finance's chart endpoint is the data source — public, no key
    required, returns JSON the agent can summarise.
    """
    yahoo = (
        "https://query1.finance.yahoo.com/v8/finance/chart/{{current_ticker}}?interval=1d&range=5d"
    )
    return {
        "name": "Example 10: While-loop with Accumulator",
        "description": (
            "Iterates over a list of tickers with a real while-loop, "
            "fetches each from Yahoo Finance, accumulates summaries into "
            "a list, then renders a comparative report. Reference for "
            "the loop + accumulator pattern using transform's outputKey."
        ),
        "category": "examples",
        "tags": ["example", "advanced", "loop", "accumulator", "http"],
        "difficulty": "advanced",
        "estimatedTime": "5-7 minutes",
        "externalSlug": "template-10-while-loop-accumulator",
        "nodes": [
            _start_node(
                label="Start",
                inputs=[
                    {
                        "name": "report_focus",
                        "type": "string",
                        "required": False,
                        "description": "Angle to emphasise in the final report",
                        "defaultValue": "5-day price momentum and which stock looks strongest",
                    },
                ],
            ),
            # Init the loop state — three named variables in three lines.
            # Could be inlined into a single transform with outputKey
            # writing a tuple/dict, but separate set-state nodes read
            # more clearly for a template that's meant to be studied.
            {
                "id": "init-tickers",
                "type": "set-state",
                "position": {"x": 280, "y": 120},
                "data": {
                    "label": "Init: tickers",
                    "nodeName": "Init: tickers",
                    "stateKey": "tickers",
                    "stateValue": ["AAPL", "MSFT", "GOOG"],
                },
            },
            {
                "id": "init-index",
                "type": "set-state",
                "position": {"x": 280, "y": 230},
                "data": {
                    "label": "Init: index",
                    "nodeName": "Init: index",
                    "stateKey": "index",
                    "stateValue": 0,
                },
            },
            {
                "id": "init-results",
                "type": "set-state",
                "position": {"x": 280, "y": 340},
                "data": {
                    "label": "Init: results",
                    "nodeName": "Init: results",
                    "stateKey": "results",
                    "stateValue": [],
                },
            },
            {
                "id": "loop-1",
                "type": "while",
                "position": {"x": 540, "y": 230},
                "data": {
                    "label": "While index < N",
                    "nodeName": "While index < N",
                    "condition": "index < len(tickers)",
                    "maxIterations": 10,
                },
            },
            # Body: pick the current ticker by index, fetch its chart,
            # append summary to `results`, increment `index`, loop back.
            # Four nodes for the body — set-state's increment-by-one
            # role is now done inside transform via outputKey.
            {
                "id": "pick-ticker",
                "type": "transform",
                "position": {"x": 800, "y": 130},
                "data": {
                    "label": "Pick ticker",
                    "nodeName": "Pick ticker",
                    "transformScript": "tickers[index]",
                    "outputKey": "current_ticker",
                },
            },
            {
                "id": "fetch-1",
                "type": "http",
                "position": {"x": 1020, "y": 130},
                "data": {
                    "label": "Fetch chart",
                    "nodeName": "Fetch chart",
                    "httpUrl": yahoo,
                    "httpMethod": "GET",
                    "httpHeaders": {"User-Agent": "composer-template/1.0"},
                    "responsePath": "chart.result[0].meta",
                },
            },
            {
                "id": "accum-1",
                "type": "transform",
                "position": {"x": 1240, "y": 130},
                "data": {
                    "label": "Append result",
                    "nodeName": "Append result",
                    # Build a record for this ticker and append it to the
                    # running list.  outputKey writes the new list back
                    # to `results` so the next iteration's accumulator
                    # sees the growing list.  Without outputKey this
                    # would need a follow-up set-state node.
                    "transformScript": (
                        'results + [{"ticker": current_ticker, "meta": lastOutput}]'
                    ),
                    "outputKey": "results",
                },
            },
            {
                "id": "increment-1",
                "type": "transform",
                "position": {"x": 1240, "y": 250},
                "data": {
                    "label": "Increment index",
                    "nodeName": "Increment index",
                    "transformScript": "index + 1",
                    "outputKey": "index",
                },
            },
            # Exit: synthesise the final report from accumulated results.
            {
                "id": "agent-1",
                "type": "agent",
                "position": {"x": 800, "y": 400},
                "data": {
                    "label": "Comparative Report",
                    "nodeName": "Comparative Report",
                    "instructions": (
                        "You have collected 5-day chart data for several "
                        "tickers in this list:\n\n"
                        "{{results}}\n\n"
                        "Each entry has `ticker` and `meta` (with "
                        "regularMarketPrice, previousClose, "
                        "fiftyTwoWeekHigh/Low, etc).\n\n"
                        "Produce a short comparative report focused on: "
                        "{{report_focus}}\n\n"
                        "Structure:\n"
                        "1. Snapshot — current price + 5-day % change per ticker\n"
                        "2. Momentum ranking — strongest to weakest\n"
                        "3. One-line bottom line per ticker"
                    ),
                    "model": _DEFAULT_MODEL,
                    "outputFormat": "Text",
                    "selectedTools": [],
                    "mcpServerIds": [],
                },
            },
            _end_node(pos_x=1100, pos_y=400),
        ],
        "edges": [
            {"id": "e0a", "source": "start-1", "target": "init-tickers"},
            {"id": "e0b", "source": "init-tickers", "target": "init-index"},
            {"id": "e0c", "source": "init-index", "target": "init-results"},
            {"id": "e1", "source": "init-results", "target": "loop-1"},
            # Body branch: into the loop body chain
            {
                "id": "e2-body",
                "source": "loop-1",
                "target": "pick-ticker",
                "sourceHandle": "body",
                "branch": "body",
                "label": "body",
            },
            {"id": "e3", "source": "pick-ticker", "target": "fetch-1"},
            {"id": "e4", "source": "fetch-1", "target": "accum-1"},
            {"id": "e5", "source": "accum-1", "target": "increment-1"},
            # Loop back to the while node so it re-evaluates the condition
            {"id": "e6", "source": "increment-1", "target": "loop-1"},
            # Exit branch: when condition is false, synthesise the report
            {
                "id": "e7-exit",
                "source": "loop-1",
                "target": "agent-1",
                "sourceHandle": "exit",
                "branch": "exit",
                "label": "exit",
            },
            {"id": "e8", "source": "agent-1", "target": "end-1"},
        ],
    }


# ─── Template 11 — RAG with Vector DB + Join Chunks ──────────────────────


def _rag_with_vector_db() -> dict[str, Any]:
    """Classic Retrieval-Augmented Generation pipeline.

    Vector DB returns a results object with `text`-bearing chunks; a
    transform extracts the array under a named variable so join-chunks
    can stitch the chunk texts into a single context block; the agent
    answers using the chunks as grounding.

    The vector-db node ships with all-empty endpoint / API key /
    collection because those are per-deployment.  Designers clone the
    template and fill those fields in on the canvas — the agent will
    fail fast with the unconfigured-endpoint error until they do.

    Why three nodes between vector-db and the agent (transform +
    join-chunks instead of just one): vector-db's built-in
    `joinResults` flag could collapse this to a single configuration
    flip, but using join-chunks explicitly demonstrates how to feed
    *any* list of chunks (from extraction, parsing, or another source)
    into the LLM context.  Designers reuse this pattern far more
    often than they reuse vector-db's bundled join.
    """
    return {
        "name": "Example 11: RAG - Vector DB + Join Chunks",
        "description": (
            "Retrieval-augmented Q&A: vector DB returns the top-k relevant "
            "chunks for a question, join-chunks stitches them into a "
            "context block, and an agent answers using the chunks as "
            "grounding. After cloning, open the Vector DB node and set "
            "the provider, endpoint, API key, and collection for your "
            "instance (Pinecone / Qdrant / Chroma / Weaviate / Milvus). "
            "Embedding key (OPENAI_API_KEY) must be set in the deployment."
        ),
        "category": "examples",
        "tags": ["example", "advanced", "vector-db", "join-chunks", "rag", "embeddings"],
        "difficulty": "advanced",
        "estimatedTime": "5-7 minutes (after configuring the vector DB)",
        "externalSlug": "template-11-rag-vector-db",
        "nodes": [
            _start_node(
                label="Start",
                inputs=[
                    {
                        "name": "question",
                        "type": "string",
                        "required": True,
                        "description": "What do you want to know?",
                        "defaultValue": "What are the key principles of agentic AI design?",
                    },
                ],
            ),
            {
                "id": "vector-db-1",
                "type": "vector-db",
                "position": {"x": 280, "y": 250},
                "data": {
                    "label": "Retrieve Chunks",
                    "nodeName": "Retrieve Chunks",
                    # Provider + connection — designer fills these in
                    # after cloning.  Pinecone is the most-used option
                    # so it's the default selection; the panel on the
                    # canvas lets them switch.
                    "vectorDbProvider": "pinecone",
                    "vectorDbEndpoint": "",
                    "vectorDbApiKey": "",
                    "vectorDbCollection": "",
                    # Query — the question flows through Mustache.  The
                    # vector-db executor calls `substitute(query_prompt,
                    # state)` so {{question}} resolves before embedding.
                    "vectorDbQueryPrompt": "{{question}}",
                    "vectorDbTopK": 5,
                    "vectorDbScoreThreshold": 0,
                    # Embedding — OpenAI's text-embedding-3-small is the
                    # cheapest reasonable default.  Dimension matches.
                    "vectorDbEmbeddingProvider": "openai",
                    "vectorDbEmbeddingModel": "text-embedding-3-small",
                    "vectorDbDimension": 1536,
                    "vectorDbIncludeMetadata": True,
                    "vectorDbIncludeVector": False,
                    "vectorDbOutputVariable": "vectorDbResults",
                },
            },
            # Vector-db's output is `{query, results: [...], total, ...}`.
            # join-chunks expects a flat list of chunks at a named
            # variable, so we extract `results` here and persist it as
            # `chunks` via the transform's outputKey.  This is the
            # canonical "compute and name" pattern that outputKey
            # collapses from two nodes (transform → set-state) into one.
            {
                "id": "extract-chunks",
                "type": "transform",
                "position": {"x": 540, "y": 250},
                "data": {
                    "label": "Extract Chunks",
                    "nodeName": "Extract Chunks",
                    "transformScript": 'vectorDbResults["results"]',
                    "outputKey": "chunks",
                },
            },
            {
                "id": "join-1",
                "type": "join-chunks",
                "position": {"x": 800, "y": 250},
                "data": {
                    "label": "Stitch Context",
                    "nodeName": "Stitch Context",
                    "joinChunksVariable": "chunks",
                    # Clear visual separator so the LLM can tell where
                    # one chunk ends and the next begins.  Without
                    # this, retrieved snippets blur together and the
                    # answer can hallucinate cross-chunk connections.
                    "joinChunksSeparator": "\n\n---\n\n",
                    "joinChunksPrefix": "",
                    "joinChunksSuffix": "",
                    # Metadata appended in [metadata: {...}] gives the
                    # agent a way to cite sources without us having to
                    # prompt-engineer the citation format.
                    "joinChunksIncludeMetadata": True,
                },
            },
            {
                "id": "agent-1",
                "type": "agent",
                "position": {"x": 1080, "y": 250},
                "data": {
                    "label": "RAG Answer",
                    "nodeName": "RAG Answer",
                    "instructions": (
                        "You are a knowledgeable assistant. Answer the "
                        "user's question using ONLY the context chunks "
                        "below — if the answer isn't in the chunks, say "
                        "you don't have enough information rather than "
                        "guessing.\n\n"
                        "Question:\n{{question}}\n\n"
                        "Context chunks (separated by `---`):\n"
                        "{{lastOutput}}\n\n"
                        "Answer (cite sources from the [metadata: ...] "
                        "lines when present):"
                    ),
                    "model": _DEFAULT_MODEL,
                    "outputFormat": "Text",
                    "selectedTools": [],
                    "mcpServerIds": [],
                },
            },
            _end_node(pos_x=1340, pos_y=250),
        ],
        "edges": [
            {"id": "e1", "source": "start-1", "target": "vector-db-1"},
            {"id": "e2", "source": "vector-db-1", "target": "extract-chunks"},
            {"id": "e3", "source": "extract-chunks", "target": "join-1"},
            {"id": "e4", "source": "join-1", "target": "agent-1"},
            {"id": "e5", "source": "agent-1", "target": "end-1"},
        ],
    }


# ─── Template 12 — Document ingestion (vector DB upsert) ─────────────────


def _document_ingestion() -> dict[str, Any]:
    """Companion to template 11 (RAG retrieval): ingest documents
    into a vector DB.  Two paths into the same vector-db node:

    * **From a URL**: Firecrawl scrapes the page → vector-db chunks
      and upserts.
    * **Pasting raw text**: skip the scrape and just upsert.

    Either way the vector-db node in `upsert` mode handles the
    embedding + chunking + insert in a single step.  The same
    collection can then be queried by template 11.
    """
    return {
        "name": "Example 12: Document Ingestion (Vector DB Upsert)",
        "description": (
            "Upload a PDF, DOCX, Markdown, or plain-text file at run "
            "time; Composer extracts the text, chunks it, embeds each "
            "chunk via OpenAI, and inserts into your vector DB. Pairs "
            "with Example 11 (RAG retrieval) — ingest with this "
            "template, query with that one. After cloning, configure "
            "the Vector DB node with your provider, endpoint, API key, "
            "and collection. Requires OPENAI_API_KEY for embeddings."
        ),
        "category": "examples",
        "tags": ["example", "advanced", "vector-db", "ingestion", "embeddings", "rag", "documents"],
        "difficulty": "advanced",
        "estimatedTime": "3-5 minutes (after configuring the vector DB)",
        "externalSlug": "template-12-document-ingestion",
        "nodes": [
            _start_node(
                label="Start",
                inputs=[
                    {
                        "name": "document",
                        "type": "document",
                        "required": True,
                        "description": (
                            "Upload a PDF, DOCX, Markdown, or plain-text "
                            "file (max 10MB). The text is extracted "
                            "server-side and passed to the next node as "
                            "a regular string variable."
                        ),
                    },
                    {
                        "name": "source_label",
                        "type": "string",
                        "required": False,
                        "description": (
                            "Tag stored in chunk metadata for filtering "
                            "later. e.g. 'q1-2026-report', 'company-policy'."
                        ),
                        "defaultValue": "uploaded-doc",
                    },
                ],
            ),
            # Vector-db in UPSERT mode.  Documents expression is
            # `document` — the extracted text from the upload widget,
            # which the executor auto-chunks using chunk_size +
            # chunk_overlap below.  No glue node needed: the upload
            # endpoint hands the form a string, the form sends it as
            # the `document` input variable, and the executor sees it
            # as a regular string.
            {
                "id": "vector-db-1",
                "type": "vector-db",
                "position": {"x": 380, "y": 200},
                "data": {
                    "label": "Ingest to Vector DB",
                    "nodeName": "Ingest to Vector DB",
                    "vectorDbOperation": "upsert",
                    # Provider + connection — designer fills these in.
                    "vectorDbProvider": "pinecone",
                    "vectorDbEndpoint": "",
                    "vectorDbApiKey": "",
                    "vectorDbCollection": "",
                    # Documents — the extracted text from the document
                    # input.  The executor sees a string and auto-chunks
                    # via the chunk_size + chunk_overlap config below.
                    "vectorDbDocuments": "document",
                    "vectorDbChunkSize": 1000,
                    "vectorDbChunkOverlap": 100,
                    # Embedding — same defaults as template 11 so the
                    # ingest + query templates are dimension-compatible
                    # out of the box.
                    "vectorDbEmbeddingProvider": "openai",
                    "vectorDbEmbeddingModel": "text-embedding-3-small",
                    "vectorDbDimension": 1536,
                    "vectorDbOutputVariable": "ingestResult",
                },
            },
            # Final summary — surface the count + ids so the user knows
            # what landed.
            {
                "id": "agent-report",
                "type": "agent",
                "position": {"x": 680, "y": 200},
                "data": {
                    "label": "Ingestion Summary",
                    "nodeName": "Ingestion Summary",
                    "instructions": (
                        "Summarise the ingestion outcome for the user.\n\n"
                        "Result: {{ingestResult}}\n\n"
                        "Tag the report with the source label "
                        "({{source_label}}) and tell them how many chunks "
                        "landed in the {{ingestResult.collection}} "
                        "collection on {{ingestResult.provider}}. If any "
                        "ids look auto-generated (long hex strings), "
                        "mention that re-running the workflow will "
                        "overwrite those same chunks idempotently."
                    ),
                    "model": _DEFAULT_MODEL,
                    "outputFormat": "Text",
                    "selectedTools": [],
                    "mcpServerIds": [],
                },
            },
            _end_node(pos_x=960, pos_y=200),
        ],
        "edges": [
            {"id": "e1", "source": "start-1", "target": "vector-db-1"},
            {"id": "e2", "source": "vector-db-1", "target": "agent-report"},
            {"id": "e3", "source": "agent-report", "target": "end-1"},
        ],
    }


# ─── Template 13 — Meeting transcript → action items ────────────────────


def _meeting_transcript_action_items() -> dict[str, Any]:
    """Upload a meeting transcript (Zoom/Teams export, raw notes), get
    back a structured action-item list and a follow-up email draft.

    Demonstrates the document upload primitive in a workflow people
    actually run weekly + JSON-mode agents + downstream consumption
    of structured fields via Mustache substitution."""
    return {
        "name": "Example 13: Meeting Transcript to Action Items",
        "description": (
            "Upload a meeting transcript (PDF / DOCX / MD / TXT). The first "
            "agent extracts action items as structured JSON (owner, task, "
            "due date, priority); the second agent drafts a polished "
            "follow-up email referencing those items inline."
        ),
        "category": "examples",
        "tags": ["example", "intermediate", "documents", "json-output", "productivity"],
        "difficulty": "intermediate",
        "estimatedTime": "3-5 minutes",
        "externalSlug": "template-13-meeting-action-items",
        "nodes": [
            _start_node(
                label="Start",
                inputs=[
                    {
                        "name": "transcript",
                        "type": "document",
                        "required": True,
                        "description": (
                            "Upload the meeting transcript. PDF, DOCX, "
                            "Markdown, or plain text — text gets extracted "
                            "server-side."
                        ),
                    },
                    {
                        "name": "meeting_date",
                        "type": "string",
                        "required": False,
                        "description": "Meeting date (informational, used in the email)",
                        "defaultValue": "today",
                    },
                    {
                        "name": "team_name",
                        "type": "string",
                        "required": False,
                        "description": "Team or project name for the email subject",
                        "defaultValue": "the team",
                    },
                ],
            ),
            {
                "id": "extract-1",
                "type": "agent",
                "position": {"x": 320, "y": 200},
                "data": {
                    "label": "Extract Action Items",
                    "nodeName": "Extract Action Items",
                    "instructions": (
                        "Read this meeting transcript and extract every "
                        "concrete action item:\n\n"
                        "{{transcript}}\n\n"
                        "Return JSON with an `items` array. For each item "
                        "include: owner (the person responsible — use 'TBD' "
                        "if unclear), task (one-sentence description), "
                        "due_date (ISO date if mentioned, else 'unspecified'), "
                        "priority ('high' / 'medium' / 'low' — infer from "
                        "tone and explicit deadlines). Skip discussion "
                        "points that didn't result in a commitment."
                    ),
                    "model": _DEFAULT_MODEL,
                    "outputFormat": "JSON",
                    "jsonSchema": {
                        "type": "object",
                        "properties": {
                            "items": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "owner": {"type": "string"},
                                        "task": {"type": "string"},
                                        "due_date": {"type": "string"},
                                        "priority": {
                                            "type": "string",
                                            "enum": ["high", "medium", "low"],
                                        },
                                    },
                                    "required": ["owner", "task", "priority"],
                                },
                            },
                            "decisions": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Decisions reached without a specific owner/task",
                            },
                        },
                        "required": ["items"],
                    },
                    "selectedTools": [],
                    "mcpServerIds": [],
                },
            },
            {
                "id": "compose-1",
                "type": "agent",
                "position": {"x": 620, "y": 200},
                "data": {
                    "label": "Draft Follow-up Email",
                    "nodeName": "Draft Follow-up Email",
                    "instructions": (
                        "Draft a follow-up email for {{team_name}} after "
                        "the {{meeting_date}} meeting. Use the extracted "
                        "action items below:\n\n"
                        "{{lastOutput}}\n\n"
                        "Structure:\n"
                        "1. One-paragraph recap of the meeting tone\n"
                        "2. Decisions made (bullets) if any\n"
                        "3. Action items grouped by owner — for each, "
                        "show task, due date, and priority\n"
                        "4. Sign-off\n\n"
                        "Markdown format. Don't add any preamble — the "
                        "subject line is the first line, then a blank "
                        "line, then the body."
                    ),
                    "model": _DEFAULT_MODEL,
                    "outputFormat": "Text",
                    "selectedTools": [],
                    "mcpServerIds": [],
                },
            },
            _end_node(pos_x=900),
        ],
        "edges": [
            {"id": "e1", "source": "start-1", "target": "extract-1"},
            {"id": "e2", "source": "extract-1", "target": "compose-1"},
            {"id": "e3", "source": "compose-1", "target": "end-1"},
        ],
    }


# ─── Template 14 — Customer Support Triage ──────────────────────────────


def _customer_support_triage() -> dict[str, Any]:
    """Classify-and-branch pattern on a support ticket.  Pure-LLM, no
    external deps, runs on any fresh instance.

    The if-else branches on whether the ticket needs urgent
    escalation; binary classification keeps the template readable.
    For more categories, designers chain additional if-else nodes
    after the first router."""
    return {
        "name": "Example 14: Customer Support Triage",
        "description": (
            "Classify an incoming support ticket as urgent or standard, "
            "then a specialist agent drafts the appropriate response. "
            "Reference for the classify-and-branch pattern. No external "
            "services — runs on any fresh instance."
        ),
        "category": "examples",
        "tags": ["example", "intermediate", "classification", "branching", "support"],
        "difficulty": "intermediate",
        "estimatedTime": "2-3 minutes",
        "externalSlug": "template-14-support-triage",
        "nodes": [
            _start_node(
                label="Start",
                inputs=[
                    {
                        "name": "ticket",
                        "type": "string",
                        "required": True,
                        "description": "The customer's message verbatim",
                        "defaultValue": (
                            "I've been trying to log in for the past hour "
                            "and keep getting a 500 error. This is "
                            "blocking the entire team from working. We "
                            "have a customer demo in 30 minutes."
                        ),
                    },
                    {
                        "name": "customer_name",
                        "type": "string",
                        "required": False,
                        "description": "Customer's name for personalisation",
                        "defaultValue": "there",
                    },
                ],
            ),
            {
                "id": "classify-1",
                "type": "agent",
                "position": {"x": 320, "y": 220},
                "data": {
                    "label": "Triage Classifier",
                    "nodeName": "Triage Classifier",
                    "instructions": (
                        "Classify this support ticket:\n\n"
                        "{{ticket}}\n\n"
                        "Decide whether it needs urgent escalation. "
                        "Urgent means: production outage, blocking "
                        "multiple users, security concern, or explicit "
                        "time pressure (demo in 30 mins, board meeting, "
                        "etc).  Everything else is standard.\n\n"
                        "Also produce a one-sentence summary the "
                        "downstream agent can use without re-reading "
                        "the full ticket."
                    ),
                    "model": _DEFAULT_MODEL,
                    "outputFormat": "JSON",
                    "jsonSchema": {
                        "type": "object",
                        "properties": {
                            "urgent": {"type": "boolean"},
                            "category": {
                                "type": "string",
                                "enum": [
                                    "outage",
                                    "bug",
                                    "billing",
                                    "feature_request",
                                    "how_to",
                                    "other",
                                ],
                            },
                            "summary": {"type": "string"},
                        },
                        "required": ["urgent", "category", "summary"],
                    },
                    "selectedTools": [],
                    "mcpServerIds": [],
                },
            },
            {
                "id": "if-else-1",
                "type": "if-else",
                "position": {"x": 600, "y": 220},
                "data": {
                    "label": "Urgent?",
                    "nodeName": "Urgent?",
                    # `triage_classifier` is the events_wrapper alias
                    # for the agent above (snake_case of nodeName);
                    # Mustache rewrites to subscript form before eval.
                    "condition": "{{triage_classifier.urgent}}",
                },
            },
            {
                "id": "agent-urgent",
                "type": "agent",
                "position": {"x": 880, "y": 100},
                "data": {
                    "label": "Urgent Specialist",
                    "nodeName": "Urgent Specialist",
                    "instructions": (
                        "Draft an urgent-response message for "
                        "{{customer_name}}.  Acknowledge the situation "
                        "in the first line, escalate explicitly, and "
                        "give a concrete next step the customer can "
                        "take while support investigates.\n\n"
                        "Ticket summary: {{triage_classifier.summary}}\n"
                        "Category: {{triage_classifier.category}}\n\n"
                        "Tone: empathetic but professional. Don't "
                        "promise specific resolution times you can't "
                        "verify. Keep it under 150 words."
                    ),
                    "model": _DEFAULT_MODEL,
                    "outputFormat": "Text",
                    "selectedTools": [],
                    "mcpServerIds": [],
                },
            },
            {
                "id": "agent-standard",
                "type": "agent",
                "position": {"x": 880, "y": 340},
                "data": {
                    "label": "Standard Reply",
                    "nodeName": "Standard Reply",
                    "instructions": (
                        "Draft a friendly response for {{customer_name}}.\n\n"
                        "Ticket summary: {{triage_classifier.summary}}\n"
                        "Category: {{triage_classifier.category}}\n\n"
                        "Acknowledge their question, give the most "
                        "useful answer or next step, and offer to "
                        "follow up if the suggestion doesn't work. "
                        "Keep it under 150 words."
                    ),
                    "model": _DEFAULT_MODEL,
                    "outputFormat": "Text",
                    "selectedTools": [],
                    "mcpServerIds": [],
                },
            },
            _end_node(pos_x=1160, pos_y=220),
        ],
        "edges": [
            {"id": "e1", "source": "start-1", "target": "classify-1"},
            {"id": "e2", "source": "classify-1", "target": "if-else-1"},
            {
                "id": "e3-true",
                "source": "if-else-1",
                "target": "agent-urgent",
                "sourceHandle": "true",
                "branch": "true",
                "label": "true",
            },
            {
                "id": "e3-false",
                "source": "if-else-1",
                "target": "agent-standard",
                "sourceHandle": "false",
                "branch": "false",
                "label": "false",
            },
            {"id": "e4", "source": "agent-urgent", "target": "end-1"},
            {"id": "e5", "source": "agent-standard", "target": "end-1"},
        ],
    }


# ─── Template 15 — Gamma AI Presentation Generator ──────────────────────


def _gamma_ai_presentation() -> dict[str, Any]:
    """Topic in, slide deck out.  Demonstrates the gamma-ai node
    (which had no template coverage before) chained with a research
    agent that produces the source outline."""
    return {
        "name": "Example 15: Gamma AI Presentation Generator",
        "description": (
            "Type in a topic, get back a Gamma-hosted slide deck. The "
            "research agent uses Tavily to ground the outline in current "
            "info; the gamma-ai node renders slides from the outline. "
            "Requires GAMMA_API_KEY (and TAVILY_API_KEY for the research "
            "step)."
        ),
        "category": "examples",
        "tags": ["example", "advanced", "gamma-ai", "presentations", "tavily"],
        "difficulty": "advanced",
        "estimatedTime": "3-5 minutes (Gamma generation can take 60-90s)",
        "externalSlug": "template-15-gamma-ai-presentation",
        "nodes": [
            _start_node(
                label="Start",
                inputs=[
                    {
                        "name": "topic",
                        "type": "string",
                        "required": True,
                        "description": "What's the deck about?",
                        "defaultValue": "How agentic AI changes knowledge work in 2026",
                    },
                    {
                        "name": "audience",
                        "type": "string",
                        "required": False,
                        "description": "Who's the audience? Drives tone + depth.",
                        "defaultValue": "executives at a mid-size tech company",
                    },
                    {
                        "name": "num_slides",
                        "type": "number",
                        "required": False,
                        "description": "Approximate slide count",
                        "defaultValue": 8,
                    },
                ],
            ),
            {
                "id": "research-1",
                "type": "agent",
                "position": {"x": 300, "y": 200},
                "data": {
                    "label": "Research + Outline",
                    "nodeName": "Research + Outline",
                    "instructions": (
                        "Research this topic and produce a slide outline "
                        "tailored for the audience.\n\n"
                        "Topic: {{topic}}\n"
                        "Audience: {{audience}}\n"
                        "Target slide count: {{num_slides}}\n\n"
                        "1. Use tavily_search to gather current "
                        "information on the topic (focus on 2025-2026).\n"
                        "2. Synthesise the findings into a slide "
                        "outline. Each slide gets one line of title and "
                        "2-4 supporting bullets.\n"
                        "3. Return the outline as plain markdown — "
                        "Gamma turns this into slides verbatim, so "
                        "structure matters more than prose."
                    ),
                    "model": _DEFAULT_MODEL,
                    "outputFormat": "Text",
                    "selectedTools": ["tavily.tavily_search"],
                    "mcpServerIds": [],
                },
            },
            {
                "id": "gamma-1",
                "type": "gamma-ai",
                "position": {"x": 600, "y": 200},
                "data": {
                    "label": "Generate Deck",
                    "nodeName": "Generate Deck",
                    # Gamma reads the prompt as the outline source.
                    "prompt": "{{lastOutput}}",
                    "format": "presentation",
                    # Preserve the outline verbatim — the research
                    # agent already structured it for slides.
                    "textMode": "preserve",
                    "numCards": 8,
                    "textAmount": "medium",
                    "imageSource": "aiGenerated",
                    "exportAs": "web",
                },
            },
            {
                "id": "report-1",
                "type": "agent",
                "position": {"x": 900, "y": 200},
                "data": {
                    "label": "Summarise Output",
                    "nodeName": "Summarise Output",
                    "instructions": (
                        "Format the Gamma generation result for the user.\n\n"
                        "Result: {{lastOutput}}\n\n"
                        "Surface the URL prominently if there is one. "
                        "Mention the topic ({{topic}}) and audience "
                        "({{audience}}) so the user has context when "
                        "they share the deck. If the result includes "
                        "an `error` or `status: failed`, explain what "
                        "happened in plain language."
                    ),
                    "model": _DEFAULT_MODEL,
                    "outputFormat": "Text",
                    "selectedTools": [],
                    "mcpServerIds": [],
                },
            },
            _end_node(pos_x=1180),
        ],
        "edges": [
            {"id": "e1", "source": "start-1", "target": "research-1"},
            {"id": "e2", "source": "research-1", "target": "gamma-1"},
            {"id": "e3", "source": "gamma-1", "target": "report-1"},
            {"id": "e4", "source": "report-1", "target": "end-1"},
        ],
    }


# ─── Template 16 — Code Review Assistant ────────────────────────────────


def _code_review_assistant() -> dict[str, Any]:
    """Paste a diff, get structured review with severity-tagged issues.
    Guardrails layer screens the OUTPUT for accidental PII/secret leaks
    (e.g. the diff contained an API key the review agent surfaced
    verbatim) before delivery.

    Demonstrates: agent JSON output with schema, guardrails on
    downstream content (not upstream), branched delivery on the
    guardrails verdict."""
    return {
        "name": "Example 16: Code Review Assistant",
        "description": (
            "Paste a diff (or upload a patch file). A review agent flags "
            "bugs / security issues / style problems with severity tags "
            "and structured JSON. A guardrails layer screens the output "
            "for PII or credential leaks before delivery; if the review "
            "would expose a secret, the workflow returns a redacted "
            "warning instead."
        ),
        "category": "examples",
        "tags": ["example", "advanced", "code-review", "guardrails", "json-output"],
        "difficulty": "advanced",
        "estimatedTime": "2-4 minutes",
        "externalSlug": "template-16-code-review",
        "nodes": [
            _start_node(
                label="Start",
                inputs=[
                    {
                        "name": "diff",
                        "type": "string",
                        "required": True,
                        "description": "Unified diff (paste from `git diff`)",
                        "defaultValue": (
                            "diff --git a/auth.py b/auth.py\n"
                            "--- a/auth.py\n"
                            "+++ b/auth.py\n"
                            "@@ -10,7 +10,7 @@\n"
                            " def verify_password(plain, hashed):\n"
                            "-    return bcrypt.checkpw(plain.encode(), hashed.encode())\n"
                            "+    return plain == hashed.decode()\n"
                        ),
                    },
                    {
                        "name": "language",
                        "type": "string",
                        "required": False,
                        "description": "Programming language (helps the reviewer)",
                        "defaultValue": "Python",
                    },
                ],
            ),
            {
                "id": "review-1",
                "type": "agent",
                "position": {"x": 300, "y": 220},
                "data": {
                    "label": "Code Reviewer",
                    "nodeName": "Code Reviewer",
                    "instructions": (
                        "Review this {{language}} diff for issues:\n\n"
                        "{{diff}}\n\n"
                        "Categorise each issue by severity:\n"
                        "  - critical: security vulnerability, data loss, "
                        "or correctness bug that ships obviously broken code\n"
                        "  - high: significant bug or anti-pattern\n"
                        "  - medium: style or maintainability concern\n"
                        "  - low: nitpick / preference\n\n"
                        "Return JSON.  Include line references (line "
                        "numbers from the diff hunks) where applicable. "
                        "Be specific in descriptions — quote the offending "
                        "code snippet rather than describing it abstractly. "
                        "If the diff looks clean, return an empty issues "
                        "array with a brief summary noting that."
                    ),
                    "model": _DEFAULT_MODEL,
                    "outputFormat": "JSON",
                    "jsonSchema": {
                        "type": "object",
                        "properties": {
                            "issues": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "severity": {
                                            "type": "string",
                                            "enum": ["critical", "high", "medium", "low"],
                                        },
                                        "line": {"type": "string"},
                                        "snippet": {"type": "string"},
                                        "description": {"type": "string"},
                                        "suggestion": {"type": "string"},
                                    },
                                    "required": ["severity", "description"],
                                },
                            },
                            "summary": {"type": "string"},
                            "ship_recommendation": {
                                "type": "string",
                                "enum": ["approve", "request_changes", "block"],
                            },
                        },
                        "required": ["issues", "summary", "ship_recommendation"],
                    },
                    "selectedTools": [],
                    "mcpServerIds": [],
                },
            },
            {
                "id": "guardrails-1",
                "type": "guardrails",
                "position": {"x": 600, "y": 220},
                "data": {
                    "label": "PII / Secret Screen",
                    "nodeName": "PII Screen",
                    # PII catches credit cards, SSNs, emails surfaced
                    # in code snippets the review verbatim quoted.
                    "piiEnabled": True,
                    "moderationEnabled": False,
                    "jailbreakEnabled": False,
                    "hallucinationEnabled": False,
                    "actionOnViolation": "warn",
                    "model": _DEFAULT_MODEL,
                },
            },
            {
                "id": "if-else-1",
                "type": "if-else",
                "position": {"x": 880, "y": 220},
                "data": {
                    "label": "Safe to deliver?",
                    "nodeName": "Safe to deliver?",
                    "condition": "{{pii_screen.passed}}",
                },
            },
            {
                "id": "deliver-1",
                "type": "agent",
                "position": {"x": 1160, "y": 100},
                "data": {
                    "label": "Format Review",
                    "nodeName": "Format Review",
                    "instructions": (
                        "Format the review for the developer.\n\n"
                        "Structured review: {{code_reviewer}}\n\n"
                        "Output a clean markdown report:\n"
                        "1. Ship recommendation in bold at the top\n"
                        "2. Summary paragraph\n"
                        "3. Issues grouped by severity (critical → low)\n"
                        "4. For each issue: line reference, code snippet, "
                        "the problem, and a concrete suggested fix"
                    ),
                    "model": _DEFAULT_MODEL,
                    "outputFormat": "Text",
                    "selectedTools": [],
                    "mcpServerIds": [],
                },
            },
            {
                "id": "redact-1",
                "type": "agent",
                "position": {"x": 1160, "y": 340},
                "data": {
                    "label": "Redacted Warning",
                    "nodeName": "Redacted Warning",
                    "instructions": (
                        "The code review surfaced sensitive content "
                        "(PII or possibly a credential) verbatim from "
                        "the diff. Don't deliver the raw review.\n\n"
                        "Violations: {{pii_screen.violations}}\n\n"
                        "Tell the developer in 2-3 sentences that the "
                        "diff appears to contain sensitive data, that "
                        "the review has been withheld to avoid "
                        "exfiltration, and recommend they redact the "
                        "diff and re-run."
                    ),
                    "model": _DEFAULT_MODEL,
                    "outputFormat": "Text",
                    "selectedTools": [],
                    "mcpServerIds": [],
                },
            },
            _end_node(pos_x=1440, pos_y=220),
        ],
        "edges": [
            {"id": "e1", "source": "start-1", "target": "review-1"},
            {"id": "e2", "source": "review-1", "target": "guardrails-1"},
            {"id": "e3", "source": "guardrails-1", "target": "if-else-1"},
            {
                "id": "e4-true",
                "source": "if-else-1",
                "target": "deliver-1",
                "sourceHandle": "true",
                "branch": "true",
                "label": "safe",
            },
            {
                "id": "e4-false",
                "source": "if-else-1",
                "target": "redact-1",
                "sourceHandle": "false",
                "branch": "false",
                "label": "redacted",
            },
            {"id": "e5", "source": "deliver-1", "target": "end-1"},
            {"id": "e6", "source": "redact-1", "target": "end-1"},
        ],
    }


# ─── Template 17 — Lead Enrichment ──────────────────────────────────────


def _lead_enrichment() -> dict[str, Any]:
    """Sales/BD use case: company name in, structured profile out.

    Multi-source research (Tavily for current news, Firecrawl for
    the company's own website) flowing into a structured-extraction
    agent.  Output is JSON shaped for direct CRM import."""
    return {
        "name": "Example 17: Lead Enrichment",
        "description": (
            "Company name in → structured profile out. The research "
            "agent pulls signals from Tavily web search and the "
            "company's own site (Firecrawl), then a second agent "
            "shapes the findings into a JSON record ready for CRM "
            "import. Requires TAVILY_API_KEY and FIRECRAWL_API_KEY."
        ),
        "category": "examples",
        "tags": ["example", "advanced", "tavily", "firecrawl", "json-output", "sales"],
        "difficulty": "advanced",
        "estimatedTime": "3-5 minutes",
        "externalSlug": "template-17-lead-enrichment",
        "nodes": [
            _start_node(
                label="Start",
                inputs=[
                    {
                        "name": "company_name",
                        "type": "string",
                        "required": True,
                        "description": "Company to research",
                        "defaultValue": "Anthropic",
                    },
                    {
                        "name": "company_url",
                        "type": "string",
                        "required": False,
                        "description": (
                            "Company website. Leave blank to let the "
                            "researcher infer it from search results."
                        ),
                        "defaultValue": "https://www.anthropic.com",
                    },
                ],
            ),
            {
                "id": "research-1",
                "type": "agent",
                "position": {"x": 300, "y": 200},
                "data": {
                    "label": "Multi-source Researcher",
                    "nodeName": "Multi-source Researcher",
                    "instructions": (
                        "Build a profile of {{company_name}}.\n\n"
                        "1. Run tavily_search for "
                        '"{{company_name}} company products" and '
                        '"{{company_name}} recent news 2026" to gather '
                        "general info and recent developments.\n"
                        "2. If {{company_url}} is non-empty, also call "
                        "firecrawl_scrape on it to capture the "
                        "company's own positioning.\n"
                        "3. Synthesise everything into a research dump "
                        "covering: industry, headquarters, employee "
                        "size estimate, key products / services, "
                        "target customers, recent news (last 6 months), "
                        "key executives, competitors. Return prose — "
                        "the next step structures it into JSON."
                    ),
                    "model": _DEFAULT_MODEL,
                    "outputFormat": "Text",
                    "selectedTools": [
                        "tavily.tavily_search",
                        "firecrawl.firecrawl_scrape",
                    ],
                    "mcpServerIds": [],
                },
            },
            {
                "id": "structure-1",
                "type": "agent",
                "position": {"x": 600, "y": 200},
                "data": {
                    "label": "Shape into CRM Record",
                    "nodeName": "Shape into CRM Record",
                    "instructions": (
                        "Reshape this research into a structured CRM "
                        "record:\n\n"
                        "{{lastOutput}}\n\n"
                        "Use 'unknown' for fields the research didn't "
                        "establish — don't guess.  Lists with no "
                        "evidence stay empty.  Conservative is better "
                        "than confident-and-wrong here."
                    ),
                    "model": _DEFAULT_MODEL,
                    "outputFormat": "JSON",
                    "jsonSchema": {
                        "type": "object",
                        "properties": {
                            "company_name": {"type": "string"},
                            "industry": {"type": "string"},
                            "headquarters": {"type": "string"},
                            "size_estimate": {
                                "type": "string",
                                "description": "e.g. '50-200 employees', 'unknown'",
                            },
                            "products": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "target_market": {"type": "string"},
                            "recent_news": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "headline": {"type": "string"},
                                        "date": {"type": "string"},
                                    },
                                    "required": ["headline"],
                                },
                            },
                            "key_executives": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "role": {"type": "string"},
                                    },
                                    "required": ["name"],
                                },
                            },
                            "competitors": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "summary": {
                                "type": "string",
                                "description": "Two-sentence elevator pitch",
                            },
                        },
                        "required": ["company_name", "summary"],
                    },
                    "selectedTools": [],
                    "mcpServerIds": [],
                },
            },
            _end_node(pos_x=900),
        ],
        "edges": [
            {"id": "e1", "source": "start-1", "target": "research-1"},
            {"id": "e2", "source": "research-1", "target": "structure-1"},
            {"id": "e3", "source": "structure-1", "target": "end-1"},
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
    _while_loop_demo(),
    _rag_with_vector_db(),
    _document_ingestion(),
    _meeting_transcript_action_items(),
    _customer_support_triage(),
    _gamma_ai_presentation(),
    _code_review_assistant(),
    _lead_enrichment(),
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
