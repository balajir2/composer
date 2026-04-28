# Archive

Material kept for traceability. **Don't read this to understand the current system** — read [`../README.md`](../README.md) and follow the role-based index.

## When to dig in here

| Want to understand... | Look at |
|---|---|
| ...why Composer is a Python rebuild of OAB rather than a port or a continuation | [`design-history/2026-04-20-composer-python-port-design.md`](design-history/2026-04-20-composer-python-port-design.md) |
| ...the strategic backstory: what was proposed, what David Lawton pushed back on, why we pivoted | All six `design-history/2026-04-15-*.md` files + `design-history/ie-analysis/` |
| ...what was originally planned for a specific Phase (vs what shipped) | `phase-history/specs/<phase>-design.md` and `phase-history/plans/<phase>-plan.md` |
| ...the OAB knowledge dump that informed Composer's design | [`design-history/2026-04-15-composer-06-oab-knowledge-dump.md`](design-history/2026-04-15-composer-06-oab-knowledge-dump.md) — covers all 18 node types, MCP OAuth, the six April 2026 critical fixes, porting checklist |

## What's here

```
archive/
├── design-history/
│   ├── 2026-04-15-composer-01-oab-reference-architecture.md   ─┐
│   ├── 2026-04-15-composer-02-executive-vision.md              │
│   ├── 2026-04-15-composer-03-engineering-blueprint.md         │ Initial 2026-04-15
│   ├── 2026-04-15-composer-04-personal-memo.md                 │ brainstorming round
│   ├── 2026-04-15-composer-05-end-state-user-journey.md        │
│   ├── 2026-04-15-composer-06-oab-knowledge-dump.md           ─┘
│   ├── 2026-04-15-composer-*.docx                              ← email-ready DOCX copies
│   ├── 2026-04-20-composer-python-port-design.md               ← The pivot design (active during build)
│   └── ie-analysis/
│       ├── OAB-Composer-Critical-Analysis.md                   ← David Lawton's 2026-04-20 critique
│       └── OAB Composer - Critical Analysis.docx
└── phase-history/
    ├── specs/                                                  ← 16 phase design specs
    │   ├── 2026-04-20-phase-1-execution-engine-design.md
    │   ├── 2026-04-20-phase-2-agent-executor-design.md
    │   ├── ... (one per phase 1–10)
    │   └── 2026-04-22-phase-10-composer-frontend-design.md
    └── plans/                                                  ← 16 phase implementation plans
        ├── 2026-04-20-phase-1-execution-engine-plan.md
        ├── ... (one per phase)
        └── 2026-04-23-phase-10-composer-frontend-plan.md
```

## Why these aren't deleted

**Design-history** captures *why* Composer exists and why specific architectural choices were made. The 2026-04-15 round documents the original proposal that was scoped as "Composer as IE module"; David Lawton's response reshaped the project; the 2026-04-20 design is what we executed against. Without these, decisions in [`../decisions.md`](../decisions.md) lose their context.

**Phase-history** is the implementation trail. Each phase's spec + plan was the contract between design and implementation; the resulting code + ADR is what's authoritative now. Keep these for: reconstructing why a particular trade-off was made, reproducing the test plans for a phase's regression suite, onboarding someone who wants to learn the codebase by following its evolution.

**Update policy**: this material is frozen. If something here is wrong or outdated, add an ADR to [`../decisions.md`](../decisions.md) explaining the new state and link both ways. Don't edit the archived docs.
