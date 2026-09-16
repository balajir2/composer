# Monitoring

**Audience:** platform ops/SRE. Covers LangSmith traces, Vercel logs and log drains, error rate
monitoring, and post-cutover watchlist.

---

## 1. LangSmith tracing

LangSmith captures every LangGraph execution trace — node-by-node inputs, outputs, token counts,
latency, and errors — without code changes beyond setting two env vars.

### Setup

1. Sign in at [smith.langchain.com](https://smith.langchain.com).
2. Create a project named `composer-production` (or match `LANGCHAIN_PROJECT` in your env vars).
3. In Vercel env vars (or your local `.env`), set:

```
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=lsv2_pt_...     # from LangSmith → Settings → API Keys
LANGCHAIN_PROJECT=composer-production
LANGCHAIN_ENDPOINT=https://api.smith.langchain.com
```

`LANGCHAIN_API_KEY` is stored in Postgres and synced to Vercel via `composer keys sync` (provider
name: `langsmith`). See [llm-keys.md §3](llm-keys.md).

4. Trigger a Vercel redeploy after setting env vars.

### What you get

- Every `POST /executions` call creates a LangSmith run with a tree of node runs.
- Agent nodes show the full prompt, tool calls, and LLM response.
- MCP tool calls appear as sub-runs with their input/output.
- Guardrail evaluations appear as annotated runs.
- Errors (node failures, LLM provider errors, timeout) are tagged automatically.

### Filtering traces

In the LangSmith UI, filter by:
- **Project:** `composer-production`
- **Status:** `error` — shows all failed runs
- **Tags:** set in `LANGCHAIN_PROJECT`; ops can add custom tags by extending executor code (Phase 10 enhancement)
- **Date range:** narrow to the cutover window to monitor migration-period executions

### Disabling tracing

Set `LANGCHAIN_TRACING_V2=false` and redeploy. All other LangSmith env vars can remain — they have
no effect when tracing is disabled.

---

## 2. Vercel runtime logs

Composer's FastAPI app logs to stdout via Python's `logging` module. All log lines appear in the
Vercel function logs.

### Viewing logs in the Vercel dashboard

Vercel dashboard → project → **Logs** tab. Filter by:
- **Function:** all (or select a specific function route)
- **Level:** shows stdout (INFO, WARNING, ERROR) and stderr
- **Time range:** up to 24 hours of real-time logs

Logs include:
- FastAPI request logs (method, path, status, duration)
- LangGraph execution events (node start/complete/error)
- Auth failures
- Rate limit rejections

### Log format

Composer uses Python's default logging format. Set `LOG_LEVEL=DEBUG` in Vercel env vars for verbose
output during incident investigation. Revert to `INFO` after.

### Setting up a log drain

For persistent log storage and alerting, configure a Vercel log drain to forward logs to your
preferred observability platform.

**Supported targets:** Datadog, New Relic, Logtail, Papertrail, or any HTTPS endpoint accepting
NDJSON.

To configure:

1. Vercel dashboard → project → **Settings** → **Log Drains** → **Add Drain**.
2. Enter your target endpoint URL and any required auth header.
3. Select **Source:** `runtime` (to capture function logs) + optionally `static` and `build`.
4. Save. Vercel begins forwarding logs immediately.

For Datadog:

```
Endpoint:  https://http-intake.logs.datadoghq.com/v1/input/<DD_API_KEY>
Headers:   DD-EVP-ORIGIN: vercel
Format:    NDJSON
```

For Logtail (Simple Analytics):

```
Endpoint:  https://in.logs.betterstack.com
Headers:   Authorization: Bearer <LOGTAIL_SOURCE_TOKEN>
Format:    NDJSON
```

---

## 3. Error rate monitoring

### Using Vercel's built-in observability

Vercel Pro/Enterprise includes an **Observability** tab with:
- Request volume by function
- Error rate (4xx, 5xx) over time
- P50/P90/P99 latency
- Cold start frequency

This requires no additional configuration.

### Using an external APM

If your org uses Datadog, New Relic, or Sentry, configure the relevant SDK or log parsing.

**Sentry (lightweight option):**

1. Create a Sentry project for `composer`.
2. Set `SENTRY_DSN=https://...@sentry.io/...` in Vercel env vars.
3. Add `sentry-sdk[fastapi]` to `pyproject.toml` dependencies and initialize in `src/main.py`:

```python
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration

sentry_sdk.init(dsn=settings.sentry_dsn, integrations=[FastApiIntegration()])
```

This is a Phase 10 enhancement; not included in Phase 9. For Phase 9, rely on Vercel's built-in
observability + LangSmith for error visibility.

### Key error signals to alert on

| Signal | Threshold | Likely cause |
|---|---|---|
| HTTP 5xx rate | > 1% of requests | App crash, Postgres connectivity, missing env var |
| HTTP 401/403 rate spike | > 20% sudden increase | JWT secret rotation without session invalidation notice |
| HTTP 429 rate spike | > 10% of requests | Rate limit hit; investigate source IP / user |
| LangSmith error runs | > 5% of traces | LLM provider outage, key expiry, MCP connectivity |
| `POST /executions` p99 latency | > 30s | LLM slow response, MCP tool timeout, Postgres slow query |

---

## 4. Rate-limit observability

Composer's rate limiter is **Postgres-backed** (`rate_limit_buckets` table, `PostgresRateLimiter` —
ADR-0033/P1-4), an atomic token-bucket per `(route, client)` pair. It is correct across every
backend instance — Cloud Run's multi-instance scaling, multiple Vercel function instances, whatever
the deployment shape — since bucket state lives in the shared database, not per-process memory.

**Implications for observability:**

- There is one source of truth for rate-limit state: query `rate_limit_buckets` directly if you need
  to inspect a specific caller's remaining tokens.
- A rate-limit hit (HTTP 429) is logged as a warning: `Rate limit exceeded: <endpoint> for <user-id or IP>`.
- Filter backend logs for `429` or `Rate limit exceeded` to detect sustained rate-limit pressure.
- Because state is shared (not per-instance), the configured limit is the *actual* effective limit
  per caller — no multiplication by instance count, and no under-counting from cold starts resetting
  in-memory state.

**Configured limits (from `src/config.py`):**

| Endpoint group | Default limit | Config variable |
|---|---|---|
| `POST /executions` | 30/min | `RATE_LIMIT_EXECUTIONS_PER_MINUTE` |
| `POST /auth/login` | 10/min | `RATE_LIMIT_LOGIN_PER_MINUTE` |
| `POST /auth/register` | 5/min | `RATE_LIMIT_REGISTER_PER_MINUTE` |
| `POST /auth/refresh` | 30/min | `RATE_LIMIT_REFRESH_PER_MINUTE` |
| `POST /executions/{id}/resume` | 60/min | `RATE_LIMIT_RESUME_PER_MINUTE` |
| `POST /mcp-servers/{id}/test` | 10/min | `RATE_LIMIT_MCP_TEST_PER_MINUTE` |

These are `Settings` fields read from env vars at startup. To adjust limits without a code change,
update the env var on the backend host and redeploy/restart.

---

## 5. Post-cutover watchlist

Run through this checklist in the 48 hours after cutover from OAB to Composer.

### Immediately after cutover (hour 0–2)

- [ ] `GET /health` returns 200 from the production URL.
- [ ] At least one test execution completes successfully end-to-end (POST /executions → WebSocket
  events → `completed` status).
- [ ] LangSmith shows traces from the production project (at least one run visible).
- [ ] LLM keys are active: attempt a single-agent workflow with the Anthropic provider; confirm no
  auth errors in LangSmith.
- [ ] Vercel logs show no startup errors or repeated panics.
- [ ] Admin user can log in and access `GET /admin/llm-keys`.

### First 24 hours

- [ ] At least 3 users reconciled (run `composer reconcile --email X` for first arrivals; confirm
  their workflows appear).
- [ ] No unexpected spike in 5xx responses (< 1% target).
- [ ] No `mcp_oauth_tokens` decryption errors reported by users (MCP connections working).
- [ ] Rate limits not triggering for normal usage patterns (check Vercel logs for 429s).
- [ ] Neon connection count not approaching the database's max_connections limit (check Neon console
  → Monitoring → Connections).

### First 48 hours

- [ ] All expected users reconciled or have registered and can see their data.
- [ ] Any user-reported "I can't see my workflows" issues resolved via reconcile (see
  [admin-operations.md §6](admin-operations.md)).
- [ ] LangSmith error rate < 5% of traces.
- [ ] WebSocket connections stable (check Vercel logs for websocket close code 1011 = internal
  error).
- [ ] PITR backup window confirmed: verify Neon console shows the last 7 days of WAL retention.
- [ ] LLM key rotation scheduled: set a calendar reminder for the next rotation cycle (e.g., 90
  days).

### Ongoing monitoring rhythm

- **Daily:** Vercel error rate dashboard — confirm 5xx rate < 1%.
- **Weekly:** LangSmith error runs — review and triage any recurring failure patterns.
- **Monthly:** LLM API key rotation across all providers (see [llm-keys.md §5](llm-keys.md)).
- **Quarterly:** Neon PITR restoration drill — restore a snapshot to a test branch; confirm data
  integrity.

---

## Cross-references

- [vercel-setup.md](vercel-setup.md) — Vercel project setup, log drain configuration point
- [postgres-setup.md](postgres-setup.md) — Neon console, PITR, connection monitoring
- [llm-keys.md](llm-keys.md) — LangSmith key management; key rotation
- [admin-operations.md](admin-operations.md) — reconciliation, user triage
