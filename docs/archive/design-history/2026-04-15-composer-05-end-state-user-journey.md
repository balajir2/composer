# Composer — End-State User Journey

**Audience:** Mixed — useful for product, design, sales, and engineering to share a vivid picture of the end state
**Purpose:** Show how Composer feels to use once it's live inside IE. What do end users see? What do they do? How does a workflow get built, run, and monitored?
**Date:** 2026-04-15

---

## Cast of Characters

Three fictional users, each representing one of the personas we're serving:

- **Priya** — Tenant Admin at a Fortune 500 bank that uses IE. She manages which agents her 400-person engineering org can use, who has access to what, and occasional per-team workflow customizations. Technical but not a developer.
- **Marcus** — Business Analyst / Tech Lead on the bank's Payments Platform team. He doesn't write code; he writes user stories, reviews acceptance criteria, and coordinates with dev teams. He has Jira, Confluence, and Slack access.
- **Aiyana** — Bounteous Platform Operations Engineer. She maintains IE, ships new agents, and creates reusable workflow templates for enterprise customers.

---

## End-State Scene 1 — Priya Enables Composer for Her Tenant

Priya logs into Chorus (IE's tenant admin UI). Along with the usual tabs (Users, Agents, Integrations, Billing), there's a new tab: **Workflows**.

She clicks it. An intro page explains:

> *"Compose your own workflows by connecting agents, integrations, and LLM calls visually. Perfect for per-team customizations and automations that don't require custom engineering."*

She enables the Workflows module for her tenant and sets role permissions:
- `Tenant Admin` → full access
- `Tech Lead` → can view and edit workflows
- `Developer` → can view and execute (but not edit)
- `Observer` → can view only

She invites Marcus (Tech Lead role) and returns to her normal dashboard.

---

## End-State Scene 2 — Marcus Composes His First Workflow

Marcus gets Priya's email, logs into Cue (or navigates directly to Chorus), and clicks **Workflows → New Workflow**.

### The canvas

A blank canvas with a **Start** node in the upper-left. On the left, a **Node Palette**:

```
┌─────────────────┐
│  🎯 Start       │  (already on canvas)
│  🤖 Agent       │
│  🔌 MCP Tool    │
│  🌐 HTTP        │
│  🔀 If/Else     │
│  🔁 While       │
│  🧮 Transform   │
│  🔎 Extract     │
│  ✋ Approval    │
│  📝 Set State   │
│  🛡️ Guardrails  │
│  📄 Note        │
│  🏁 End         │
└─────────────────┘
```

### The task

Marcus wants to automate this flow:

> *"When a product manager drops a rough user story into a Jira ticket, auto-enhance it, validate its acceptance criteria, notify me in Slack for a quick sanity check, and — if I approve — update the ticket and post to a Confluence page summarizing this sprint's enhanced stories."*

### Step-by-step composition

1. **Start node**: Marcus configures an input variable — `jiraIssueKey` (string, required). This will be the Jira ticket ID.

2. **Drag a Agent node** and connect Start → Agent.
   - Panel opens on the right. Marcus picks **Story Enhancer** from the agent dropdown (populated from IE's Maestro agent catalog).
   - Input mapping: `issueKey = {{input.jiraIssueKey}}`.
   - Output stored as `enhancedStory`.

3. **Drag another Agent node**, connect prior → this one.
   - Picks **AC Validator** from the dropdown.
   - Input: `story = {{enhancedStory}}`.
   - Output: `validationResult`.

4. **Drag a Note node** ("This is the human review point") — documentation only.

5. **Drag an MCP Tool node** and connect.
   - Picks **Slack** (from IE's MCP orchestrator).
   - Tool: `post_message`.
   - Channel: `#story-reviews`.
   - Message template:
     ```
     {{enhancedStory.summary}}
     Validation: {{validationResult.status}}
     Approve? Reply ✅ or ❌
     ```
   - Output: `slackMessageId`.

6. **Drag a User-Approval node** and connect.
   - Configures: "Wait for approval in Slack (via `slack.reactions` MCP tool)" OR "Pause for manual approval in Composer UI."
   - Marcus picks the Composer UI option for his first build — he'll get a notification in the Composer approvals queue.

7. **Drag an If/Else node** after approval.
   - Condition: `approvalResult === 'approved'`
   - Creates two branches: `if` and `else`.

8. **On the `if` branch**, drag an MCP Tool node.
   - Picks **Atlassian Jira**.
   - Tool: `update_issue`.
   - Fields mapped from `enhancedStory`.

9. **Also on the `if` branch**, drag an MCP Tool node for Confluence.
   - Tool: `update_page`.
   - Page: `Sprint 23 Enhanced Stories`.
   - Appends `enhancedStory.summary`.

10. **On the `else` branch**, drag a Note node: "Rejected — log only, no update."

11. **End node** connected to both branches.

Marcus clicks **Save**. The workflow is stored in his tenant with a name: *Story Enhancement + Review + Publish*.

---

## End-State Scene 3 — Marcus Runs the Workflow

Marcus clicks **Run**. A panel appears asking for `jiraIssueKey`. He enters `PAY-4231` and clicks **Execute**.

### The execution view

The canvas shows nodes lighting up in sequence:

- **Start** → *green, instant*
- **Story Enhancer Agent** → *blue (running)* for 3 seconds → *green (completed)*. Hovering shows the enhanced story.
- **AC Validator Agent** → *blue* → *green*. Output shows 3 gaps in the AC.
- **Slack MCP** → *blue* → *green*. Slack message posted.
- **User Approval** → *yellow (paused)*. Execution halts.

### The approval queue

In Composer's sidebar, Marcus sees a pending approval card:

```
┌──────────────────────────────────────────┐
│  PAY-4231: Story Enhancement Review      │
│                                          │
│  Enhanced story:                         │
│  > As a corporate customer, I can view   │
│    consolidated balances across all my   │
│    accounts in a single dashboard...     │
│                                          │
│  AC Validator found 3 gaps:              │
│   • No AC for empty-state display        │
│   • Missing error handling scenario      │
│   • Currency formatting not specified    │
│                                          │
│  [ ✅ Approve ]   [ ❌ Reject ]           │
└──────────────────────────────────────────┘
```

Marcus reviews, decides the AC gaps are fine (they'll be addressed in sub-tasks), clicks **Approve**.

### Resume

- **User Approval** → *green*
- **If/Else** → routes to the `if` branch
- **Jira Update MCP** → *blue* → *green*
- **Confluence Update MCP** → *blue* → *green*
- **End** → *green*. Execution complete.

Marcus can see the full execution history, click any node to inspect inputs/outputs, and share the execution URL with teammates.

---

## End-State Scene 4 — Priya Audits and Manages

Priya opens Chorus → Workflows → **Audit Log**.

She sees:
- Who created each workflow
- Who ran each workflow, when, and the result
- LLM token usage per workflow / per user / per team
- MCP calls made per workflow (every Jira update, every Slack post is logged)
- Approval history (who approved what)

She can filter by user, team, time range, or workflow. Export to CSV for compliance.

She can also:
- **Pause** a workflow (prevent new executions)
- **Quota-limit** specific users or teams
- **Revoke** MCP OAuth tokens for a user
- **View** which workflows a specific integration is used in

Because Composer inherits IE's RBAC, tenancy, and encryption, none of this required separate implementation. It "just works" — to a platform admin, Composer looks like another IE module.

---

## End-State Scene 5 — Aiyana Builds a Template

Aiyana (Bounteous Platform Ops) notices multiple customers are asking for Story Enhancer → AC Validator → Notify workflows. She decides to build a reusable template.

She opens **Composer → Templates → New Template**. Builds the workflow once, parameterizing the Slack channel, Jira project, and Confluence page as **Template Variables**.

She publishes it to IE's Catalogue UI under the category **SDLC Automation Templates**. It appears alongside hand-coded agents, marked with a distinct icon (🎼 = composed).

Priya (or any tenant admin) can fork the template into their tenant, fill in the three template variables for their environment, and it's ready to use in minutes.

---

## End-State Scene 6 — An Executive Demo

Imagine a demo call with a prospective customer, a CTO at a mid-market tech company:

**Bounteous sales engineer (running IE Composer):**
> *"Here's what your situation looks like today. You bought us for our Story Enhancer and AC Validator agents. They work great. But every time your team wants a variation — 'also post to Slack,' 'also create a Confluence page,' 'also require sign-off from the tech lead' — you file a ticket with us, and it takes 6 weeks. Let me show you what that looks like with Composer."*

Drags nodes on the canvas for 5 minutes. Shows the workflow running live. Shows the audit log. Shows that the customer's own tech lead could have built this in an afternoon.

**CTO:**
> *"So we get the agents plus this?"*

**Sales engineer:**
> *"In the Platform tier, yes. Your team composes; we vetted the building blocks. That's the product."*

That's the demo. That's the sale.

---

## What This Replaces

Without Composer, the same scenarios today require:

| Scenario | Today (IE-only) | With Composer |
|---|---|---|
| Add Slack notification to Story Enhancer | File RFE → Bounteous engineering sprint → custom agent variant → 4-6 weeks | Marcus drags a Slack MCP node — 10 minutes |
| Per-tenant AC Validator with their own custom rules | Bounteous builds a "Bank X AC Validator" — tenant-specific branch | Priya composes a workflow with a Note node reminding reviewers of Bank X specifics, or a pre-processing Transform node with their rules |
| Add approval gate before any Jira update | Engineering change; contract amendment | User-Approval node, done |
| Combine multiple agents | N/A — impossible without platform engineering | Drag, connect, done |

---

## The Composite Mental Model

> **Composer is a canvas. The canvas is inside IE's tenant environment. The building blocks are IE's coded agents plus external MCPs plus LLM calls. What non-coders draw on the canvas, Composer executes — governed, audited, and safely.**

---

## Design Principles for the UX

From OAB's lessons, carried forward:

1. **Nodes are cards with configuration panels, not wizards.** No multi-step flows; click a node, edit its fields in a sidebar, done.
2. **Live execution is visible.** Nodes change color as they run. Outputs are inspectable at each step.
3. **Errors are locatable.** When a workflow fails, the failing node glows red with a clear error message and a "retry from here" button.
4. **Secrets are invisible.** OAuth tokens, API keys, encrypted blobs never appear in the UI. Users see "Connected ✅" or "Not connected."
5. **Defaults are smart.** Dropping an Agent node picks a sensible default LLM (the tenant's chosen provider). Dropping an MCP node shows only the tenant's available integrations.
6. **Undo is free.** Every edit is reversible. Templates make exploration cheap.
7. **Sharing is first-class.** An execution URL deep-links to the execution viewer. A workflow URL deep-links to the editor. Real collaboration.

---

## The One-Sentence Summary

> *"Marcus drags nodes, connects them, runs the workflow, approves a Slack prompt, watches Jira and Confluence update themselves. Priya audits everything, quotas nothing, and never calls Bounteous. That's Composer."*
