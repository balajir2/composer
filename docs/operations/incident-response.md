# Incident Response Runbook

> **Maintained by:** Balaji Rajan (`balajirajan@gmail.com`)
> **Audience:** the on-call engineer, the customer success contact, and anyone who'll be in the war room.
> **Companion docs:** [`../saas/sla.md`](../saas/sla.md) for severity classification and response targets, [disaster-recovery.md](disaster-recovery.md) for the recovery procedures, [observability.md](observability.md) for what to look at, [`../saas/support.md`](../saas/support.md) for customer-facing channels.

This runbook is what you reach for when something is **on fire right now**. Read it before an incident — calm reading is faster than panicked reading.

## The incident lifecycle

```
        ┌──────────────────────────────────────────────────────────┐
        │                                                          │
        ▼                                                          │
   1. DETECT  ──→  2. ACKNOWLEDGE  ──→  3. TRIAGE  ──→  4. MITIGATE│
                                                          │       │
                                                          ▼       │
                                                     5. RESOLVE   │
                                                          │       │
                                                          ▼       │
                                                     6. POST-     │
                                                        MORTEM ──┘
```

The runbook walks each step. The phrase **"escalate up the chain"** means notify the next contact in the escalation list per the customer's contract — typically engineering manager, then product/exec.

## 1. Detect

Detection happens via:

| Channel | Latency | Coverage |
|---|---|---|
| External uptime monitor | ~1 min | `/health` endpoint reachability |
| LangSmith trace volume alert | ~5 min | Real workflow throughput |
| Customer ticket | minutes to hours | Customer-visible symptoms |
| Backend log error rate | ~1 min | Internal exceptions, 5xx counts |
| Postgres connection pool alarm | ~1 min | DB capacity issues |
| Sweeper inactivity alarm | ~10 min | Background maintenance loop stopped |

When any channel fires, the on-call engineer is paged. Multiple firings on the same incident are collapsed by the alerting tool.

## 2. Acknowledge

Within the response SLA for the customer's plan ([`../saas/sla.md`](../saas/sla.md)):

1. **Acknowledge the page** (PagerDuty / Opsgenie / your tool).
2. **Open a war-room channel** — Slack `#incident-yyyy-mm-dd-shortname` is the convention. Invite the customer's primary technical contact for Sev-1 with material customer impact.
3. **Drop a status update** in the customer-facing channel:

   > "We're aware of an issue affecting *<scope>*. We're investigating. Next update by *<time>*."

   Even one line is better than silence. Update at the cadence in [`../saas/sla.md`](../saas/sla.md).

4. **Assign roles** in the war room:
   - **Incident lead** — the person who owns the decision authority. Often the on-call engineer.
   - **Comms** — updates to the customer + status page. Often a customer success / TAM.
   - **Investigator** — actively looking at logs / DB / traces. Can be the same person as the lead for a small incident.

   For Sev-1, these should be three different people. For Sev-2, two. For Sev-3, one is fine.

## 3. Triage

Determine severity per [`../saas/sla.md`](../saas/sla.md):

| Severity | Definition |
|---|---|
| Sev-1 | Production workflows broken; no workaround |
| Sev-2 | Production workflows degraded; workaround exists |
| Sev-3 | Inconvenience; non-blocking |
| Sev-4 | Question, request, observation |

Severity drives:
- Response cadence (how often you update the customer)
- Who you escalate to (Sev-1 may go straight to engineering manager)
- Whether the postmortem is required
- Whether a service credit applies per the SLA

Once severity is set, **send the second status update** to the customer with:
- Confirmed scope (what's affected, what's working)
- Suspected cause (if any) — be careful not to commit before evidence
- Next-update time

## 4. Mitigate

Pick the mitigation that **minimises customer impact fastest**, even if it doesn't fix root cause. Investigation continues; mitigation buys time.

Common mitigations by failure class:

### Backend won't start / crashes

```
1. Roll back to the previous container image / Vercel deployment
2. If rollback is unavailable, scale the failing replicas to 0 — this returns 502 honestly instead of intermittent timeouts
3. Investigate the failing image offline
```

### Database is degraded (slow queries, connection-pool exhaustion)

```
1. Check the host's status page — is it a host issue or a Composer issue?
2. If host: comms to the customer, wait it out (with periodic updates)
3. If Composer: scale the connection pool, kill long-running queries, look at recent migrations for slow plan changes
4. Postgres `pg_stat_activity` shows the active query mix; that's where you start
```

### Specific LLM provider is failing

```
1. Confirm with the provider's status page (Anthropic / OpenAI / etc.)
2. If single-provider outage: comms to the customer, no Composer-side mitigation possible
3. If Composer-side (key issue / verification mismatch): rotate the key in Admin → LLM keys, click Verify on the affected models
4. If the provider's outage is sustained, point customer workflows at a different provider as a temporary fallback
```

### Sweeper isn't running

```
1. The "execution_sweeper: started" log line should appear once at boot. If not, the lifespan didn't start it correctly — check env vars (`EXECUTION_SWEEPER_INTERVAL_SECONDS != 0`) and restart
2. Manually invoke the sweeper while you investigate:
   uv run python -c "import asyncio; from src.maintenance.execution_sweeper import sweep_stuck_executions; from prisma import Prisma; \
     async def main():
         db = Prisma()
         await db.connect()
         try:
             res = await sweep_stuck_executions(db, stuck_after_seconds=900)
             print(res)
         finally:
             await db.disconnect()
     asyncio.run(main())"
3. While the sweeper's down, manually clear stuck rows via SQL if customer-impacting:
   UPDATE workflow_executions SET status='failed', error='Manually cleared during incident <id>', completed_at=now()
   WHERE status='running' AND started_at < now() - interval '15 minutes';
```

### MCP server returning bad data

```
1. The base64-blob sanitiser (src/mcp/sanitize.py) handles this defensively, but if a different shape of malformed response is causing failures:
2. Disable the offending MCP server (Admin → MCP servers → toggle enabled=false). Workflows that reference it will fail fast with a clear error.
3. Investigate the server's actual response with `curl -X POST <mcp-url>/rpc -d '{"method":"tools/list",...}'` to see the raw shape.
4. If a sanitiser update is needed, that's a code change → release path; not a runtime mitigation.
```

### Memory / CPU exhaustion on backend host

```
1. Identify the runaway with the host's metrics (`top` / `htop` if you have shell access)
2. Common culprits: a huge document upload, a workflow with an iteration cap that's too high, a memory leak in a recently changed dependency
3. Restart the affected replicas one at a time; new replicas pick up traffic without a service interruption
4. Profile in a non-production replica if the issue persists
```

### Auth misconfigured (no one can sign in)

```
1. Check that ENVIRONMENT, JWT_SECRET, AUTH_SECRET, and the Azure AD env vars are set on the backend
2. If JWT_SECRET was rotated without invalidating sessions: roll back the env change and notify users to sign in again
3. If Azure AD app misconfigured: validate the reply URL matches the deployment's hostname exactly; redeploy if needed
4. If the standalone fallback is needed in an SSO-only environment: temporarily set ENVIRONMENT=development to enable dev-mode auth (only for emergency admin sign-in; revert immediately after)
```

## 5. Resolve

After mitigation has stabilised the customer-impact:

1. **Confirm with the customer** that they're seeing recovery. ("Are your workflows running again?")
2. **Send the resolution status update**:

   > "Resolved at *<time>*. Symptoms: *<what they saw>*. Mitigation applied: *<what we did>*. We'll send a postmortem within *<SLA>*."

3. **Update the public status page** (managed customers) with the incident as resolved.
4. **Close the war room** — but archive the channel; you'll need it for the postmortem.

## 6. Postmortem

Required for Sev-1 incidents per the SLA. Recommended for Sev-2 incidents that surfaced a previously-unknown failure mode.

The postmortem document follows the template at [`../archive/incident-history/`](../archive/incident-history/) (use any existing entry as a starting point). Required sections:

| Section | Content |
|---|---|
| **Summary** | One paragraph: what happened, what the impact was, what we did. |
| **Timeline** | UTC timestamps from detection to resolution, with key events. |
| **Customer impact** | How many customers, what were they unable to do, did anyone lose data. |
| **Root cause** | Not what triggered the failure; what *allowed* the trigger to cause failure. ("The deploy was bad" is the trigger; "we have no automated check that catches this class of bad deploy" is the root cause.) |
| **Why didn't we catch it sooner?** | Detection-side analysis. |
| **Action items** | Specific, owned, with dates. Each item closes a path the incident exposed. |
| **What went well** | The parts of the response that worked. Worth naming so they don't atrophy. |

Postmortem cadence per [`../saas/sla.md`](../saas/sla.md). For customer-facing postmortems, edit the document to remove sensitive operational details and share with the customer.

File the postmortem under [`../archive/incident-history/`](../archive/incident-history/) with a date-prefixed filename.

## Specific runbooks for known failure modes

The following are responses to incidents we've seen before. They're shorter than the general framework above because the diagnosis is already done.

### Mass execution failures with "model X is marked unavailable"

The Verify probe noticed an LLM model is no longer reachable for the configured key, auto-disabled it, and workflows referencing it now fail fast. This is **expected behaviour** — better to fail clearly than to retry ambiguously.

Mitigation:
1. Admin → LLM models → find the disabled model
2. Verify it again to confirm it's still unavailable
3. If a replacement model exists (e.g., Google retired `gemini-2.0-flash` → switch to `gemini-2.5-flash`), enable the replacement
4. The agent panel surfaces "(unavailable — pick a replacement)" for affected workflows; designers can swap in the replacement and re-publish

### "Stuck running" execution rows

The stuck-execution sweeper handles this automatically, but during an incident you may want to clear them faster than the 5-minute interval. Use the manual SQL above under "Sweeper isn't running."

### Highspot MCP returning huge base64 responses

The base64-blob sanitiser handles this. If a customer reports it's still happening:
1. Confirm the sanitiser path is wired (`grep strip_binary_blobs src/mcp/base.py`)
2. Look at the raw MCP response in LangSmith — has Highspot changed the response format?
3. If yes, update the sanitiser to handle the new format, ship a patch release

### Customer's external invoke returns 401 unexpectedly

Most common cause: the customer's API key was revoked or expired. Confirm via the runs API keys page. If the key is fine:
1. Check that the workflow is still `isProduction=true` (someone may have unpublished it)
2. Check that the workflow's owner exists and is `isActive=true`
3. Check the rate-limit bucket for that API key (is it being throttled?)
4. Backend logs around the failed call will show the exact rejection reason

### LangSmith traces stop appearing

1. Check that `LANGCHAIN_TRACING_V2=true` is still set
2. Check that `LANGCHAIN_API_KEY` is valid (LangSmith may have rotated keys)
3. Check the LangSmith status page for an outage
4. Tracing failures don't break execution — workflows continue to run; you just lose the trace data

## Coordinating with the customer

For Sev-1 with material customer impact:

- **Set up a dedicated war-room channel** (Slack Connect / Teams / shared Zoom). The channel persists across the incident; updates flow there.
- **Update at the cadence in [`../saas/sla.md`](../saas/sla.md)** — every 1 hour for Sev-1, every 4 hours for Sev-2. Even "no progress, still investigating" is better than silence.
- **Don't speculate on root cause** until you have evidence. Premature attribution backfires when the real cause turns out to be different.
- **Be honest about ETA**: "We don't have an ETA yet" is better than a date you'll miss.
- **Summarise at the end** with a clear "resolved" message and a commitment to a postmortem.

## What we don't do during incidents

- **Don't disable monitoring** to silence noise. Investigate the noise; that's signal.
- **Don't push speculative fixes** to production without testing — your "fix" can become the next incident.
- **Don't deflect blame to the customer** unless their action is genuinely the cause. Even then, be careful with the language.
- **Don't promise features as compensation** — service credits per the SLA, not improvised offers.
- **Don't hide the postmortem** — internal versions can stay internal; the customer-facing version is shareable.

## Adjacent docs

- [`../saas/sla.md`](../saas/sla.md) — severity classification + response targets
- [disaster-recovery.md](disaster-recovery.md) — recovery procedures referenced from mitigation steps
- [observability.md](observability.md) — what to look at while investigating
- [`../saas/support.md`](../saas/support.md) — the customer-facing comms channels
- [`../archive/incident-history/`](../archive/incident-history/) — past postmortems for pattern recognition
