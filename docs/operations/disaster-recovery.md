# Disaster Recovery Runbook

> **Maintained by:** Balaji Rajan (`balajirajan@gmail.com`)
> **Audience:** SREs, platform team, anyone holding the on-call pager.
> **Companion docs:** [postgres-setup.md](postgres-setup.md) for the backup setup, [incident-response.md](incident-response.md) for the broader incident workflow, [`../saas/sla.md`](../saas/sla.md) for the RPO/RTO commitments.

This is the playbook for restoring Composer after a disaster — meaning loss of data, loss of database, loss of compute, or any combination. It is **not** a general operational guide; for normal operations see [production-deployment.md](production-deployment.md). It is **not** an incident-response guide; see [incident-response.md](incident-response.md) for the broader response. This is specifically: "we've lost something important, get it back."

Read this in full before you need it. Restore-by-Googling is not a thing — by the time you need this, you should already know what's here.

## RPO and RTO commitments

| Plan tier | RPO (max data loss) | RTO (time to restore) | Source of commitment |
|---|---|---|---|
| Free / Self-hosted | Customer's responsibility | Customer's responsibility | n/a |
| Starter | 24 hours | 8 hours | [`../saas/sla.md`](../saas/sla.md) |
| Business | 4 hours | 4 hours | Same |
| Enterprise | 15 minutes | 1 hour | Same (negotiated) |

The numbers above mean:
- **RPO** (Recovery Point Objective) = the most data we'd lose. A 4-hour RPO means at worst we restore to a state from 4 hours ago.
- **RTO** (Recovery Time Objective) = the maximum time from "we noticed the disaster" to "service is back up."

These are commitments to customers. To meet them in practice, our backup + restore mechanics need to support them with margin.

## What we back up

| Component | What's in it | Where it lives | Frequency |
|---|---|---|---|
| Postgres (workflows, executions, users, secrets) | The complete application state | Neon (or your Postgres host) | Continuous WAL archiving + daily logical backup |
| Encryption key (`ENCRYPTION_KEY`) | The key that decrypts secrets at rest | Host's secret store | Whenever it's set; rotation requires explicit replacement |
| JWT signing secret (`JWT_SECRET`) | The key that signs session tokens | Host's secret store | Same |
| LLM keys (also encrypted in Postgres) | Already in the Postgres backup | n/a beyond Postgres | Same |
| Frontend code | Versioned in git; deployable from any commit | GitHub | Continuous (every push) |
| Backend code | Same | Same | Continuous |
| Container images | Built from code; tagged per release | Container registry (ghcr / ECR / equivalent) | Per release |

Importantly, we **do not** independently back up:
- LangSmith traces (LangSmith owns those)
- Vector DB contents (the customer's vector-DB hosts own those)
- MCP server data (the third-party MCP server owns it)
- Documents uploaded for extraction (we don't store them at all — see [`../saas/privacy.md`](../saas/privacy.md))

## What "loss" looks like

The disasters we plan for:

| Disaster | RPO impact | RTO impact | Most likely cause |
|---|---|---|---|
| **Postgres host corrupted / destroyed** | Up to RPO | Full RTO | Hosting provider failure, accidental DROP DATABASE |
| **Region-wide cloud outage** | Possibly significant | Possibly significant | Major incident at AWS / GCP / similar |
| **Encryption key lost** | Effectively all encrypted secrets gone | Variable | Secret store misconfiguration, key rotation gone wrong |
| **Bad migration applied** | From migration timestamp | Time to restore + re-apply | Untested migration |
| **Mass deletion (admin error)** | From deletion event | Quick if recent | Misuse of "Delete all" admin action |
| **Ransomware or data tampering** | From compromise | Variable | Compromised admin credentials |
| **Backup corruption discovered late** | From most recent valid backup | Full RTO + fallback to older backup | Storage corruption, backup process failure |
| **Code regression deployed** | Zero | Time to roll back | Bad deploy that gets through review |

For each, the response is below.

## Pre-incident: the things that must be true *now*

If any of these isn't true today, fix it before you need this runbook for real.

- [ ] Postgres backups are running daily, with verification (the host's backup-status indicator says "successful" within the last 24 hours).
- [ ] Point-in-time recovery is enabled with at least 7 days of retention (Neon: default; RDS: explicitly).
- [ ] The encryption key is stored in a system that will survive the backend host being destroyed (e.g., a separate secret store, a password manager exported to encrypted offline storage, or a managed KMS).
- [ ] The encryption key has been **tested for restoration** at least once — you've decrypted a known secret using the stored key, not just confirmed the file exists.
- [ ] You have admin access to the Postgres host's UI (not just the application's connection string).
- [ ] You have access to the container registry where backend images live.
- [ ] You have sign-in access to the frontend host (Vercel etc.) to roll back deployments.
- [ ] At least two team members have all of the above access (no single point of human failure).
- [ ] The DR drill (below) has been run within the last 6 months.

If you've inherited a deployment and any of these are unclear, **stop and verify before continuing**. The cost of unclear DR posture is paid during the next disaster, not now.

## The DR drill

Run a full DR drill at least every 6 months. The drill exercises the restore path against a non-production deployment so the steps are familiar when you need them.

### Drill script (copy this; modify only as needed)

1. **Trigger**: pick a calendar date. Coordinate with the on-call rotation so people aren't paged for the drill.
2. **Scenario**: provision a parallel non-production deployment (or use your staging environment if it's representative). Decide what kind of disaster you're simulating (e.g., "Postgres lost, restore from PITR").
3. **Restore from backup**: walk through the procedure below for the relevant scenario. Document elapsed time at each step.
4. **Validate**: run the smoke test from [production-deployment.md](production-deployment.md#phase-8--production-smoke-test) against the restored deployment.
5. **Compare**: against the RTO commitment for the relevant plan tier — did you meet it?
6. **Postmortem**: short write-up of what worked, what didn't, what to fix in the runbook. File under [`../archive/incident-history/`](../archive/incident-history/) tagged as a drill.

The point isn't to demonstrate flawless execution; it's to find the steps where execution was unclear and fix them.

## Restoration procedures

### A. Postgres host corrupted / destroyed

The fastest path is point-in-time restore (PITR), which Neon and RDS both support.

1. **From the Postgres host's UI, branch from a known-good timestamp.** Pick a timestamp earlier than the corruption event by at least the RPO margin. For Neon: branch the database. For RDS: restore-to-point-in-time creates a new instance.
2. **Validate the branched/restored database.** Connect with `psql`, run:
   ```sql
   SELECT count(*) FROM users;
   SELECT count(*) FROM workflows;
   SELECT count(*) FROM workflow_executions WHERE status = 'completed';
   ```
   The numbers should match what you'd expect from the timestamp. If they're zero, the restore failed — try a different timestamp.
3. **Cut traffic to the new database.** Update the backend's `DATABASE_URL` env var to the new connection string. Restart the backend (or roll new replicas).
4. **Smoke test.** Run the smoke test from [production-deployment.md](production-deployment.md#phase-8--production-smoke-test). If it passes, you're done.
5. **Communicate to customers.** Post the resolution update per [incident-response.md](incident-response.md). Be honest about what was lost between the RPO timestamp and the disaster.
6. **Schedule the postmortem**.

**Estimated time**: 1-2 hours for Neon (branching is fast); 2-4 hours for RDS (instance restore is slower).

### B. Region-wide cloud outage

Wait. There's no DR action for "AWS us-east-1 is down" except move to a different region, which is a multi-day project, not an incident response. Communicate to customers that the region-wide outage is the cause and updates will follow as the cloud provider restores service.

If your contract demands cross-region failover, that's a roadmap conversation — see [`../saas/roadmap.md`](../saas/roadmap.md). Today we don't run active-passive cross-region.

### C. Encryption key lost

This is the one that hurts. **The Postgres backup contains encrypted blobs that are useless without the key.** If the key is gone, the encrypted data is effectively destroyed.

1. **Confirm the key is actually gone.** Check every place it could be: secret store, password manager, deployment-specific encrypted notes, the original engineer who set it up. Sometimes "lost" is "the new admin doesn't know where the previous admin filed it."
2. **If the key is truly lost**: the encrypted secrets (LLM API keys, MCP OAuth tokens) cannot be recovered. The application can still run — non-encrypted data (workflows, users, executions) is fine — but customers will need to re-add their LLM keys + re-complete OAuth flows for MCP servers.
3. **Generate a new key.** `openssl rand -hex 32`. Store it in the secret store with multiple redundant copies + a documented restoration test.
4. **Set the new key as `ENCRYPTION_KEY`.** Restart the backend.
5. **Notify customers.** They will see "no LLM keys configured" on their admin → LLM keys page; they need to re-add. Same for MCP OAuth.
6. **Schedule the postmortem** — this should never happen again. The action item is "encryption key has been tested for restoration at least once."

**Estimated time**: minutes to swap the key; days to weeks for customers to fully re-onboard their integrations.

### D. Bad migration applied

The migration ran, applied a destructive change (dropped a column, deleted rows), and rolled forward to the next deploy.

1. **Stop more migrations from running.** If your deploy pipeline auto-runs `prisma migrate deploy`, pause it.
2. **Identify the migration.** `SELECT * FROM _prisma_migrations ORDER BY finished_at DESC LIMIT 5;` shows the most recent migrations and their timestamps.
3. **Restore from PITR** to a timestamp before the bad migration applied. Procedure A above.
4. **Fix the migration in code.** Either revert the bad migration entirely or write a corrected version.
5. **Re-apply** the corrected migration after testing.
6. **Postmortem.** The action item is typically "migration tests on a copy of production data before promoting."

### E. Mass deletion (admin error)

Someone clicked "Delete all" in admin → executions and meant to delete only their staging environment but did production by accident.

1. **Determine the deletion timestamp.** Backend logs around the action will show the actor + timestamp.
2. **Restore from PITR** to a timestamp before the deletion. Procedure A above.
3. **Smoke test** to confirm executions are back.
4. **Notify customers** of any data-residency window between deletion and restore.
5. **Postmortem.** Action items typically include: a confirmation flow that names the impact size, a separation between "delete all (this customer's runs)" vs "delete all (system wide)," and admin-permission tightening.

### F. Ransomware / data tampering

Someone has modified the database in a way that's unsafe to operate from. The trustworthy state is the backup.

1. **Isolate the deployment.** Cut external access while you investigate (taking the backend down is acceptable if the alternative is serving compromised data).
2. **Determine the compromise window.** Backend logs + Postgres audit logs (if you have them) + the host's IAM access log will show when the unauthorised access started.
3. **Restore from PITR** to a timestamp before the compromise.
4. **Rotate every secret.** `JWT_SECRET`, `ENCRYPTION_KEY`, all LLM API keys, all MCP OAuth tokens (these get re-encrypted with the new `ENCRYPTION_KEY` so customers may need to re-complete OAuth flows depending on how the rotation is staged).
5. **Force every user to re-authenticate.** Rotating `JWT_SECRET` invalidates all existing sessions automatically.
6. **Investigate the compromise vector.** What credential was used? How was it obtained? Close the path before bringing the deployment back online.
7. **Communicate to customers** per [`../saas/compliance.md`](../saas/compliance.md) breach-notification commitments — within 24 hours of confirming the breach.

**Estimated time**: half a day to a week, depending on the investigation.

### G. Backup corruption discovered late

The backup you wanted to restore from is itself corrupt.

1. **Try the next-most-recent backup.** Most hosts retain multiple days of backups; older is more likely to predate the corruption.
2. **If all recent backups are corrupt**: the corruption likely happened at the host level. Open a support ticket with the host immediately.
3. **Communicate the wider RPO impact** to customers — they're losing more than the standard window.
4. **Postmortem** items usually include: backup-integrity testing as part of the daily backup pipeline, not just "backup completed."

### H. Code regression deployed

Pre-deploy testing missed something; the deployed version misbehaves.

1. **Roll back via the host's UI** (Vercel: previous deployment → Promote; backend: previous container tag).
2. **Validate** with the smoke test.
3. **Investigate offline** in your dev environment.
4. **Re-deploy with a fix** once the regression is identified + tested.
5. **Postmortem**: the action item is to close the test gap that allowed the regression through CI.

This is the most common "DR" event by far, and it's not really DR — it's a regular release rollback. The runbook is here because rollbacks are sometimes mistaken for incidents.

## After every restoration

Regardless of which procedure you ran:

- [ ] Customer-facing comms updated (resolution status, postmortem ETA)
- [ ] Status page updated (managed customers)
- [ ] Backups verified resuming on the new infrastructure
- [ ] Monitoring confirmed the deployment is stable
- [ ] Postmortem scheduled
- [ ] Action items from the postmortem assigned with dates

## When to call for backup

Specifically for managed customers, escalation up the chain when:

- The restoration estimate exceeds the RTO by more than 50%
- Customer impact is larger than initially scoped
- The disaster's cause is unclear after the first hour of investigation
- A second pair of eyes would speed the response

## Adjacent docs

- [postgres-setup.md](postgres-setup.md) — backup configuration that this runbook depends on
- [incident-response.md](incident-response.md) — broader incident workflow
- [`../saas/sla.md`](../saas/sla.md) — RPO + RTO commitments
- [`../saas/compliance.md`](../saas/compliance.md) — breach-notification obligations
- [production-deployment.md](production-deployment.md) — the smoke test referenced from validation steps
- [`../archive/incident-history/`](../archive/incident-history/) — past DR-relevant write-ups
