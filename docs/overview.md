# Composer — Product Overview

Composer is a visual platform for designing, governing, and operating agentic AI workflows. It
combines deterministic workflow logic, LLM reasoning, enterprise tools, human approval, durable
state, and published APIs in one inspectable system.

## Product status

Composer is an **early commercial product** with an operational backend, visual Designer, Runs and
Approvals experience, administration console, production invocation API, and 19 reference
templates. The current repository collects 925 backend tests across unit, regression, and
integration suites.

The strongest deployment shape today is dedicated single-tenant or self-hosted. Work required for
a scaled shared managed service—durable workers, distributed events/rate limits, stronger
idempotency, credential centralization, and framework modernization—is tracked openly in the
[Improvement Backlog](claude-improvement-backlog.md).

## Who Composer serves

| Audience | What Composer gives them |
|---|---|
| Workflow designers | A canvas for assembling agents, tools, data operations, control flow, approvals, and delivery. |
| Business reviewers | A focused place to inspect work and approve or reject consequential steps. |
| Application developers | Published synchronous/asynchronous APIs and WebSocket execution events. |
| Platform administrators | User, role, model, key, MCP connection, deployment, and execution management. |
| Security and operations teams | Explicit auth boundaries, encrypted key storage, execution records, runbooks, and deployment controls. |

## The product loop

1. **Author** a typed graph on the visual canvas.
2. **Configure** models, tools, credentials, variables, and guardrails.
3. **Test** the draft with real inputs and node-level execution feedback.
4. **Pause and review** using persisted human-approval checkpoints when required.
5. **Publish** the workflow as an authenticated production endpoint.
6. **Operate** it through execution history, live events, logs, and optional LangSmith traces.

## What you can build

- Requirements intake → structured BRD → human approval → Jira work items → notification.
- RAG ingestion and retrieval across Pinecone, Qdrant, Chroma, Weaviate, or Milvus.
- Multi-source research using search, scraping, browser automation, and MCP knowledge systems.
- Support or operations triage with classification, guardrails, branching, and specialist agents.
- Document processing that turns PDF, DOCX, Markdown, or text into structured business output.
- Approval-controlled email and external-system actions.
- Presentation generation through Gamma.
- Published AI workflow APIs embedded in existing products.

## Capability summary

| Area | Shipped capability |
|---|---|
| Workflow design | 20 Designer node types, variables, aliases, branching, loops, autosave, templates, draft runs. |
| AI models | Anthropic, OpenAI, Google, and Groq families; model catalogue and verification; structured output. |
| Enterprise tools | MCP with static/OAuth auth, Jira, HTTP, email, Arcade, Gamma, search, scrape, and browser providers. |
| Data | Five vector DBs, ingestion/upsert, retrieval, embeddings, transforms, structured extraction, document upload. |
| Human oversight | Checkpointed approve/reject steps, in-app decisions, emailed decision links, approval records and expiry. |
| Product integration | Published workflow endpoints, per-user API keys, sync/async invocation, WebSocket events. |
| Administration | Users, roles, sharing, models, encrypted LLM keys, MCP servers, deployment settings, run history. |
| Operations | PostgreSQL checkpoints, stuck-run cleanup, logs, LangSmith integration, deployment and incident runbooks. |

See [Product Capabilities](product-capabilities.md) and the [Designer Guide](designer-guide.md) for
the complete feature catalogue.

## Why Composer is differentiated

### It is workflow-first

Composer does not hide the application inside a prompt. LLM nodes participate in a visible graph
alongside deterministic conditions, bounded loops, transformations, safety checks, approvals, and
delivery actions.

### Human decisions are durable state

Approval is implemented through LangGraph interrupts and PostgreSQL checkpoints. A workflow can
wait without holding compute and resume later with its state intact.

### MCP is part of the platform, not a demo

Composer includes server administration, tool discovery, static and OAuth authentication,
per-user tokens, shared connections, and response sanitization around the Model Context Protocol.

### It is inspectable and deployable

The code, API, schema, tests, architecture decisions, security posture, and operating runbooks are
available in the repository. Prospects can perform technical diligence before committing to a
deployment model.

## Architecture

| Layer | Current choice |
|---|---|
| Backend | Python 3.11/3.12, FastAPI, Pydantic |
| Workflow runtime | LangGraph and LangChain |
| Persistence | PostgreSQL with a Prisma Python client and custom LangGraph checkpointer |
| Frontend | Next.js App Router, React, Tailwind, shadcn/ui, React Flow |
| Authentication | NextAuth, Composer JWTs, Azure AD/standalone modes, per-user API keys |
| Real-time | WebSocket execution events |
| Security primitives | AES-256-GCM, bcrypt, sandboxed expressions, E2B code execution |
| Observability | Structured logs and optional LangSmith traces |

The architecture is sound for the product, with two modernization priorities: replace the archived
Prisma Client Python dependency over time and move the frontend to supported Next.js/React/React
Flow lines. See [Architecture](architecture.md) and [Engineering Decisions](decisions.md).

## Evidence and diligence

- [Investor Overview](investor-overview.md) — market/product narrative and diligence index.
- [Product Capabilities](product-capabilities.md) — shipped feature catalogue.
- [Architecture](architecture.md) — runtime and data flow.
- [API Reference](api-reference.md) — integration contract.
- [Security](saas/security.md) and [Compliance](saas/compliance.md) — posture and current limits.
- [Changelog](../CHANGELOG.md) — shipped work.
- [Roadmap](saas/roadmap.md) — directional future work.
- [Improvement Backlog](claude-improvement-backlog.md) — known engineering risks and acceptance criteria.

## Where to go next

- Build locally: [Getting Started](getting-started.md).
- Learn the canvas: [Designer Guide](designer-guide.md).
- Deploy and operate: [Operations](operations.md).
- Evaluate a commercial deployment: [SaaS Overview](saas/overview.md).
