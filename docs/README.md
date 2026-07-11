# Composer Documentation

> **Maintained by:** Balaji Rajan (`balajirajan@gmail.com`)

Pick the doc that matches what you're trying to do.

## I want to...

| ...do this | ...read this |
|---|---|
| **Evaluate Composer as an investor or strategic partner** | [investor-overview.md](investor-overview.md) |
| **Review every shipped product capability** | [product-capabilities.md](product-capabilities.md) |
| **Understand what Composer is and why it exists** | [overview.md](overview.md) |
| **Run Composer locally and build my first workflow** | [getting-started.md](getting-started.md) |
| **Understand how the platform works internally** | [architecture.md](architecture.md) |
| **Build workflows in the Designer (nodes, variables, templates, publishing)** | [designer-guide.md](designer-guide.md) |
| **Manage users, LLM keys, MCP servers, deployment settings** | [admin-guide.md](admin-guide.md) |
| **Deploy and operate Composer in production** | [operations.md](operations.md) |
| **Call Composer's HTTP API or external-invoke endpoint** | [api-reference.md](api-reference.md) |
| **Understand a specific engineering decision** | [decisions.md](decisions.md) (ADR record) |
| **Host Composer as a SaaS — security, privacy, compliance, SLA, pricing** | [saas/](saas/) |
| **See what's changed recently** | [/CHANGELOG.md](../CHANGELOG.md) |

## By role

- **Investor / strategic partner** — [investor-overview.md](investor-overview.md) → [product-capabilities.md](product-capabilities.md) → [architecture.md](architecture.md).
- **First-time visitor / stakeholder** — start with [overview.md](overview.md), then skim [product-capabilities.md](product-capabilities.md).
- **Workflow designer / business user** — [getting-started.md](getting-started.md) → [designer-guide.md](designer-guide.md).
- **Engineer adding a new node or fixing a bug** — [architecture.md](architecture.md) → [decisions.md](decisions.md). Phase-specific design specs are under [archive/phase-history/](archive/phase-history/).
- **Admin** — [admin-guide.md](admin-guide.md).
- **Ops / SRE deploying Composer** — [operations.md](operations.md), then the relevant runbook under [operations/](operations/).
- **API consumer building an integration** — [api-reference.md](api-reference.md).
- **Customer security / GRC reviewer** — [saas/security.md](saas/security.md), [saas/privacy.md](saas/privacy.md), [saas/compliance.md](saas/compliance.md).
- **Customer procurement / legal** — [saas/sla.md](saas/sla.md), [saas/pricing.md](saas/pricing.md), [saas/legal/](saas/legal/).
- **Customer admin onboarding** — [saas/customer-onboarding.md](saas/customer-onboarding.md).

## SaaS hosting documentation

If you're considering Composer for an enterprise deployment, or operating Composer as a SaaS yourself, the [saas/](saas/) folder is the customer-facing layer:

- [saas/overview.md](saas/overview.md) — what Composer is, the deployment shapes we support
- [saas/security.md](saas/security.md) — security posture, threat model, encryption, vulnerability disclosure
- [saas/privacy.md](saas/privacy.md) — what we collect, retention, sub-processors, GDPR / CCPA mapping
- [saas/compliance.md](saas/compliance.md) — SOC 2 / GDPR / HIPAA stance, audit-log capabilities
- [saas/multi-tenancy.md](saas/multi-tenancy.md) — single-tenant vs. shared-tenant trade-offs and the path between them
- [saas/sla.md](saas/sla.md) — service level objectives, response targets, exclusions
- [saas/support.md](saas/support.md) — channels, tiers, response time commitments
- [saas/pricing.md](saas/pricing.md) — plan template, included quotas, overage policy
- [saas/roadmap.md](saas/roadmap.md) — what's shipped, what's next, what we're explicitly not doing
- [saas/customer-onboarding.md](saas/customer-onboarding.md) — first-week journey from signup to production
- [saas/legal/](saas/legal/) — Terms of Service, Privacy Policy, AUP, DPA, Sub-processors (templates; review with counsel)

The deeper operational runbooks are under [operations/](operations/) — see the [operations.md](operations.md) hub. Of particular note for SaaS:

- [operations/production-deployment.md](operations/production-deployment.md) — end-to-end deployment checklist
- [operations/incident-response.md](operations/incident-response.md) — what to do when production misbehaves
- [operations/disaster-recovery.md](operations/disaster-recovery.md) — backups, restore, RPO / RTO mechanics
- [operations/observability.md](operations/observability.md) — logs, metrics, traces — what to watch
- [operations/scaling.md](operations/scaling.md) — capacity model and when to add more

## Where the historical material went

`docs/archive/` holds material that was load-bearing during the rebuild but is no longer needed for daily reference:

- **`design-history/`** — the six 2026-04-15 brainstorming docs, the 2026-04-20 Python port design, and David Lawton's IE critique. Useful for understanding *why* Composer is the way it is.
- **`phase-history/`** — phase-by-phase implementation specs (`specs/`) and plans (`plans/`) from Phases 1–10. The code that resulted is what's authoritative now; these are kept for traceability.
- **`incident-history/`** — postmortems and defect-class write-ups, with status headers showing what landed in Composer for each.

If you find yourself reading archived material to understand the current system, that's a documentation bug — open a PR.
