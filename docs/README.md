# Composer Documentation

## Start here

**For the current design and plan:** [`design/2026-04-20-composer-python-port-design.md`](design/2026-04-20-composer-python-port-design.md)

This is the active design spec. All other documents are context or historical.

## Directory layout

```
docs/
├── design/          # Composer design specs (historical + active)
└── ie-analysis/     # Intelligent Engineering team's analysis
```

---

## `design/` — Composer design specs

### Active
- **[`2026-04-20-composer-python-port-design.md`](design/2026-04-20-composer-python-port-design.md)** — Current design. Phased rebuild plan, stack decisions, governing rules, completion criteria, risks. **Read this first.**

### Historical (from 2026-04-15 — superseded in part by the 2026-04-20 design)
These six documents were produced during the initial Composer brainstorming round, before David Lawton's critique and our pivot to "clean-break rebuild on IE's stack":

- [`2026-04-15-composer-01-oab-reference-architecture.md`](design/2026-04-15-composer-01-oab-reference-architecture.md) — Technical reference describing OAB's architecture for an IE engineering audience
- [`2026-04-15-composer-02-executive-vision.md`](design/2026-04-15-composer-02-executive-vision.md) — Strategic narrative for Composer inside IE ("platform → product" thesis)
- [`2026-04-15-composer-03-engineering-blueprint.md`](design/2026-04-15-composer-03-engineering-blueprint.md) — Original "Composer as IE module" engineering design (now superseded; see 2026-04-20 design)
- [`2026-04-15-composer-04-personal-memo.md`](design/2026-04-15-composer-04-personal-memo.md) — Balaji's personal working memo (risks, open questions, gut checks)
- [`2026-04-15-composer-05-end-state-user-journey.md`](design/2026-04-15-composer-05-end-state-user-journey.md) — Vivid user-journey scenarios (Priya, Marcus, Aiyana)
- [`2026-04-15-composer-06-oab-knowledge-dump.md`](design/2026-04-15-composer-06-oab-knowledge-dump.md) — Comprehensive OAB knowledge base for a Claude agent to consume; covers all 15 node types, MCP OAuth, six April 2026 critical fixes, porting checklist

Each design doc has both `.md` (canonical) and `.docx` (for email-ready sharing with stakeholders).

---

## `ie-analysis/` — IE team analysis

- **[`OAB-Composer-Critical-Analysis.md`](ie-analysis/OAB-Composer-Critical-Analysis.md)** — David Lawton's (IE owner) critical analysis of the 2026-04-15 Composer vision (received 2026-04-20). Key input that drove the current plan.
- [`OAB Composer - Critical Analysis.docx`](ie-analysis/OAB%20Composer%20-%20Critical%20Analysis.docx) — Original Word document (preserved)

## Related context outside this repo

- **Open Agent Builder repo:** `D:/GitHub/open-agent-builder` — read-only reference for behavioral parity. Do NOT commit there.
- **OAB design docs (original home):** `D:/GitHub/open-agent-builder/docs/superpowers/specs/` — the design docs listed above were originally committed there. Copies here are kept in sync by the human at major revisions.
