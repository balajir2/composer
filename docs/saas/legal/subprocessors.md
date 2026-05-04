# Sub-processors

> **Maintained by:** Balaji Rajan (`balajirajan@gmail.com`)
> **Effective:** {{EFFECTIVE_DATE}}
> **Last updated:** {{LAST_UPDATED}}
> **Notification contact:** `balajirajan@gmail.com`

This document is the canonical list of third parties ("**sub-processors**") that {{LEGAL_ENTITY}} engages to process Customer Data in connection with the Composer service. New sub-processors are added per the notification protocol in the [Data Processing Addendum](data-processing-addendum.md) and the [SaaS docs README](../README.md). The operational discussion of why each sub-processor exists and what data flows through it is in [`../privacy.md`](../privacy.md).

Customers can subscribe to notifications of changes by:

- Watching this file in the [GitHub repository](https://github.com/balajir2/composer)
- Emailing the notification contact above to be added to the change-notification mailing list

A new sub-processor takes effect no earlier than **30 days** after notification.

## How to read the table

- **Service** is the sub-processor's offering, not their corporate name.
- **Provider** is the corporate entity behind the service.
- **Purpose** is what we use them for.
- **Data flow** describes what data flows to them.
- **Region** is where the data physically resides for our standard managed deployments. Customers with custom data-residency requirements get a region set per the contract.
- **Engagement** is whether the sub-processor is core (always engaged) or opt-in (engaged only if Customer enables the relevant feature or workflow).

## Core infrastructure sub-processors

These run the platform regardless of Customer's specific workflow choices.

| Service | Provider | Purpose | Data flow | Region | Engagement |
|---|---|---|---|---|---|
| Neon | Neon, Inc. | Managed Postgres database | All persisted Customer Data | EU / US / APAC per deployment | Core |
| Vercel | Vercel, Inc. | Frontend hosting | Customer Data passing through the canvas / runs / admin UIs | Per deployment region | Core |
| {{BACKEND_HOST_NAME}} | {{BACKEND_HOST_VENDOR}} | Backend hosting (FastAPI + WebSocket) | All Customer Data | Per deployment region | Core |
| GitHub | GitHub, Inc. | Source code, CI, release artefacts | Code only — not Customer Data | US | Core |
| Cloudflare | Cloudflare, Inc. | DNS + edge protection | Request metadata only | Global edge | Core |

## LLM and embedding sub-processors

Engaged when Customer's workflows use the corresponding provider.

| Service | Provider | Purpose | Data flow | Region | Engagement |
|---|---|---|---|---|---|
| Claude | Anthropic, PBC | LLM inference (agent + structured-output nodes) | Prompt + tool definitions for each agent invocation that targets Anthropic | US | Opt-in (enabled by Customer providing API key) |
| OpenAI API | OpenAI, L.L.C. | LLM inference; embeddings for vector-DB upsert | Same as Claude where Customer targets OpenAI; embedding text where vector-DB upsert is used | US | Opt-in |
| Gemini | Google LLC | LLM inference | Same as above where Customer targets Gemini | US (default) | Opt-in |
| Groq | Groq, Inc. | LLM inference (low-latency hosted models) | Same as above where Customer targets Groq | US | Opt-in |

## Tool and integration sub-processors

Engaged when Customer enables the corresponding tool in a workflow.

| Service | Provider | Purpose | Data flow | Region | Engagement |
|---|---|---|---|---|---|
| Tavily | Tavily AI | Web search | Search query Customer's workflow specifies | US | Opt-in |
| Firecrawl | Firecrawl | Web scraping | URL Customer's workflow specifies | US | Opt-in |
| Serper | Serper.dev | Google-search proxy | Search query Customer's workflow specifies | US | Opt-in |
| Browserless | Browserless.io | Headless Chrome rendering | URL + DOM operations Customer's workflow specifies | US / EU per Customer's choice | Opt-in |
| Gamma | Gamma App, Inc. | Slide deck rendering | Outline content the workflow generates | US | Opt-in |
| Arcade | Arcade, Inc. | External tool execution platform | Tool-call payloads for Arcade tools the workflow uses | US | Opt-in |

## Vector database sub-processors

Engaged when Customer connects a vector DB to a workflow.

| Service | Provider | Purpose | Data flow | Region | Engagement |
|---|---|---|---|---|---|
| Pinecone | Pinecone Systems, Inc. | Vector storage + similarity search | Vector embeddings + Customer-supplied metadata | Per Customer's index region | Opt-in |
| Qdrant Cloud | Qdrant Solutions GmbH | Same | Same | Per Customer's index region | Opt-in |
| Chroma | Chroma | Same | Same | Per Customer's index region | Opt-in |
| Weaviate | Weaviate B.V. | Same | Same | Per Customer's index region | Opt-in |
| Milvus / Zilliz | Zilliz Inc. | Same | Same | Per Customer's index region | Opt-in |

## MCP servers

MCP servers are configured by Customer's admin per workflow needs. The list of authorised MCP servers for a given Customer is whatever appears in that Customer's Admin → MCP servers UI. Each MCP server is a sub-processor in respect of the data the Customer's workflows send to it; Customer's own due diligence on each server applies.

We list known third-party MCP servers Customer admins commonly enable; this is not an exhaustive list.

| Server | Provider | Purpose | Data flow | Engagement |
|---|---|---|---|---|
| Highspot MCP | Highspot, Inc. | Sales-content lookup | OAuth-bound Customer access to their own Highspot data | Opt-in via Admin |
| DeepWiki MCP | Cognition Labs (DeepWiki) | Repository documentation | Customer-supplied query | Opt-in via Admin |
| Firecrawl MCP | Firecrawl | Web scraping (alternative MCP path) | Customer-supplied URL | Opt-in via Admin |

## Optional observability sub-processors

Engaged when Customer enables the integration.

| Service | Provider | Purpose | Data flow | Region | Engagement |
|---|---|---|---|---|---|
| LangSmith | LangChain, Inc. | Per-execution tracing of LLM + tool calls | Full prompt / response / tool-call trace per execution | US | Opt-in (`LANGCHAIN_TRACING_V2=true`) |

## Sub-processors we do not engage

For clarity:

- **No advertising or marketing analytics platforms.** Composer ships with no Google Analytics, Segment, Pendo, Hotjar, or similar.
- **No customer support analytics inside the product.** If we use a help-desk tool (Zendesk / Linear / etc.) for support tickets, it processes the ticket content you submit; it doesn't see your application data automatically.
- **No payments processor in the product itself.** Payment for the Service is via invoicing per the Order; no card data is collected through the Service.

## How to verify the current state

The current authoritative state lives in this file in the `main` branch of the GitHub repository. The corresponding configuration in code lives at:

- LLM provider list: [`../../../src/llm/providers.py`](../../../src/llm/providers.py)
- Tool provider registrations: [`../../../src/tools/providers/`](../../../src/tools/providers/)
- Vector DB provider registrations: [`../../../src/vectordb/providers/`](../../../src/vectordb/providers/)
- MCP integration: [`../../../src/mcp/`](../../../src/mcp/)

If something is in the code but not in this list, treat it as a documentation bug and report to balajirajan@gmail.com.

## Change history

| Date | Change | Notification sent |
|---|---|---|
| {{INITIAL_DATE}} | Initial publication | n/a |

<!-- Update this table whenever a new sub-processor is added or removed. The table is the audit trail customers can rely on. -->
