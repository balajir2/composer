# Service Level Agreement (SLA)

> **Audience:** procurement, legal, ops contacts at customer organisations.
> **Maintained by:** Balaji Rajan (`balajirajan@gmail.com`)
> **Status:** This document is the **commitment template** for managed Composer deployments. The contract you sign with us is the operative agreement; this is what we'll bring to the table.
> **Self-hosted customers:** the SLA below doesn't apply to deployments you operate. Your internal SLA is what you commit to internally.

## Commitments by plan tier

| Commitment | Free / Self-hosted | Starter (managed) | Business (managed) | Enterprise (managed) |
|---|---|---|---|---|
| Monthly uptime target | n/a | **99.5%** | **99.9%** | **99.95%** |
| Service credit if missed | n/a | 10% of monthly fee per missed 9 | 25% of monthly fee per missed 9 | 50% of monthly fee per missed 9, capped at 100% |
| First response on incident reports | Best effort | 1 business day | 4 business hours | 1 hour, 24×7 |
| Time to first fix (target) | n/a | Best effort | Same calendar week | 24 hours for Sev-1; 1 week for Sev-3 |
| Maintenance windows | n/a | Up to 4 hours / month, weekend | Up to 2 hours / month, weekend | Pre-coordinated, customer-approved |
| Disaster recovery RTO | Customer's responsibility | 8 hours | 4 hours | 1 hour |
| Disaster recovery RPO | Customer's responsibility | 24 hours | 4 hours | 15 minutes |
| Postmortem on Sev-1 incidents | n/a | Within 10 business days | Within 5 business days | Within 2 business days |

The numbers above are **the template we negotiate from**, not the floor. Enterprise customers regularly negotiate higher uptime + tighter response targets; we'll tell you up-front what's achievable and what isn't. We won't sign a 99.99% SLA we can't deliver against — we'd rather deliver 99.9% reliably and price accordingly.

## What "uptime" means here

We measure uptime against the **availability of the published external-invoke API** (`POST /api/run/{slug}`) for each managed deployment. Specifically:

- The endpoint must return a 2xx, 4xx (other than 5xx-class equivalents like 502/503/504), or `{"status": "running"}` async response within 30 seconds of receiving a valid request from a synthetic monitor we run.
- Measurement happens **per-minute** from at least three geographic probes.
- Downtime is counted from the first failed probe to the first successful subsequent probe, summed across the month.

What's **excluded** from uptime calculation:

- Failures **caused by the customer's workflow** — bad LLM keys, MCP server outages, vector DB downtime, malformed input, agent loops hitting iteration cap, etc. (We measure these separately for transparency, but they don't affect the SLA.)
- Failures of the **frontend** — the canvas / runs page / admin console. These are convenience interfaces; the SLA is on the API surface that powers production workflows.
- Failures of **third-party LLM / tool / vector providers**. If Anthropic is down, your workflows that use Anthropic will fail. We'll help you root-cause and switch to a fallback provider; we won't credit the time as Composer downtime.
- **Approved maintenance windows**, communicated at least 7 days in advance.
- **Force majeure** — internet routing incidents, regional cloud-provider outages affecting your deployment region, denial-of-service attacks Composer's hosts can't absorb. We'll communicate; we won't credit.

Customers get a **public status page** (managed plans only) listing current and historical incidents, including the ones excluded above. Transparency on what's broken matters more than crediting accuracy.

## Execution durability

A queued or in-flight workflow run is now durable against the underlying compute recycling: `POST /executions` enqueues a Google Cloud Tasks job rather than a request-bound background task, a claim-and-run endpoint picks it up under `SELECT ... FOR UPDATE SKIP LOCKED`, and a lease/heartbeat plus a Cloud-Scheduler-triggered sweep recover a run whose worker died mid-execution. In practice this means an autoscaled backend instance scaling to zero — or a deploy rolling instances — no longer silently kills work in progress. This is an architectural property of the platform, not a separate contractual commitment; it's what the uptime and RTO/RPO numbers above are built on.

## Severity classification

When you report an issue, we triage to one of four severities. The category drives response time and escalation.

| Severity | Definition | Examples | First response | Active update cadence |
|---|---|---|---|---|
| **Sev-1** | Production workflows broken; no workaround | External-invoke API returns 5xx for a sustained period; workflows can't be saved; admin can't sign in | Within SLA | Every 1 hour until resolved |
| **Sev-2** | Production workflows degraded; workaround exists | One LLM provider failing verifications; canvas slow but functional; specific node type erroring | Within 1 business day (Starter), 4 hours (Business), 1 hour (Enterprise) | Daily |
| **Sev-3** | Inconvenience; non-blocking | UI bug; a template doesn't seed; a doc link is broken | Within 3 business days | Weekly |
| **Sev-4** | Feature request, question, observation | "How do I do X?"; "Could you add Y?" | Best effort | n/a |

Customers can propose a severity at submission time; we may re-classify with rationale.

## Escalation path

For Sev-1 + Sev-2 (Business + Enterprise plans):

```
1. Customer's primary technical contact files via the support channel
2. On-call engineer acknowledges and triages within the SLA
3. If unresolved by 2× the response SLA → escalation to engineering manager
4. If unresolved by 4× the response SLA → escalation to product/exec
5. Postmortem follows resolution per the table above
```

The escalation contacts are named in the contract. For Enterprise, we name a specific named technical contact and a named exec sponsor on each side.

## What customers get during a Sev-1

- A dedicated incident channel (Slack Connect or Teams, set up at deployment time) — humans, not bots
- Real-time status updates on the public status page (managed plans)
- All available diagnostic data: runtime logs, LangSmith trace links (with permission), Postgres query plans, error trace
- A war room where the incident lead has decision authority — no triage shopping
- Coordinated comms with our team if your customers are seeing the impact too

## Service credits — how they work

If we miss the uptime commitment in a calendar month:

1. Credit is calculated automatically based on the measurements logged on the status page.
2. Credits are applied to the **next** invoice cycle.
3. Credits **cap at the monthly fee** (you don't owe us money for missed uptime).
4. Credits are the **sole financial remedy** for missed SLA per the contract — they don't compound across consecutive months and don't include consequential damages.

If you believe an incident should have been credited and wasn't (or was, and shouldn't have been), open a Sev-3 ticket and we'll review. The invoice will reflect any correction the next cycle.

## What's outside the SLA

Things we provide on a **best-effort, no-SLA** basis:

- Self-hosted deployments (your operations are your responsibility)
- Free-tier deployments
- Public templates and the seed-templates script (these are reference material; your operational workflows shouldn't depend on the template seeding running)
- Evaluation / proof-of-concept deployments before contract signing
- Anything labelled "alpha" or "beta" in [roadmap.md](roadmap.md)
- The MCP server registry's third-party servers (we provide the integration; the third party owns the uptime)

## Your obligations under the SLA

Reciprocal commitments — we can hit the SLA only if you do these:

- Keep the **primary technical contact email** current. We use it for incident notifications, maintenance windows, and renewal communication.
- Use the **published external-invoke API**, not undocumented internal endpoints. If your integration relies on an endpoint not in [api-reference.md](../api-reference.md), it's not under SLA.
- Don't disable required deployment settings — `ENVIRONMENT=production`, the `JWT_SECRET`, the `ENCRYPTION_KEY`, etc. The runbooks are explicit about this and our health checks alert on misconfiguration.
- Don't try to bypass the rate limits. They protect the entire deployment, including you.
- Pay invoices on time. Suspended accounts are not covered by SLA.

## Incident reporting + history

Past incidents — public-facing summaries, customer-impact analysis, root cause, remediation — are published in [`../archive/incident-history/`](../archive/incident-history/). The detail in those write-ups is the standard customers can hold us to in their own postmortems.

For a managed deployment, your status page also tracks per-deployment incidents that may not show up in the public archive (single-customer events).

## Adjacent docs

- [support.md](support.md) — how to reach us during and outside an incident
- [compliance.md](compliance.md) — the broader assurance regime that includes SLA performance
- [`../operations/incident-response.md`](../operations/incident-response.md) — the runbook our team uses on the inside
- [`../operations/disaster-recovery.md`](../operations/disaster-recovery.md) — what RTO / RPO actually means in practice
- [pricing.md](pricing.md) — how SLA tiers map to plans
