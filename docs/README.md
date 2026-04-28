# Composer Documentation

Pick the doc that matches what you're trying to do.

## I want to...

| ...do this | ...read this |
|---|---|
| **Understand what Composer is and why it exists** | [overview.md](overview.md) |
| **Run Composer locally and build my first workflow** | [getting-started.md](getting-started.md) |
| **Understand how the platform works internally** | [architecture.md](architecture.md) |
| **Build workflows in the Designer (nodes, variables, templates, publishing)** | [designer-guide.md](designer-guide.md) |
| **Manage users, LLM keys, MCP servers, deployment settings** | [admin-guide.md](admin-guide.md) |
| **Deploy and operate Composer in production** | [operations.md](operations.md) |
| **Call Composer's HTTP API or external-invoke endpoint** | [api-reference.md](api-reference.md) |
| **Understand a specific engineering decision** | [decisions.md](decisions.md) (23 ADRs) |
| **See what's changed recently** | [/CHANGELOG.md](../CHANGELOG.md) |

## By role

- **First-time visitor / stakeholder** — start with [overview.md](overview.md), then skim [architecture.md](architecture.md).
- **Workflow designer / business user** — [getting-started.md](getting-started.md) → [designer-guide.md](designer-guide.md).
- **Engineer adding a new node or fixing a bug** — [architecture.md](architecture.md) → [decisions.md](decisions.md). Phase-specific design specs are under [archive/phase-history/](archive/phase-history/).
- **Admin** — [admin-guide.md](admin-guide.md).
- **Ops / SRE deploying Composer** — [operations.md](operations.md).
- **API consumer building an integration** — [api-reference.md](api-reference.md).

## Where the historical material went

`docs/archive/` holds material that was load-bearing during the rebuild but is no longer needed for daily reference:

- **`design-history/`** — the six 2026-04-15 brainstorming docs, the 2026-04-20 Python port design, and David Lawton's IE critique. Useful for understanding *why* Composer is the way it is.
- **`phase-history/`** — phase-by-phase implementation specs (`specs/`) and plans (`plans/`) from Phases 1–10. The code that resulted is what's authoritative now; these are kept for traceability.

If you find yourself reading archived material to understand the current system, that's a documentation bug — open a PR.
