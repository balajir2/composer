# Compliance

> **Audience:** customer security teams, procurement, GRC reviewers.
> **Maintained by:** Balaji Rajan (`balajirajan@gmail.com`)
> **Last reviewed:** 2026-07-21. The certification status section is the part most likely to change.

This document is the formal counterpart to [security.md](security.md). Where that doc explains the controls we've built, this one explains the **third-party assertions** customers can rely on, the regulatory frameworks Composer maps to, and the audit-log capabilities we expose.

We are honest about what we have, what we don't, and what we're working on. If a question matters to your procurement and we don't have a clear answer below, escalate via [support.md](support.md) and we'll commit to a date.

## Status table

| Framework | Status | Evidence available |
|---|---|---|
| **SOC 2 Type II** | In progress (Type I targeted Q4 2026; Type II Q3 2027) | Gap-assessment report on request under NDA |
| **ISO 27001** | Not pursuing — SOC 2 covers our customers' needs | n/a |
| **GDPR** (data controller / processor) | Controls and templates available; deployment/operator assessment required | [legal/data-processing-addendum.md](legal/data-processing-addendum.md), [privacy.md](privacy.md) |
| **CCPA / CPRA** | Controls and templates available; legal assessment required | [privacy.md](privacy.md) "Customer rights mapping" |
| **HIPAA** | Not in scope today; possible with self-hosting + customer-side BAA flow | See "HIPAA stance" below |
| **PCI DSS** | Not in scope — Composer does not store card data | n/a |
| **FedRAMP** | Not in scope today | Roadmap candidate if a federal customer materialises |
| **SOX** | Composer is not a financial system; customer can use it inside a SOX-controlled workflow with their own controls | n/a |

The **status** column is the truth as of the doc's last reviewed date. We'll update with the same change-notice protocol as [privacy.md](privacy.md) when a status moves.

## SOC 2 — what we hold today

We are working toward SOC 2 Type II. A pre-audit gap assessment was completed and the controls mapped to the AICPA Trust Services Criteria. The current state:

| Trust Services Criterion | What we have | Gap to Type II readiness |
|---|---|---|
| **CC1 — Control environment** | Org-level governance + Composer-specific code review + ADR-backed change management | Formal information-security policy needs sign-off |
| **CC2 — Communication & information** | All policies in this folder + onboarding docs | Internal policy distribution log |
| **CC3 — Risk assessment** | Threat model in [security.md](security.md); incident-class write-ups in [archive/incident-history](../archive/incident-history/) | Annual formal risk-assessment ritual |
| **CC4 — Monitoring** | Per-route logging, per-execution audit, optional LangSmith tracing | Continuous-monitoring tooling (vendor TBD) |
| **CC5 — Control activities** | RBAC + access reviews via admin UI + SQL audit | Quarterly access-review attestation |
| **CC6 — Logical & physical access** | RBAC, MFA via Azure AD when SSO enabled, least-privilege DB roles | Documented termination procedure |
| **CC7 — System operations** | Runbooks under `../operations/` | Change-management ticket trail (we use git, but auditors usually want issue-tracker linkage) |
| **CC8 — Change management** | Every change ships through a PR with code review + CI gates | Same ticket trail above |
| **CC9 — Risk mitigation** | Encryption, sandboxing, sweepers, rate limits, vulnerability disclosure | Vendor risk-management formalisation |

What that means in practice:

- The **technical** controls a Type II auditor cares about are largely in place. The encryption, RBAC, audit logging, change management, and incident history primitives work today.
- The **process** controls — formal policies on paper, attestation rituals, vendor-management binders, evidence-preservation tooling — are in flight. The gap assessment laid them out; we expect to land Type I in Q4 2026.

For customers who need SOC 2 today: we provide controls evidence under NDA. The gap items above are documented honestly in the request response so your security team isn't surprised at the audit table later.

## GDPR

Composer provides controls that can support a GDPR-aligned deployment, but software alone cannot
make an operator compliant. Lawful basis, notices, contracts, retention, subprocessors, data
residency, and data-subject request operations remain the responsibility of the deploying
controller/processor and their counsel. The repository provides implementation controls and
templates, not a legal certification. Operationally:

- **Lawful basis** is *contract* for paid customers (Article 6(1)(b)) and *legitimate interest* for the operational logging we do.
- **Data Processing Addendum** is available via the contract; the template is at [legal/data-processing-addendum.md](legal/data-processing-addendum.md). It includes the Standard Contractual Clauses (2021/914 modules 2 + 3) for transfers outside the EEA.
- **Sub-processor list** is at [legal/subprocessors.md](legal/subprocessors.md); see also the operational discussion in [privacy.md](privacy.md).
- **Customer rights mapping** is in [privacy.md](privacy.md) — access, rectification, erasure, restriction, portability, objection, and the right not to be subject to automated decisions.
- **Data residency** options are documented in [privacy.md](privacy.md). EU residency is available for managed deployments via Neon EU + Vercel EU regions.
- **Breach notification**: in the event of a personal data breach (Article 33), we notify the customer's primary contact within **24 hours** of confirming the breach, with a follow-up technical report within 7 days. The runbook is at [`../operations/incident-response.md`](../operations/incident-response.md).
- **DPO contact**: managed customers have a named contact; for self-hosted deployments your own DPO is responsible.
- **Records of processing** are maintained per Article 30 — we hold the records for our own processing activities and provide template entries for customers' records covering Composer.

## CCPA / CPRA

The California regime maps cleanly onto the GDPR controls. Specific notes:

- We **do not sell or share personal information** as defined by CPRA. We have no advertising business model.
- Notice at collection is provided via the privacy policy template at [legal/privacy-policy.md](legal/privacy-policy.md), to be served at the customer's signup flow.
- The opt-out signal is honoured — the privacy policy template includes a contact email, and managed customers can flip an `data_sharing_opt_out` flag on their organisation if shared-tenant ever ships.

## HIPAA stance

We don't currently offer Composer for processing Protected Health Information. The reason is operational, not technical:

1. Sub-processors must each sign a Business Associate Agreement. Anthropic, OpenAI, and Google offer BAA-eligible API tiers; not all our optional sub-processors do.
2. Logging discipline must keep PHI out of structured logs that flow to non-BAA-covered observability sinks. Today our log policy doesn't enforce this.
3. The retention defaults need to align with HIPAA's 6-year minimum on PHI access logs.

Customers who need HIPAA today can self-host Composer in their own HIPAA-covered VPC, sign BAAs directly with the LLM and tool providers they use, and configure logging to a HIPAA-eligible sink. The **codebase doesn't block this**; the gap is operational uplift on the managed offering. If this is a procurement requirement, raise it on the [support contact](support.md) and we'll scope what a BAA-covered tier would look like.

## Audit log capabilities

Every Composer deployment captures the trail described below. For SOC 2 / GDPR / customer audits, what's captured covers most evidence questions out of the box; the gaps are noted.

### What's captured today (by table / source)

| Captured event | Where | Retention |
|---|---|---|
| User login / logout / refresh | Backend logs (`/auth/login`, `/auth/refresh`) | Host-level |
| Role change (member↔admin) | `users.role` column + DB audit log if you've added one — **no app-level history table yet** | Forever (or until SQL delete) |
| Workflow create / update / publish / delete | Backend logs + `workflows.updated_at` | Same |
| Workflow ownership reassignment | Backend logs | Same |
| Workflow assignment grant / revoke (sharing) | Backend logs + `workflow_assignments` row (`assignedById`, `assignedAt`) | Same |
| Execution start / complete / fail | `workflow_executions` row | Same |
| Approval decision (approved/rejected) | `approvals` row | Same |
| API key creation / revocation / first use | `api_keys.created_at`, `api_keys.revoked_at`, `api_keys.last_used_at` | Same |
| External invoke (`POST /api/run/{slug}`) | Backend logs + `workflow_executions` | Same |
| LLM key add / rotate / disable | Backend logs | Same |
| MCP server add / `isShared` toggle / OAuth completion | Backend logs + `mcp_servers` row | Same |
| Deployment-setting change | Backend logs + `deployment_settings` row | Same |
| Document upload | Backend log line: filename, size, format, extraction time, requesting user — **document content is not logged** | Host-level |

### Gaps we acknowledge

- **No dedicated `audit_log` table.** The audit trail is reconstructed from per-table timestamps + structured logs. This works for forensic investigation; it's harder to filter for "show me everything user X did in July." A dedicated audit table is on the [roadmap](roadmap.md).
- **Role-change history is not preserved.** Today `users.role` is a single column; we don't keep a history of who promoted whom and when. If your contract requires this, the simplest implementation is a Postgres trigger writing to a `user_role_audit` table — happy to build it in for managed customers.
- **Export of the audit trail** is currently SQL-driven. A self-serve "give me my audit log for the last 90 days as CSV" endpoint is planned.

## Penetration testing

Independent penetration testing is performed annually (managed deployments) and on-demand for material releases. The most recent report is available to managed customers under NDA. Findings are tracked publicly in [`../archive/incident-history/`](../archive/incident-history/) once the customer review window closes.

We welcome **customer-led penetration testing** of any deployment under your control. For managed deployments, please coordinate with the support contact in [support.md](support.md) so we can flag the activity to monitoring and arrange a rules-of-engagement document. We commit to a 5-business-day acknowledgement of pen-test requests.

## Vulnerability disclosure

Reporting path is in [security.md](security.md). Disclosed vulnerabilities are tracked publicly in [`../archive/incident-history/`](../archive/incident-history/) once the fix has shipped and the disclosure window has elapsed.

## SBOM (Software Bill of Materials)

A CycloneDX SBOM is generated as part of CI on every release tag. To request the most recent SBOM under NDA, contact the support address. The Python dependency manifest is at [`../../pyproject.toml`](../../pyproject.toml) and `uv.lock`; the JS dependencies are at [`../../frontend/package.json`](../../frontend/package.json) and `package-lock.json`.

## Customer assurance materials

These are made available to managed customers under NDA on request:

- Pre-audit SOC 2 gap-assessment report
- Most recent penetration-testing report (redacted external version available without NDA)
- Annual access-review attestation
- Vendor-management register
- Disaster-recovery test report (last successful test date + RTO/RPO observed)
- Insurance coverage certificate (cyber liability)
- The most recent SBOM

If a procurement questionnaire asks for one of these and it's missing from the list, escalate — we'd rather build the artefact than hand-wave it.

## Changes to compliance posture

Status changes (achieving a certification, losing one, opening a new framework) are announced via the same channel as data-handling changes ([privacy.md](privacy.md)): a heading update here, a CHANGELOG line, and a notification to managed customers.

## Adjacent docs

- [security.md](security.md) — the controls these certifications attest to
- [privacy.md](privacy.md) — operational data-handling truth
- [sla.md](sla.md) — uptime + response-time commitments
- [legal/](legal/) — DPA, sub-processors, ToS, AUP, privacy policy templates
- [`../operations/incident-response.md`](../operations/incident-response.md) — the breach-notification runbook
