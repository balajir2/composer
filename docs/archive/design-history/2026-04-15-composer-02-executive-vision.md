# Composer — Executive Vision

**Audience:** Bounteous leadership, IE product/exec stakeholders
**Purpose:** Strategic narrative for adding a visual, low-code composition layer to the Intelligent Engineering platform
**Date:** 2026-04-15

---

## The One-Liner

> **Composer is the low-code composition layer for Intelligent Engineering. It makes IE's coded agents assembly-able — not just consumable — by non-developers.**

---

## The Thesis in Two Paragraphs

Intelligent Engineering today is a curated catalog of vetted SDLC agents (Story Enhancer, AC Validator, Code Reviewer, Unit Test Agent, etc.) running on enterprise-grade scaffolding — multi-tenant, governed, auditable, SSO-protected. It's a strong product, but it has one structural limit: **every new workflow requires a Bounteous engineer to code a new agent**. Tenants can consume what we ship; they cannot compose what we've already shipped.

Composer solves this. It is a visual, drag-and-drop workflow builder — ported from Bounteous's Open Agent Builder — that lets tenant admins and business users compose IE's existing agents, MCP integrations, and LLM calls into custom workflows **without filing a ticket to engineering**. Same enterprise wrapper, same governance, new composition surface. The analog is Salesforce → Lightning App Builder: the CRM is the core product; Lightning is what turned it into a platform.

---

## Why Now

Three forces align:

1. **Enterprise LLM adoption is moving from "pilot an agent" to "deploy dozens of agents across workflows."** Customers increasingly ask "can your AC Validator also post to our Slack channel when it finds gaps?" Today that's a change request. With Composer, it's a 10-minute workflow edit by the tenant themselves.

2. **Open Agent Builder exists today as a working, debugged product.** We've shipped real customer workflows through it (including Highspot OAuth, proven end-to-end as of April 14-15 2026). The hard engineering problems — LangGraph execution, MCP with OAuth, multi-LLM support, human-in-the-loop — are solved. We're porting known-good code, not inventing.

3. **IE is ready to be a platform, not a product.** The musical naming (Maestro, Chorus, Cue) hints at an intention. Composer completes the theme — **the Composer writes the piece, the Maestro conducts, the Chorus performs, the Cue signals the start** — and completes the platform architecture.

---

## Strategic Positioning

| Without Composer | With Composer |
|---|---|
| IE = "a set of AI tools Bounteous sells to enterprises" | IE = "a platform on which enterprises build their own AI-powered SDLC automations" |
| Pricing = per-agent or per-seat | Pricing = tiered platform (Base / Pro / Platform) where Platform tier unlocks workflow creation |
| Customer feedback loop = RFE → Bounteous backlog → 6 months | Customer feedback loop = self-service in their tenant |
| Competitive moat = the agents themselves | Competitive moat = governed low-code + ready-made agents combined |
| Total addressable market = customers who can use what we shipped | Total addressable market = customers + what they compose + what they share back |

---

## Who Benefits and How

Three personas the product serves:

**Tenant admins (IT / DevOps leads at customer organizations)**
> *"Our Jira workflow needs an extra approval step before the Code Reviewer runs. I can add it myself in 10 minutes."*

Customizes workflows per tenant without a Bounteous ticket. Configures integrations. Decides which agents their team can compose with.

**Business analysts / tech leads at customer organizations**
> *"I want to chain Story Enhancer → AC Validator → auto-link to Confluence → notify Slack. I just drew it on the canvas."*

Composes new SDLC automations from existing IE building blocks plus external MCPs. No coding.

**Bounteous platform operations**
> *"Three customers asked for variations of the same workflow. I'll build a template once and publish it to the catalog."*

Creates reusable workflow templates that become part of IE's marketplace. Uses the same tool customers use.

---

## Phased Roadmap (Built for GTM Speed)

### Phase 1 — Composer as IE Module (MVP)
**Target:** Working demo to close first 1-2 enterprise deals that have asked for workflow customization
**Shape:** Composer is a new app in the IE monorepo (`apps/composer/`). Opt-in per tenant. Uses IE's auth, tenancy, MCP orchestrator, and agent catalog. Delivers the 12 most-used node types out of OAB's 15.
**Success signal:** One tenant uses Composer to build a non-trivial workflow without Bounteous engineering support.

### Phase 2 — Template Marketplace
**Target:** Reduce time-to-value for new tenants
**Shape:** Templates published by Bounteous (and, eventually, tenants) live in IE's Catalogue UI alongside agents. A tenant forks a template, customizes 2-3 nodes, and runs it.
**Success signal:** 50%+ of tenant-composed workflows start from a template, not from scratch.

### Phase 3 — Composer as Core (Promotion)
**Target:** Composer becomes the canonical execution runtime for all IE workflows, including the agents Bounteous ships
**Shape:** Bounteous's own agents are themselves composed workflows (or mixed composed/coded). Coded agents and composed workflows are first-class siblings. Catalog unifies.
**Success signal:** At least 2 new Bounteous-shipped agents are built in Composer rather than hand-coded.

### Phase 4 — Ecosystem
**Target:** Turn Composer into a Bounteous business unit asset, not just an IE feature
**Shape:** Tenant-authored templates can be shared across tenants (opt-in). Tenant-built workflows become case studies. Bounteous consulting wraps implementation services around customer-specific compositions.
**Success signal:** Revenue from Composer-enabled consulting exceeds Composer licensing revenue.

---

## What This Is Not

Three clarifications to preempt misunderstandings:

1. **Composer does not replace Bounteous-coded agents.** The AC Validator, Story Enhancer, etc., continue to exist as coded microservices. Composer *uses* them as building blocks.
2. **Composer does not compete with Claude/ChatGPT as a product.** It competes with asking Bounteous engineering for a custom agent. The enterprise wrapper is the moat — not the LLM.
3. **Composer is not a Zapier clone for AI.** Zapier targets individual productivity with consumer SaaS. Composer targets IT/engineering leaders inside enterprise orgs with governed, audited, tenanted workflows.

---

## Economic Shape

**Investment shape:**
- Port Open Agent Builder's execution engine to Python (FastAPI + `agent-commons`) — the bulk of the work
- Port the React Flow UI into `apps/composer/composer-ui/` — mostly adaptation
- Integrate into IE's auth, tenancy, agent catalog, MCP orchestrator
- Ship behind a feature flag; roll out per tenant

**Revenue shape (Phase 1 → 4):**
- Phase 1: Add-on SKU to existing IE tenants who want composition; bundled with enterprise tier
- Phase 2: Templates drive stickiness; upsell signal
- Phase 3: New "Platform" tier — Composer + coded agents at premium price point
- Phase 4: Consulting multiplier — every Composer tenant is a services opportunity

---

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| **Scope creep** — tempting to port every OAB feature | Ship 12 of 15 node types; defer UI Builder, Arcade, Gamma (documented, not built) |
| **Python rewrite takes longer than expected** | OAB is debugged and working; Python port is disciplined re-implementation, not R&D |
| **IE architecture inertia** — teams resist new app in monorepo | Composer as optional module; no changes to existing IE code in Phase 1 |
| **Tenant abuse / runaway workflows** | Inherit IE's rate limiting, quotas, and execution caps |
| **OAB product continues separately** — confusion in market | Clear positioning: OAB is the standalone offering; Composer is the IE-integrated module; same engine, different distribution |

---

## What Success Looks Like at 12 Months

- **At least 3 enterprise tenants** have Composer enabled and are actively composing workflows
- **At least 15 published templates** in the marketplace (mix of Bounteous-authored and tenant-authored)
- **At least 50% of tenant-originated feature requests** are now self-servable in Composer (measurable reduction in Bounteous engineering backlog)
- **At least 1 new IE agent** shipped as a Composer workflow rather than hand-coded
- **Net-new revenue** tied to Composer SKU exceeds the engineering investment

---

## The Recommendation

**Ship Composer as a Module in IE now.** Port the Python backend, adapt the React Flow frontend, integrate with IE's platform services. Target first demo in the next quarter, first paid tenant in the quarter after. Use Open Agent Builder's existing fixes and patterns as the design spec — don't re-invent.

The pitch to a customer shifts from *"here's what Bounteous built for you"* to *"here's a platform on which you can build what you need."* That's a category shift, not a feature.
