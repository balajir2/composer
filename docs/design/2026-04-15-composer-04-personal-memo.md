# Composer — Personal Working Memo

**Audience:** Balaji (and no one else, unless I choose to share)
**Purpose:** Unvarnished working thoughts — what I'm sure of, what I'm worried about, what decisions I'm not yet confident in
**Date:** 2026-04-15

---

## What I'm Confident About

- **The wedge is real.** Coded agents on enterprise scaffolding is a product; adding visual composition on top turns it into a platform. That distinction matters for ARR multiples, sales motion, and competitive defensibility.
- **Python port is disciplined work, not R&D.** The code works. I debugged it end-to-end for Highspot OAuth over ~48 hours. I know what the gotchas are. Porting to Python is a re-implementation, not a discovery project.
- **Composer as a name fits.** Completes the musical theme. Will be memorable inside the org.
- **Module in Phase 1 is the right risk level.** Anything deeper (e.g., Composer as Core) ties us to IE architectural decisions we don't yet control.
- **The three personas (B, C, D) actually converge on one product.** Tenant admins, business analysts, and platform ops all want the same primitive: visually compose IE's coded agents into custom workflows. The same UI serves all three.

---

## What I'm Not Sure About

- **Whether IE's MCP orchestrator already has OAuth 2.0 with RFC 8707.** If yes, life is easy. If no, we're doing that work twice (once for Composer, and arguably we should upstream it to the orchestrator). This changes the effort shape meaningfully.
- **Whether IE's WebSocket protocol covers SSE-style live streaming of workflow events.** The protocol exists (`DES-007`), but is it opinionated about event shapes? If yes, we may need to reshape OAB's event vocabulary. If no, fine.
- **Whether the IE team has the LangGraph/LangChain expertise for a clean port.** `DES-025-langchain-langgraph-integration.md` suggests yes, but the depth matters. If they're using LangChain superficially, the port will be slower.
- **Whether we'll lose the existing Open Agent Builder customer base in the transition.** We agreed OAB continues as a separate product — but product teams are always tempted to converge. I don't want that.
- **Whether the "faster GTM" pressure will tempt us to skip Phase 1.4 (tenancy/RBAC integration) or Phase 1.5 (remaining nodes).** Both are integration-critical. Skipping them makes a demo work but not a product.

---

## What Keeps Me Up at Night

1. **The two-codebase problem.** We now have OAB in TypeScript and Composer in Python. Bug fixes diverge. New features diverge. Without discipline, we end up with two products that drift apart and require double maintenance. Who owns that discipline? What's the sync policy?
2. **The IE team not adopting it.** We can ship Composer. IE's tenants can see it. But if IE's sales team doesn't pitch it, and IE's platform team doesn't maintain it, it dies in the monorepo.
3. **Regression via port.** ~5,000 lines of executor logic. Every LLM provider has quirks. Every MCP server has quirks. A disciplined port still introduces regressions. The OAB team should be behavioral oracles for the first 6 months.
4. **Convex departure pain.** Convex is really nice for real-time. Postgres + WebSocket is fine but requires more scaffolding. The gap between "it works in dev" and "it works at scale for 500-seat tenants" is bigger on Postgres than on Convex.
5. **Scope creep from the IE plugin contract.** IE has a plugin architecture (`DES-013`, `DES-014`, `DES-024`). If Composer is forced to fit the plugin contract too strictly, we lose some of the execution model flexibility LangGraph needs. I don't fully understand their contract yet.

---

## Decisions I Want Gut-Checks On

### Decision 1: Composer or Score?
I recommended **Composer**. It's the obvious musical fit and describes what the product does. But "Score" is shorter, punchier, and the written artifact metaphor is strong — a score is what you write before the chorus performs it. I think Composer is right because of the human-noun angle (a person composes), but I'd take feedback.

### Decision 2: SQLAlchemy or Prisma for composer-service?
I recommended **SQLAlchemy**. Prisma Python is newer; SQLAlchemy is battle-tested and has existing patterns for LangGraph checkpointers. But IE already uses Prisma elsewhere, and a split might create friction. If IE says "all Python services use Prisma," I yield immediately.

### Decision 3: SSE now, WebSocket later?
I recommended **SSE first, WebSocket in Phase 1.4**. SSE is simpler and lets us ship faster. But IE's standard is WebSocket (`DES-007`). If the team says "no SSE, start with WebSocket," I yield; the extra week is worth architectural consistency.

### Decision 4: Keep OAB as a separate product indefinitely?
We said yes, but the longer Composer lives, the more tempting it becomes to converge. At some point, someone will propose shutting down OAB. My gut: **OAB stays as long as there are non-enterprise users who benefit from a standalone low-code agent builder** (teams smaller than "need IE-grade governance"). Drop it when that audience dries up.

### Decision 5: Do we include the UI Builder in Phase 1?
I recommended **no**. It's a different product (OAB's drag-drop UI creator). But someone will push for it because it's visible in OAB's repo. The decision cost of including it is +2-3 weeks and a meaningfully larger surface area. I'd defer to Phase 2 or later and be firm about it.

---

## What the Execs Will Ask That I Don't Have Clean Answers For

- **"What's the revenue model?"** — I sketched tiered platform pricing in the exec doc, but honestly this is speculative. We need product marketing to run a real exercise.
- **"When's first customer demo?"** — I said "next quarter" in the exec doc as a round number. The phased plan supports it if nothing goes wrong; I'd add 20% buffer before committing.
- **"Why not just buy a product that does this?"** — There aren't great answers here. Zapier-for-AI startups exist. Differentiator is the enterprise wrapper and IE-specific integrations. Honest answer: buy-vs-build framing depends on whether IE becomes a strategic asset or a tactical one.
- **"What happens to the IE engineers' existing workload when Composer lands?"** — Some agents will migrate from coded → composed. That's a re-org of engineering effort, not necessarily a reduction. The story isn't "less engineering," it's "different engineering."

---

## What the IE Engineers Will Push Back On

- **"Why not just embed OAB as an iframe?"** — Too loose; auth and tenancy can't flow cleanly. Also defeats the whole "enterprise wrapper" thesis.
- **"Why not expose OAB as an API and call it from Chorus?"** — Same objection. Plus latency, plus two codebases that drift.
- **"Can't we just build this in TypeScript to match the rest of the UIs?"** — The UI *is* TypeScript. Only the executor is Python, and it matches IE's existing agent pattern. The executor in TypeScript is a double maintenance burden because OAB already exists there.
- **"This is too much new surface area."** — Fair. Ship module-first, defer features aggressively (UI Builder, Arcade, Gamma). Scope control is how we get to a demo in 13-14 weeks.
- **"How does this affect the plugin architecture?"** — It doesn't, in Phase 1. Composer is a standalone module. In Phase 3, Composer-built workflows may become a new kind of plugin. That's a future conversation.

---

## GTM Reality Check

The exec doc has a nice story. The reality:

- **Sales cycles are slow.** "Faster GTM" doesn't mean revenue in a quarter. It means the first demo and the first signed pilot. Real revenue from Composer is 2+ quarters out from first demo.
- **Customers don't ask for "low-code" features by name.** They ask for specific things ("I need the AC Validator to post to Slack"). Composer lets us answer those asks without coding. Frame it that way, not as a feature.
- **Competition exists.** LangFlow, Flowise, n8n (with AI nodes), LangSmith Studio. None have IE's enterprise wrapper, but all are faster to try. Positioning has to hammer enterprise governance + IE-specific integrations.
- **Internal competition.** Bounteous engineering might be skeptical of low-code. "Why should I compose a workflow when I can just code the agent?" Answer: because the customer isn't you. They can't code. That's the whole point.

---

## My Honest Recommendation

- **Ship Phase 1.0 + 1.1 (walking skeleton + execution engine core) as a forcing function.** Two weeks of work to prove the port is viable. If that's painful, we pause and re-plan. If it's clean, we accelerate.
- **Keep Open Agent Builder running in parallel — don't starve it.** The OAB team should continue shipping fixes. Composer inherits those as reference changes.
- **Have one human be accountable for Composer.** Not a committee. Someone who can say "defer Arcade, defer Gamma, defer UI Builder, ship 12 nodes."
- **Treat the April 2026 Highspot debugging session as the canonical acceptance test.** If Composer can connect to Highspot and complete the same workflow OAB does, the port is real.

---

## Open Questions I Need Help With

1. Does IE's MCP orchestrator already support OAuth 2.0 with RFC 8707? (Ask: Platform Engineering lead)
2. Is there a specific customer / deal driving the "faster GTM" urgency? (Ask: Sales / Account Exec)
3. Who's accountable for Composer inside IE — Product, Platform, or Agents team? (Ask: Head of IE)
4. What's the budget / headcount shape for this work? (Ask: Engineering Manager)
5. How much of `agent-commons` is ready-for-reuse for MCP client, LLM integration, sessions? (Ask: IE engineer on `agent-commons`)
