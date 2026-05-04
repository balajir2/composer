# Scaling Runbook

> **Maintained by:** Balaji Rajan (`balajirajan@gmail.com`)
> **Audience:** SREs, platform team, anyone planning capacity.
> **Companion docs:** [observability.md](observability.md) for the signals that drive scaling decisions, [postgres-setup.md](postgres-setup.md) for the database-side capacity options, [monitoring.md](monitoring.md) for the alarms that surface when scaling is needed.

This runbook describes Composer's capacity model: where the bottlenecks are, what to watch, what to scale first, and how to know when you've scaled enough.

## The capacity model in one paragraph

A Composer deployment has four capacity surfaces: **backend compute**, **database**, **external sub-processors**, and **WebSocket connection pool**. Backend compute scales horizontally — add replicas. Database scales vertically (compute) and horizontally (read replicas, partitioning). External sub-processors scale per their own plans (Anthropic / OpenAI / etc.) and Composer's job is to fail gracefully when they're saturated. WebSocket connections live on a single backend replica per execution; for very high concurrent execution counts, that becomes the limit. Most deployments will never hit any of these limits at the volumes a single customer drives — the runbook is for when you do.

## Bottleneck order — what saturates first

In our experience, deployments hit limits in roughly this order:

1. **Postgres connections** — every backend request opens a connection, and at high request volumes the connection pool fills.
2. **External LLM provider rate limits** — `429 Too Many Requests` from Anthropic / OpenAI / Google / Groq, before Composer itself is sweating.
3. **Backend memory** — large document uploads + concurrent agent executions hold a lot of state in memory; eventually a replica OOMs.
4. **WebSocket connection limit** — if you have many long-running executions with active stream subscribers.
5. **Postgres compute** — query latency rises before connections saturate, but it's usually a sign of a missing index, not raw capacity.
6. **Frontend host bandwidth** — Vercel scales this for you; rarely a bottleneck.

The sequence isn't strict — a workflow that uploads enormous documents + calls an LLM in a tight loop will hit memory before connections. Capacity reviews should look at all four in parallel.

## Signals that scaling is needed

| Signal | Meaning | Action |
|---|---|---|
| Postgres connection pool > 80% sustained | Approaching DB connection limit | Increase pool size on Postgres host or add a connection pooler (PgBouncer / Neon's built-in pooler) |
| Backend CPU > 80% sustained for 10 min | Hot CPU | Add a replica |
| Backend memory > 85% sustained for 5 min | Memory pressure; OOM imminent | Add replicas; investigate memory leaks; consider larger replicas |
| External-invoke p95 latency drift up | Either workflow regression or upstream slowness | Compare against LangSmith — is the LLM the bottleneck or Composer? |
| LLM provider 429 rate climbing | Per-key rate limit being approached | Upgrade the LLM provider's plan tier or split traffic across multiple keys |
| WebSocket connection count approaching backend's per-process limit | Long-running executions stacking up | Investigate; usually a workflow with too-long iteration cap |
| Stuck-row sweeper marking lots of rows | Backend was killed or saturated; rows are leftovers | Investigate why backends crashed; add capacity if recurring |

## Backend compute (horizontal scaling)

The backend is **stateless per request** (state lives in Postgres + checkpoints + the in-memory event bus). Horizontal scaling is straightforward:

- **Add replicas** via the host's UI. New replicas pick up traffic from the load balancer immediately.
- **Health checks** prevent traffic from hitting unhealthy replicas; the host should be configured for both startup probes (don't route until ready) and liveness probes (restart unhealthy).
- **Rolling restarts** are safe. Pending executions continue on their current replica; new requests land on whichever replica picks them up.

What doesn't scale horizontally:

- **WebSocket connections** for a given execution stay on the replica that serves the initial connection. If that replica is restarted mid-execution, the WS drops; the client polls the DB and observes the run completes (Composer's design — see [`../archive/incident-history/2026-04-30-execution-status-truth.md`](../archive/incident-history/2026-04-30-execution-status-truth.md)). For deployments with many long-running executions, this means rolling restarts cause WS reconnects.
- **The in-memory rate limiter** is per-replica today. With N replicas, the effective rate is N× the configured per-route limit. If accurate cross-replica rate limiting matters, the Redis-backed limiter on the [`../saas/roadmap.md`](../saas/roadmap.md) is the fix.

### Sizing rule of thumb

Start with **2 replicas, 1 vCPU + 1 GiB RAM each**. That handles ~30 RPS sustained on the API surface for typical workflows. Scale horizontally as RPS grows; scale vertically (more RAM per replica) only when you see frequent OOM kills.

## Database

### Postgres compute

Most managed Postgres hosts (Neon, RDS) let you scale compute up/down with zero downtime via the UI. Triggers to scale up:

- Average CPU > 70% for 15 min sustained
- Slow-query log shows queries timing out
- Connection pool consistently > 80%

Triggers to scale down:

- CPU < 30% for a week
- Connection pool consistently < 50%

Scaling up costs more per hour; scaling down recovers it. Neon's auto-suspend on idle helps non-production deployments save money during off-hours.

### Postgres connections

Each backend request acquires a connection from the pool. The default Prisma client's pool size is conservative (~10). At higher RPS:

- **Increase the per-replica pool size** if your Postgres host can support it (each connection has memory cost on the DB side)
- **Switch to a connection pooler** (PgBouncer in transaction mode, or Neon's built-in pooler). One pooled connection per request, not per backend connection.

Watch the Postgres host's "active connections" metric; if it's growing without bound, the pool isn't being released — investigate for leaked connections in the application.

### Read replicas

Composer doesn't need read replicas at typical volumes. They become useful when:

- Reporting / analytics queries against `workflow_executions` are large enough to slow main traffic
- Cross-region read latency dominates user experience
- The deployment has many concurrent designers reading their workflow lists

If you add a read replica, set the application to route reads (workflow lists, execution lists, executions histroy) at the replica and writes at the primary. The application code doesn't do this routing today — it's a connection-string change you'd need to layer in.

### Storage

Postgres storage grows with execution volume:

- Each `workflow_executions` row is small (~1 KB plus the size of input/output JSON)
- Each `langgraph_checkpoints` row is variable — proportional to workflow state size
- Backups multiply this by retention

For a typical customer running 10K executions/month with average input/output sizes of 1 KB each:
- ~10 MB of new execution data per month
- ~50–100 MB of new checkpoint data per month
- 60 days of execution retention + 30 days of backups = ~600 MB total

Most deployments stay well under 1 GB indefinitely. Storage is rarely the bottleneck.

## External sub-processors

The LLM providers, tool providers, and vector DBs are **outside our control**. Composer's job is to fail clearly when they fail and to spread load when possible.

### LLM provider rate limits

If you're hitting Anthropic's or OpenAI's rate limits, the options are:

1. **Upgrade the LLM plan tier.** Each provider has a tier ladder.
2. **Split across keys.** Add multiple keys for the same provider in Admin → LLM keys; today the dispatcher uses the first match — multi-key load balancing is on the [`../saas/roadmap.md`](../saas/roadmap.md). For now, manually rotate the active key under heavy load.
3. **Fall back to a different provider.** Workflows can target any provider; a designer could publish two versions (one Anthropic, one OpenAI) and the calling system could pick.
4. **Cache where appropriate.** For workflows with repetitive prompts, the LLM providers' prompt-caching features dramatically reduce per-call cost + latency. Configure on the agent node.

### Vector DB throughput

Each vector DB provider has different scaling characteristics:

- **Pinecone**: scales with the index's pod tier; upgrade for more QPS
- **Qdrant Cloud**: cluster size; scale via the host's UI
- **Chroma**: typically self-hosted; scale the underlying compute
- **Weaviate**: cluster + replication factor
- **Milvus / Zilliz**: cluster topology; consult the provider's docs

If a workflow does vector queries inside an agent loop, that's the most common cause of vector-DB saturation. Look at LangSmith traces for the query rate.

### MCP server rate limits

Highspot, ServiceNow, and similar enterprise MCP servers often rate-limit per OAuth token. If multiple workflows call the same shared MCP server, the **shared token fallback** ([`../decisions.md`](../decisions.md) ADR-0008) means one token serves all callers — and one token also has one rate limit. The fix is per-user OAuth tokens for high-volume callers.

## WebSocket connection limit

Each long-running execution holds a WebSocket connection open while a client is watching it. The single-process WS pool size depends on the backend host's process limits — typically 10K concurrent connections per replica without tuning.

For deployments where this matters (lots of designer activity simultaneously, or lots of approval-pending executions with the runs page open):

- Multiple replicas multiply the available WS pool
- Connections are stateless per execution — restarting a replica drops only the connections it owns
- Idle clients (designer who closed the tab) eventually time out at the load balancer; the count drops naturally

If you're seeing WebSocket drops at lower volumes, check:

- Load balancer idle timeout (Vercel: ~1 min for serverless; Fly: configurable for long-running)
- Backend's WebSocket framework defaults
- Browser-side connection limits (rare; usually a deployment configuration issue)

## Frontend host

Vercel scales the frontend automatically — function instances spin up under load. Limits to watch:

- **Build minutes per month** — most plans have a cap; running frequent deploys consumes them
- **Bandwidth** — usually not a concern for a B2B SaaS with low public traffic
- **Function invocations** — the frontend's API proxy makes a backend call per request; volume is bounded by user activity, not workflow execution

If the Vercel plan limits become tight, the next-tier upgrade is usually cheaper than the engineering time to optimise around them.

## Capacity review cadence

For each managed deployment, a capacity review at:

- **Day 30** post-deployment — has actual usage matched what was forecasted? Adjust if surprised.
- **Quarterly** — look at the trend lines; project forward to the next quarter; right-size proactively rather than reactively.
- **Whenever an alarm fires** — even if you mitigated, take 15 minutes to ask "is this a sign we should permanently add capacity?"

## When to push back on customers asking for more

Sometimes a customer asks for more capacity for a workload that's poorly designed:

- A workflow with `iterations: 100` that's actually meant to terminate after a couple iterations
- An MCP node calling a slow upstream from inside a tight agent loop
- A workflow uploading 10MB documents in a vector-DB upsert without chunking

Capacity scaling solves the symptom; workflow review solves the cause. Politely ask "what does this workflow do, and is it possible to redesign it so it needs less capacity?" before quoting the customer a bigger plan.

## Adjacent docs

- [observability.md](observability.md) — the signals that drive scaling decisions
- [monitoring.md](monitoring.md) — the alarms that surface when scaling is needed
- [postgres-setup.md](postgres-setup.md) — DB scaling specifics
- [`../saas/sla.md`](../saas/sla.md) — the SLA commitments capacity has to support
- [`../saas/roadmap.md`](../saas/roadmap.md) — Redis-backed rate limiter, multi-key LLM dispatch, etc.
