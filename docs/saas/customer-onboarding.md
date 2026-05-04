# Customer Onboarding

> **Audience:** new customer admins + their first wave of designers + end users.
> **Maintained by:** Balaji Rajan (`balajirajan@gmail.com`)
> **Goal:** from contract signed to first production workflow running in seven business days.

This is the reference timeline we work to. Your specific onboarding may compress or expand based on your team's availability and the scope of your first workflow.

## Day 0 — Contract signed

Before we touch any infrastructure, the contract is signed and these prerequisites land:

- [x] Master agreement, DPA, and (if applicable) BAA executed
- [x] Plan tier confirmed (see [pricing.md](pricing.md))
- [x] Deployment region confirmed (per [privacy.md](privacy.md) data residency)
- [x] Primary technical contact identified — name, email, timezone
- [x] Secondary contact identified for incident escalation
- [x] Support channel established (Slack Connect / Teams / dedicated email per [support.md](support.md))
- [x] Initial deployment shape decided — managed single-tenant vs. self-hosted vs. embedded

## Day 1 — Provisioning (managed customers) or kickoff (self-hosted)

### Managed single-tenant

We provision your environment:

1. **Postgres database** in your selected region (Neon EU / US / APAC), branched off the latest schema.
2. **Backend hosting** in the same region (Fly / Render / your container host of choice).
3. **Frontend hosting** on Vercel in the same region pool.
4. **Encryption keys** (`ENCRYPTION_KEY`, `JWT_SECRET`) generated per-deployment, stored only in the host's secret store.
5. **Health checks + monitoring** wired (Pingdom / your monitor; LangSmith if you've enabled tracing; Vercel logs).
6. **First admin user** seeded — typically the primary technical contact's email.
7. **Status page entry** added so the deployment shows up on the public board.

You receive:
- The deployment URL (e.g. `composer.your-org.example.com`)
- The first admin's temporary password (rotate it immediately on first sign-in) **or** an Azure AD sign-in link if SSO is configured up-front
- The Slack Connect channel invite

### Self-hosted

You receive:
- Repo access if you've forked privately, or the public repo link to follow
- A 1-hour kickoff call to walk through the deployment runbook ([`../operations/production-deployment.md`](../operations/production-deployment.md))
- Templates for the env vars and infrastructure-as-code stubs

You provision in your own VPC; we're available on the support channel for questions.

## Day 2 — First sign-in + admin tour

The primary admin signs in and walks through:

1. **Verify the deployment is healthy** — `GET /health` returns 200, the canvas loads, the runs page loads.
2. **Add LLM API keys** — at minimum one provider (Anthropic / OpenAI / Google / Groq). Admin → LLM keys → Add → paste → Test (the Test button issues a 1-token call to confirm the key works).
3. **Verify default models** — Admin → LLM models → click Verify on each row. Disabled rows disappear from designer dropdowns. The auto-disable on `unavailable` ([decisions.md](../decisions.md)) is what closes "designer adds a retired model" forever.
4. **Configure SSO if not yet done** — admin guide → Azure SSO runbook ([`../operations/azure-sso.md`](../operations/azure-sso.md)). Enterprise plans almost always do this on day 1.
5. **Promote a second admin** — Admin → Users → row → Toggle role. Two admins is the minimum operational shape (for vacations, sickness, etc.).
6. **Read the admin guide quickly** — [`../admin-guide.md`](../admin-guide.md). It's intentionally concise; 15 minutes covers the lifecycle of every admin task you'll do.

By end of Day 2 you should be able to: sign in, see the empty Designer / Runs / Admin pages, and have at least one verified LLM model.

## Day 3 — First designer onboarding

Identify a designer (someone who will build workflows) and walk them through:

1. **Templates gallery** — `/designer/templates`. The 17 reference templates are the recommended starting point; each demonstrates a specific capability.
2. **Pick the first template** — for most teams, **"Example 1: Simple Agent"** is the right first run. It's a one-node workflow, takes 30 seconds to clone and run, validates the LLM key, and gives the designer a feel for the canvas + Run Draft panel.
3. **Run it** — Save → Run Draft → enter a question → watch the live execution panel. The agent node turns purple while running, green when complete. Output appears in the panel.
4. **Tweak it** — change the prompt, save, run again. Add an input variable on the Start node and reference it in the agent prompt. This is the core editing loop; designers should feel comfortable here before they touch anything more complex.
5. **Read the designer guide** — [`../designer-guide.md`](../designer-guide.md). It walks through every node type with examples.

We recommend designers spend a couple of hours here on Day 3 before moving on. The designer guide is dense; better to get the canvas-fluency baseline first, then read the guide as a reference when needed.

## Day 4 — Real workflow scoping

Working session (1 hour, with us if you've taken the included onboarding hours, or with your designer alone):

1. **Pick the first production workflow.** What does it do? What's the input shape? What's the output? Who calls it?
2. **Look at templates that match the pattern** — RAG, classify-and-branch, multi-source research, etc. The templates aren't fits for every problem; they're starting points.
3. **Identify dependencies** — which LLM provider, which tools (Tavily / Firecrawl / Browserless / Gamma / Arcade), which MCP servers, which vector DB, which data inputs.
4. **Provision the dependencies** — admin adds any missing tool keys, MCP servers, vector DB credentials.
5. **Sketch the workflow on paper** before opening the canvas. Designers who skip this step typically rebuild three times.

By end of Day 4 you have a clear path from input to output, the dependencies in place, and a paper sketch of the node graph.

## Day 5 — Build + iterate

Designer builds the workflow on the canvas:

1. **Start node configures inputs** — variable names, types (text / number / boolean / json / document), defaults, descriptions for the run-input form.
2. **Add nodes one at a time** — name each one (this becomes the alias `{{node_name}}` for downstream references). Run the partial workflow after each addition; if the partial doesn't work, the full one won't either.
3. **Save often.** Save commits the workflow row; it doesn't trigger a run.
4. **Run Draft repeatedly.** The execution panel shows live progress + per-node input/output; this is where you debug.
5. **Watch the LangSmith trace** if you've configured tracing — the prompt + tool-call detail there is unmatched.

Common stumbling blocks (with the patterns):

- **`{{name}}` references render literally** — the alias is `{{snake_case_name}}` (the node's `Name` field, not the technical id).
- **Agent node fails with "no tools available"** — make sure you've toggled the tools you want in the agent panel; nothing is on by default.
- **MCP node returns nothing useful** — check the MCP server is `enabled` and the OAuth flow is complete (admin console → MCP servers).
- **Vector DB query returns empty** — confirm the index has data; the upsert happens in a separate workflow (template 12 is the reference pattern).

By end of Day 5 the workflow either runs end-to-end on a small test case or you have a clear error you're working through.

## Day 6 — Publish + integrate

Once the workflow runs, publish it:

1. **Designer**: open the workflow's Settings → Publish → pick a URL-safe slug (e.g. `lead-enrichment`). The published external-invoke URL appears on the canvas top bar.
2. **End user**: open `/runs/api-keys` → Generate a new API key. Copy the `ck_...` value (shown once).
3. **Test the external invoke** with curl:

```bash
curl -X POST "https://composer.your-org.example.com/api/run/lead-enrichment" \
  -H "Authorization: Bearer ck_YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"input": {"company": "Acme Corp"}, "sync": true}'
```

Async by default (`"sync": false` or omitted) returns immediately with a stream URL; sync waits up to 300 seconds for the result.

4. **Wire the integration** — your application calls the external-invoke endpoint with whatever inputs your workflow needs. The full API contract is in [`../api-reference.md`](../api-reference.md).

## Day 7 — Production handover

Final checklist before declaring "we're live":

- [ ] At least two admins on the deployment
- [ ] LLM keys verified, with the relevant models marked enabled
- [ ] SSO configured (or, for standalone deployments, password policy understood)
- [ ] Workflow published with a stable slug
- [ ] At least one API key issued for production traffic
- [ ] Run history visible at `/runs` for the team that will operate the workflow
- [ ] LangSmith tracing enabled (highly recommended) and pointing at the customer's LangSmith project
- [ ] Status page subscribed to (managed customers)
- [ ] Incident-response escalation tested (at minimum, a Sev-3 ticket round-trip)
- [ ] Designer team has read [`../designer-guide.md`](../designer-guide.md) sections relevant to their workflows
- [ ] Admin has read [`../admin-guide.md`](../admin-guide.md) end-to-end
- [ ] Backup + restore procedure rehearsed mentally — even a self-host customer should have walked through [`../operations/disaster-recovery.md`](../operations/disaster-recovery.md) once

If everything's checked, you're live. The service-level commitments in [sla.md](sla.md) apply.

## Beyond Day 7

- **Week 2**: review token spend in admin → LLM keys vs. what you forecasted. Adjust if surprised.
- **Week 4**: review execution success rate (Runs page filter on `failed`). Anything systematic? Investigate.
- **Month 2**: revisit your first workflow with the designer. With a month's hindsight, what would you build differently? Refactor.
- **Quarter 1**: the quarterly business review (Business + Enterprise) — what's working, what isn't, what's on your roadmap.

## Common questions during onboarding

**Q: Can we start with self-hosted and migrate to managed later?**
Yes. The migration is "export workflows from your self-hosted instance, import into the managed instance." We provide a migration script for managed customers; for self-hosted-to-self-hosted moves the operational steps are in [`../operations/`](../operations/).

**Q: Can multiple designers work on the same workflow simultaneously?**
Not today — last-save-wins. Designers usually work on different workflows in parallel and review each other's via the templates / clone path. Real-time collaborative editing is on the speculative roadmap.

**Q: Should we use SSO from day 1?**
For managed Business + Enterprise, yes — Azure AD setup is 30 minutes ([`../operations/azure-sso.md`](../operations/azure-sso.md)) and avoids the password-management overhead. For Starter or self-hosted POCs, standalone username/password is fine for the first month.

**Q: How do we handle two designers wanting to test prompt changes simultaneously?**
Each designer clones the production workflow into a private draft, edits there, and only the validated draft gets re-published. Workflow versioning ([roadmap.md](roadmap.md)) will simplify this.

**Q: When should we move to LangSmith?**
Before your first Sev-1. The prompt-by-prompt trace LangSmith provides is the single biggest lever on debug speed. It's optional but we recommend it.

## Adjacent docs

- [overview.md](overview.md) — what Composer is, before you start clicking through it
- [`../getting-started.md`](../getting-started.md) — the technical 30-minute version of Day 1
- [`../designer-guide.md`](../designer-guide.md) — the reference Designer needs from Day 4 onward
- [`../admin-guide.md`](../admin-guide.md) — the reference Admin needs from Day 2 onward
- [support.md](support.md) — when you're stuck and need help
