# Composer — Investor Overview

> **Purpose:** a concise, evidence-backed introduction for investors, strategic partners, and
> enterprise buyers. Product capabilities described as shipped are traceable to this repository.
> Commercial plans and roadmap dates are directional unless supported by a signed agreement.

## The one-line thesis

Composer is an open-source visual control plane for building, governing, and operating agentic AI
workflows: teams assemble deterministic business logic, LLM reasoning, external tools, human
approvals, and enterprise data access on one canvas, then publish the result as a monitored API.

## The problem

Moving an AI prototype into a dependable business process usually requires teams to assemble the
same infrastructure repeatedly:

- Prompt and model configuration across multiple providers.
- Tool calling and enterprise-system authentication.
- Durable state, retries, branching, loops, and long-running work.
- Human approval before consequential actions.
- Secret management, role-based access, and auditability.
- Deployment APIs, live execution visibility, and operational runbooks.

The result is often a collection of scripts that demonstrates value quickly but becomes difficult
to govern, explain, and operate. Composer turns those recurring concerns into a reusable platform.

## The product

Composer provides three connected surfaces:

| Surface | User | Outcome |
|---|---|---|
| **Visual Designer** | AI engineers, automation teams, technical business users | Build and test workflows using typed nodes, variables, branching, loops, tools, and approvals. |
| **Runs & Approvals** | Operators, reviewers, end users | Submit work, follow node-level progress, inspect results, and resolve human decisions. |
| **Administration & APIs** | Platform teams, administrators, application developers | Manage users, models, keys, MCP connections, published endpoints, and execution history. |

A workflow can start as a visual draft, pause for review, resume from a persisted checkpoint, and
be published as `POST /api/run/{slug}` for another application to invoke synchronously or
asynchronously.

## What is shipped today

Repository evidence currently includes:

- **20 workflow node types** spanning AI, integration, data flow, control flow, safety, and delivery.
- **4 first-class LLM provider families:** Anthropic, OpenAI, Google, and Groq, with an extensible
  model catalogue and additional OpenAI-compatible provider support.
- **5 vector database connectors:** Pinecone, Qdrant, Chroma, Weaviate, and Milvus, supporting
  retrieval and ingestion/upsert paths.
- **Model Context Protocol support** for static and OAuth-connected MCP servers, including shared
  server configuration and per-user OAuth tokens.
- **Human-in-the-loop execution** using LangGraph interrupts and PostgreSQL checkpoints.
- **Jira, email, HTTP, Gamma, Arcade, search, scraping, browser, and MCP integrations.**
- **Document intake** for PDF, DOCX, Markdown, and text.
- **Published workflow APIs**, per-user API keys, WebSocket execution events, workflow sharing,
  standalone authentication, and Azure AD SSO support.
- **19 reference workflow templates** and **925 collected backend tests** in the current tree.
- Operational, security, privacy, compliance, deployment, incident-response, and disaster-recovery
  documentation intended to support enterprise evaluation.

For the complete capability map, see [Product Capabilities](product-capabilities.md). For operating
details, see [Architecture](architecture.md), [Security](saas/security.md), and
[Production Deployment](operations/production-deployment.md).

## Representative use cases

| Use case | Why Composer matters |
|---|---|
| **Requirements to delivery** | Turn source documents into structured requirements, require approval, create Jira work, and notify stakeholders. |
| **Enterprise research** | Combine search, scraping, MCP knowledge sources, structured extraction, and source-aware synthesis. |
| **RAG and document intelligence** | Extract documents, ingest or query vector stores, join evidence, and generate grounded output. |
| **Operational triage** | Classify incoming work, apply guardrails, branch to specialists, and preserve an execution record. |
| **Controlled content distribution** | Draft with an LLM, pause for human approval, then deliver through email or an external API. |
| **Presentation generation** | Research a topic, structure the narrative, generate a Gamma deck, and surface the result. |

## Differentiation

### Workflow-first, not chatbot-first

Composer treats an AI application as an explicit state machine. LLMs are powerful nodes inside a
larger process rather than the process itself. Deterministic branches, bounded loops, transforms,
guardrails, approvals, and delivery steps remain visible and reviewable.

### Enterprise connection through MCP

MCP support lets Composer connect to an expanding ecosystem of tools and knowledge systems without
hard-coding every integration into the core product. OAuth, shared connections, and response
sanitisation address operational concerns beyond a basic MCP demo.

### Durable human decisions

Approval is part of the execution model. A run can checkpoint, wait without holding compute, and
resume later—even after a process restart—while preserving its state and decision trail.

### Open and deployable

The repository includes the application, infrastructure guidance, API documentation, operating
runbooks, and customer-facing policy templates. Customers can evaluate the implementation and
choose self-hosted or dedicated deployment models without placing workflow definitions behind a
proprietary black box.

### Built from operational lessons

Composer is a Python rebuild of an earlier TypeScript/Convex product, Open Agent Builder. The
rebuild preserved behavioral lessons while moving to FastAPI, PostgreSQL, and LangGraph. Historical
design and incident material remains in [`archive/`](archive/) for traceability.

## Architecture at a glance

```text
Designer / Runs / Admin (Next.js)
              |
        FastAPI control plane
              |
       LangGraph execution engine
        /          |           \
 PostgreSQL    LLM providers    Tools / MCP / Jira / Vector DBs
 checkpoints
```

The architectural center is LangGraph: it provides the state-machine and interrupt/resume model;
Composer adds visual authoring, typed node contracts, provider management, persistence adapters,
authentication, operational surfaces, and deployment APIs around it.

## Business model direction

The code is open source and can be self-hosted. The commercial opportunity is in reducing the cost
and risk of operating agentic workflows for organizations that prefer a supported, dedicated, or
enterprise-integrated deployment.

Potential commercial layers include:

- Managed single-tenant environments.
- Enterprise support and operational commitments.
- SSO, data-residency, network, and security integration.
- Custom nodes, MCP connections, and workflow implementation services.
- Governance, analytics, versioning, promotion, and compliance capabilities.

The current pricing document is a commercial design template, not evidence of contracted revenue.
See [Pricing](saas/pricing.md) for the proposed packaging model.

## Product maturity and current priorities

Composer is a functioning end-to-end product with broad feature coverage and substantial automated
tests. It should be presented as an early commercial platform—not as a finished hyperscale SaaS.

The highest-value engineering priorities are documented transparently in
[Claude Improvement Backlog](claude-improvement-backlog.md), including:

- Stronger frontend/backend node contract testing.
- Durable background workers and idempotent side effects.
- Centralized credential management and outbound-request hardening.
- Multi-instance events and rate limiting.
- Migration away from archived Prisma Client Python.
- Upgrades from unsupported frontend framework versions.

This transparency is intentional: technical diligence is stronger when shipped capability,
known risk, and future investment are clearly separated.

## Investment narrative

Composer demonstrates four assets that are expensive to assemble together:

1. **A broad, working product surface** across authoring, execution, integration, administration,
   and operations.
2. **A reusable execution architecture** for deterministic and agentic business processes.
3. **Enterprise-oriented implementation depth** in auth, checkpoints, MCP OAuth, secrets,
   observability, and deployment documentation.
4. **A credible expansion path** from self-hosted/dedicated deployments toward governance,
   analytics, workflow lifecycle management, and managed enterprise operations.

The fundraising question is therefore not whether a visual workflow proof of concept can be built;
this repository already answers that. The question is how quickly the platform can convert its
technical breadth into repeatable customer outcomes, durable operations, and focused distribution.

## Diligence index

| Question | Evidence |
|---|---|
| What does the product do? | [Product Capabilities](product-capabilities.md), [Designer Guide](designer-guide.md) |
| How is it built? | [Architecture](architecture.md), [Engineering Decisions](decisions.md) |
| Can I run it? | [Getting Started](getting-started.md), [Operations](operations.md) |
| How do applications integrate? | [API Reference](api-reference.md) |
| What is the security posture? | [Security](saas/security.md), [Privacy](saas/privacy.md), [Compliance](saas/compliance.md) |
| What is shipped versus planned? | [Changelog](../CHANGELOG.md), [Roadmap](saas/roadmap.md) |
| What risks are known? | [Improvement Backlog](claude-improvement-backlog.md) |
| What are the proposed commercial packages? | [SaaS Overview](saas/overview.md), [Pricing](saas/pricing.md) |

