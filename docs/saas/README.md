# Composer as a SaaS — Documentation

> **Maintained by:** Balaji Rajan (`balajirajan@gmail.com`)

This folder is for anyone hosting Composer as a multi-customer service: founders, security reviewers, prospective enterprise customers, and the team running the service. If you're an internal contributor working on the codebase, start at [`../README.md`](../README.md) instead.

## What's here

| Document | What it answers |
|---|---|
| [overview.md](overview.md) | What Composer does, who it's for, the SaaS shape we run |
| [security.md](security.md) | Security posture, threat model, encryption, key management, vulnerability disclosure |
| [privacy.md](privacy.md) | What data we collect, why, retention windows, deletion paths, sub-processors |
| [compliance.md](compliance.md) | SOC 2 / GDPR / CCPA / HIPAA stance, audit-log capabilities, certification status |
| [multi-tenancy.md](multi-tenancy.md) | Isolation model, blast radius, single-tenant vs. shared deployment trade-offs |
| [sla.md](sla.md) | Service level objectives, response targets, exclusions, service credit policy |
| [support.md](support.md) | Support tiers, channels, response time commitments, escalation paths |
| [pricing.md](pricing.md) | Plan template, included quotas, overage policy (commercial terms — adjust per deal) |
| [roadmap.md](roadmap.md) | What's shipped, what's next, what we're explicitly not doing |
| [customer-onboarding.md](customer-onboarding.md) | The first-week journey from signup to running production workflows |
| [legal/](legal/) | Templated terms of service, privacy policy, AUP, DPA, sub-processor list — **review with counsel before publishing** |

## How to read this folder

**Buying Composer for your enterprise:** start at [overview.md](overview.md) → [security.md](security.md) → [compliance.md](compliance.md) → [sla.md](sla.md). The legal templates in [legal/](legal/) are the agreement skeletons; the actual contract is whatever your account team signs.

**Operating Composer as a SaaS:** the customer-facing docs above describe *what we promise*. The runbooks under [`../operations/`](../operations/) describe *how we deliver* — production deployment, incident response, DR, observability, scaling. Both folders are load-bearing; one without the other leaves either commitments without a process or a process without commitments.

**Engineering on Composer's codebase:** stay in [`../`](../). The architecture, designer guide, admin guide, and ADRs live there. This folder rarely gets touched during feature work.

## Status & honesty

Composer is a **deliberate Python rebuild** of [Open Agent Builder](https://github.com/balajir2/open-agent-builder), built on a stack that mirrors Bounteous's Intelligent Engineering platform. Phases 0–10 are complete; the platform runs end-to-end with FastAPI + Postgres + LangGraph + Next.js.

The **deployment topology we run today is single-tenant per environment** — one Composer instance, one Postgres database, one Vercel/Fly host, one set of customer accounts. We have not yet shipped a shared-tenant SaaS where multiple customer organisations live in the same database. [multi-tenancy.md](multi-tenancy.md) explains the trade-offs and the path to a shared model when a customer needs it.

Where a doc describes something we do today vs. something we will deliver, the doc says so plainly. If you spot a section that overstates the current state, [open an issue](https://github.com/balajir2/composer/issues) — credibility decays the moment a customer catches a mismatch.

## Adjacent material

- [`../decisions.md`](../decisions.md) — engineering ADRs that shape what's possible to commit to (encryption choices, auth model, deployment toggles, etc.)
- [`../operations/`](../operations/) — operational runbooks that back the customer-facing commitments here
- [`../archive/incident-history/`](../archive/incident-history/) — postmortems & defect-class write-ups (kept for cross-product learning)
