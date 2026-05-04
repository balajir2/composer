# Pricing

> **Audience:** procurement, finance, customer admins evaluating plan tiers.
> **Maintained by:** Balaji Rajan (`balajirajan@gmail.com`)
> **Status:** This is the **plan template**. Actual prices are set per-deal and live in your contract; this document describes the *shape* of the plans and what's included in each.

## Plan shape

Composer is sold in four tiers. Specific dollar values are not committed in this document — they're set per-deal based on usage, deployment shape, and contract length. The structure below is what we negotiate from.

| Tier | Audience | Deployment shape | Pricing model |
|---|---|---|---|
| **Free / Self-hosted** | Solo developers; internal POCs | DIY in your own infrastructure | $0 — open source on the [GitHub repo](https://github.com/balajir2/composer) |
| **Starter** (managed) | Small teams getting their first agentic AI app live | Managed single-tenant | Monthly subscription, included quota |
| **Business** (managed) | Mid-market teams running production workflows | Managed single-tenant, dedicated region | Monthly subscription with overage billing |
| **Enterprise** (managed) | Larger organisations with strict procurement / compliance | Managed single-tenant + dedicated VPC + named team | Annual contract, custom terms |

## What's included by tier

Use this table when comparing plans during evaluation. All numbers are **per Composer environment** unless noted.

| Capability | Free / Self-hosted | Starter | Business | Enterprise |
|---|---|---|---|---|
| **Designer audience** | Up to 5 designers (advisory cap) | Up to 10 designers | Up to 50 designers | Negotiated |
| **End-user audience** | Unlimited | Up to 100 active end users / month | Up to 1,000 active end users / month | Negotiated |
| **Workflow count** | Unlimited | 50 | 250 | Negotiated |
| **Executions / month (included)** | Unlimited (you pay LLM costs) | 5,000 | 50,000 | Negotiated |
| **External-invoke endpoints** | Unlimited | 5 published workflows | 25 published workflows | Negotiated |
| **API key count per user** | Unlimited | 5 | 25 | Negotiated |
| **Document upload size** | 10 MB | 10 MB | 25 MB | Negotiated |
| **Document uploads / month** | Unlimited | 1,000 | 25,000 | Negotiated |
| **Vector DB connectors enabled** | All 5 | All 5 | All 5 | All 5 + custom |
| **MCP server count** | Unlimited | 5 | 25 | Negotiated |
| **LLM providers enabled** | All 4 | All 4 | All 4 | All 4 + bring-your-own |
| **Auth modes** | All | All | All | All |
| **SSO** | Self-configured | Azure AD | Azure AD + custom | Azure AD + custom + SAML |
| **LangSmith integration** | Customer's account | Customer's account | Customer's account | Customer's or shared |
| **Custom data residency** | Customer's responsibility | US or EU | US, EU, or APAC | Any region we operate in |
| **Bring-your-own KMS / encryption key** | Customer's responsibility | Not available | Available | Available |
| **Custom integrations** | DIY | DIY | Up to 10 hours / quarter | Negotiated |

The **executions / month** count is the conservative metric — anything that gets a `WorkflowExecution` row counts. LLM token costs pass through (we don't mark them up).

## Overage policy

For Starter and Business plans, exceeding the included quota happens on these fronts:

| Resource | Overage handling |
|---|---|
| Executions | Soft-throttle: per-user rate limits tighten by 50% once 80% of monthly quota is consumed; new executions continue but slower. Hard cap at 200% of quota = HTTP 429 until next billing cycle. |
| Workflow count | Soft-block at the cap: existing workflows still work; creating new ones requires plan upgrade or removing one. |
| External-invoke endpoints | Soft-block at the cap as above. |
| Document uploads | Same as executions. |
| LLM token spend | Pass-through — billed by your provider, not by Composer. We surface the daily totals in admin → LLM keys for visibility. |

We notify the primary technical contact at 80% of any quota and again at 100%. Enterprise plans typically negotiate either no overages or a transparent per-unit overage rate.

## What's NOT included

Worth being explicit about so there are no surprises:

- **LLM API costs.** These are billed by Anthropic / OpenAI / Google / Groq directly to your accounts. Composer is the orchestration layer; tokens are your spend.
- **Tool provider costs** (Tavily, Firecrawl, Serper, Browserless, Gamma, Arcade). Same as LLM — billed direct.
- **Vector DB hosting** (Pinecone, Qdrant, Weaviate, Chroma, Milvus). Billed direct.
- **MCP server hosting**. If you stand up an internal MCP server (Highspot, ServiceNow, Slack, etc.), that infrastructure is yours.
- **Postgres + frontend hosting** if you self-host. Managed plans bundle them; self-hosted plans are your responsibility.
- **LangSmith subscription**. Composer integrates with your LangSmith account; the LangSmith plan is yours.
- **Professional services** for custom workflows beyond the included quarterly hours. Quoted separately as SOW.

## Free / Self-hosted

The codebase is open source and free to self-host. There's no licence fee, no telemetry, no time-bombed features. What you get for $0:

- Every node type, every executor, every UI surface
- All 17 reference templates
- All 4 LLM provider integrations
- All 5 vector DB connectors
- All 6 built-in tool providers
- Full MCP support including OAuth flow
- The full operational runbook collection at [`../operations/`](../operations/)

What you don't get on the free tier:

- SLA — you're operating it; we're not
- Managed updates — you upgrade when you want, on your testing cadence
- Priority support — issues are best-effort via GitHub
- Custom SAML, custom data residency commitments, custom contracts, custom integrations — these are professional services

The free tier is the **right starting point** for evaluation, internal POCs, and customers with a strong platform team. We've built the runbooks specifically so self-hosting is realistic for someone with FastAPI + Postgres + Vercel experience.

## Plan changes

Customers can:

- **Upgrade at any time.** Pro-rated charges for the remainder of the current billing cycle.
- **Downgrade at the next renewal.** We don't enforce a punitive lock-in; downgrade with 30 days' notice and the new tier kicks in next cycle.
- **Cancel at any time.** Service continues through the end of the current billing cycle; data export available throughout.

Annual contracts (Business + Enterprise) commit on the term length. Cancelling mid-term doesn't refund the remainder unless we materially fail the SLA.

## What we charge for, and why

The honest version of the pricing rationale:

1. **Hosting cost recovery.** Postgres (Neon), Vercel/Fly, monitoring tooling, LangSmith all have line-item costs we pass through with margin to keep the business sustainable.
2. **Operational availability.** The on-call rotation, the runbooks, the postmortems, the customer-success motion all cost real human hours. Higher tiers buy more of these.
3. **Bring-your-own complexity.** Custom data residency, custom KMS, custom SSO, custom integrations all cost engineering time. They're available on higher tiers because the price covers the engineering work.
4. **Risk pricing.** A customer running Composer for a regulated workload represents more risk to us (more stringent SLA, more potential liability) and that's reflected in the contract.

We **don't** price on:

- Number of LLM calls (that's a pass-through cost; we'd be double-billing)
- Workflow complexity (a 50-node workflow runs as cheaply as a 5-node one from our side)
- Number of users you have logging in (within the plan caps; we use designer/end-user counts as a soft sizing signal, not a billing signal)
- Whether you use the canvas or the API — both surfaces are part of the same product

## Frequently asked

**Q: Can we negotiate a custom plan?**
Yes — Enterprise is the negotiated tier. If your needs don't fit Starter or Business, we'll start with Enterprise and back off complexity to find the right fit.

**Q: Is there a non-profit / educational discount?**
Yes — for verified non-profits and accredited educational institutions, the Business tier is available at a meaningful discount. Email the procurement contact and we'll start a conversation.

**Q: How does pricing change when shared tenancy ships?**
A free / self-serve shared-tenant tier is on the [roadmap](roadmap.md). When it ships, the Free / Self-hosted tier stays available; the new tier sits between Free and Starter.

**Q: What happens if you raise prices?**
For existing customers, plan prices are locked for the contract term. Renewals take the then-current price; we commit to **no surprise increases** — material price changes are communicated 90 days before renewal, with the option to lock in the existing rate for one additional year.

**Q: Do you offer per-user pricing?**
Not as the base model. Plan tiers include user counts; if you exceed them you upgrade. We've seen per-user pricing become a friction tax on the customer's adoption ("should we let the new hire sign in?") and we'd rather you onboard freely.

## Adjacent docs

- [sla.md](sla.md) — what each plan tier commits operationally
- [support.md](support.md) — what each plan tier offers in support
- [roadmap.md](roadmap.md) — when shared tenancy + new tiers ship
- [overview.md](overview.md) — what Composer is, before you decide what to pay for it
