# Support

> **Audience:** customers operating Composer day-to-day; admins; primary technical contacts.
> **Maintained by:** Balaji Rajan (`balajirajan@gmail.com`)

## Channels

| Plan | Primary | Secondary | Hours |
|---|---|---|---|
| Self-hosted / Community | [GitHub Issues](https://github.com/balajir2/composer/issues) | Discussions on the same repo | Best effort, community-driven |
| Starter (managed) | Support email (named in contract) | Slack Connect channel | Business hours, primary timezone |
| Business (managed) | Slack Connect channel | Support email; phone for Sev-1 | Extended business hours, primary timezone |
| Enterprise (managed) | Dedicated Slack Connect + named CSM | Phone for Sev-1, 24×7 on-call rotation | 24×7 |

For all customers regardless of tier, **security issues** route via the dedicated security disclosure path in [security.md](security.md), not normal support. This guarantees the issue reaches the right people and doesn't sit in a regular support queue while we coordinate disclosure.

## Response targets

| Severity | Starter | Business | Enterprise |
|---|---|---|---|
| Sev-1 (production broken, no workaround) | 1 business day | 4 business hours | 1 hour, 24×7 |
| Sev-2 (degraded, workaround exists) | 2 business days | 1 business day | 4 hours |
| Sev-3 (inconvenience) | 3 business days | 2 business days | 1 business day |
| Sev-4 (question, feature request) | Best effort | Best effort | Best effort |

The full severity definitions live in [sla.md](sla.md).

## What to send when you report an issue

A clear bug report is the single biggest accelerator on time-to-fix. Please include:

1. **What you were trying to do** in plain English
2. **What you observed** vs. what you expected
3. **Steps to reproduce**, ideally on a minimal workflow
4. **Composer version** (the commit hash on the deployed backend; the admin console shows it)
5. **The execution id** (or the workflow id if it's a save/edit issue) — this is what gives us the trace + the DB row + the LangSmith link in one shot
6. **Browser + OS** if it's a UI issue
7. **Time of incident** in UTC, ideally a 5-minute window

For Sev-1 reports, screenshot or paste the error you saw. For data integrity issues, **don't include actual customer data** in the report — paste the column shape and the type of value, not the value itself.

## What you can expect from us

When you open a ticket, we commit to:

- **Acknowledgement** within the SLA — even if it's just "got it, on it"
- **Triage decision** within the next response cycle: severity, owner, target resolution
- **Status updates** at the cadence in [sla.md](sla.md) until resolved
- **A clear "we won't fix this" if applicable** — we'd rather tell you than ghost the ticket. We'll explain why and propose alternatives if any exist.
- **A postmortem on Sev-1 incidents** within the cadence in [sla.md](sla.md). Customer-facing version is shareable; the internal version stays internal.

## What we'll ask from you

Reciprocal — we can move fast on your behalf only if:

- **Primary technical contact is reachable** during the response window
- **A second contact is named** for cases when the primary is unavailable (out of office, switched roles, etc.)
- **You can grant temporary access** to your deployment for diagnostic purposes when needed (we'll always ask first; you can refuse and we'll work with what you can share)
- **Bug reports include the diagnostic detail above** — vague reports add a ticket round-trip
- **Renewal conversations happen 60 days before expiry** so the support relationship doesn't lapse

## Self-serve resources

Before opening a ticket, the following resources answer most operational questions:

| Question | Where to look |
|---|---|
| "How do I do X in the canvas?" | [`../designer-guide.md`](../designer-guide.md) |
| "How do I add an LLM key / promote a user / configure SSO?" | [`../admin-guide.md`](../admin-guide.md), [`../operations/`](../operations/) |
| "What does this API endpoint return?" | [`../api-reference.md`](../api-reference.md) |
| "Why does Composer behave that way?" | [`../decisions.md`](../decisions.md) — every meaningful design choice has an ADR |
| "What changed recently?" | [`../../CHANGELOG.md`](../../CHANGELOG.md) |
| "Has this issue happened before?" | [`../archive/incident-history/`](../archive/incident-history/) |
| "What's coming next?" | [roadmap.md](roadmap.md) |

If a doc gap surprised you, file it as a Sev-3 — improving the docs is in scope for support.

## Status communications

Managed customers get:

- **Public status page** with current state and historical incidents per the SLA section ([sla.md](sla.md))
- **Email** to the primary technical contact for: confirmed incidents (Sev-1 and Sev-2), maintenance windows (7 days advance notice), material policy or sub-processor changes (30 days advance notice)
- **In-product banner** on the affected deployment when an incident is in progress (so end users see "we're aware" without the admin having to relay it)

Self-hosted customers get the public status of the codebase via GitHub Releases + the public CHANGELOG, but we have no view into your individual deployment's status — that's your platform team's responsibility.

## Office hours + advisory

Business and Enterprise plans include scheduled office-hours sessions:

- **Business**: 1 hour per quarter, customer-led agenda — typically architecture, scaling, or template design
- **Enterprise**: 1 hour per month, plus a named CSM doing quarterly business reviews

These are advisory, not support — they don't replace the ticket flow. Use them for "we're thinking about doing X, what would you recommend?" questions where the answer benefits from a conversation.

## Feature requests

We track feature requests publicly on [GitHub Discussions](https://github.com/balajir2/composer/discussions). The roadmap ([roadmap.md](roadmap.md)) is updated quarterly and reflects our actual priority order.

What we look for in a feature request:

- The user problem, not the implementation. ("I need to do X" beats "Add an X node.")
- Impact: how many of your workflows would benefit, how much faster you'd ship the work, etc.
- Whether you can self-serve via existing primitives — sometimes the answer is "you can already do that with these three nodes" and we'll show you how

We don't ship every requested feature. The roadmap explains the cuts.

## What's not in scope for support

To keep the support relationship sustainable, the following are out of scope (we'll either redirect or quote separately):

- **Workflow design consulting at scale.** A request like "build us a 50-node workflow that does X" is a professional services engagement, not support.
- **Custom node types.** We can advise on patterns; building one is engineering work scoped via SOW.
- **Migrating from a competitor.** We'll help with Composer-specific questions; the upstream system's data extraction is yours.
- **Operational ownership of self-hosted deployments.** Self-hosted customers operate their own infrastructure; we provide the runbooks.
- **Training the LLM.** We don't fine-tune; the LLM providers do that on their own platforms.

## Contacts

| Need | Contact |
|---|---|
| General support | `balajirajan@gmail.com` |
| Security disclosure | `balajirajan@gmail.com` (per [security.md](security.md)) |
| Privacy / data subject requests | `balajirajan@gmail.com` (per [privacy.md](privacy.md)) |
| Compliance / audit / questionnaire | `balajirajan@gmail.com` |
| Procurement / contract | `balajirajan@gmail.com` |
| Billing | `balajirajan@gmail.com` |

Composer is currently authored and operated by **Balaji Rajan** (`balajirajan@gmail.com`). If your organisation forks Composer to run as its own SaaS, replace this single contact with whatever inbox structure (separate concerns per address) suits your team — what matters is that each concern has a clear, monitored owner.

## Adjacent docs

- [sla.md](sla.md) — the formal commitments behind these channels
- [compliance.md](compliance.md) — what we commit to procurement
- [`../archive/incident-history/`](../archive/incident-history/) — past incident postmortems for reference
- [pricing.md](pricing.md) — how plans relate to support tier
