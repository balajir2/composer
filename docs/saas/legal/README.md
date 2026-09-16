# Legal Templates

> **Maintained by:** Balaji Rajan (`balajirajan@gmail.com`)
>
> **⚠️ These are templates, not legal advice.** Every document in this folder is a starting point intended to be reviewed and customised by qualified counsel before you publish or sign it. Variables in `{{double-braces}}` indicate text that *must* be replaced before use. Comments in `<!-- HTML comments -->` are review notes that should be deleted before publication.

## What's here

| Document | When to use |
|---|---|
| [terms-of-service.md](terms-of-service.md) | Customer-facing terms of use for the SaaS offering |
| [privacy-policy.md](privacy-policy.md) | The user-facing privacy notice; served at signup and from the footer |
| [acceptable-use.md](acceptable-use.md) | The AUP that governs what customers can and can't do with Composer |
| [data-processing-addendum.md](data-processing-addendum.md) | DPA template for customer agreements (GDPR / UK / Switzerland) |
| [subprocessors.md](subprocessors.md) | Versioned list of third parties Composer routes data through |

## How to use these

### If you're hosting Composer as your own SaaS (commercial or internal)

1. Bring each template to your privacy + commercial counsel.
2. Replace every `{{variable}}` with the specific facts of your operation — your legal entity, governing law, primary contact addresses, sub-processor list per [privacy.md](../privacy.md).
3. Strike sections that don't apply to your business model.
4. Add sections specific to your jurisdiction or industry that the templates don't cover.
5. Establish the version-control + change-notification process described below.
6. Have counsel re-review on a cadence (annually at minimum, and when material business changes happen).

### If you're a customer evaluating Composer's published SaaS offering

These are reference. The contract you sign with us is the operative agreement.

## What these templates assume

The templates assume:

- **A B2B SaaS business model.** They reference plan tiers and contract structures from [pricing.md](../pricing.md). They don't presume a free B2C consumer product.
- **GDPR / CCPA in scope.** The privacy materials map cleanly onto European + Californian regimes. Other regimes (LGPD, PIPEDA, APPI, India's DPDP) are addressable but not pre-written.
- **No HIPAA, PCI, FedRAMP, or other regulated-data regime out of the box.** See [compliance.md](../compliance.md) for the stance on each. Where these regimes matter, materially revised contractual language is required.
- **A placeholder default contracting entity.** Replace with your legal entity and governing law throughout.
- **Sub-processors limited to the list in [subprocessors.md](subprocessors.md).** Adding a new third party means updating the sub-processor list, notifying customers per the change-notice protocol, and revising the privacy policy if user-visible.

## Change-notification protocol

Material changes to any document in this folder follow the same protocol as data-handling changes ([privacy.md](../privacy.md)):

1. Update the document with a new "Last reviewed" / "Effective" date in the heading.
2. Add a CHANGELOG entry summarising what changed and why.
3. Email the primary contact on each managed customer's account at least **30 days before the change takes effect** (longer if the change is materially adverse).
4. Customers can decline a materially adverse change by leaving the platform within the notice window without penalty.

For the sub-processor list specifically, the GDPR / DPA-mandated cadence is **adding a new sub-processor requires the same 30-day notice**, and customers may object in writing within the notice window.

## Version control

All documents in this folder are versioned through git. The commit history is the source of truth for "what changed and when." For audit purposes, the published version of each document is the one in the `main` branch as of the date served to the customer.

For external customer agreements, the operative version is the one **attached to or referenced in** the signed contract — those are typically PDF snapshots taken at contract signing, not the live document. The live versions here may evolve; existing customers stay on the version they signed unless we mutually amend.

## Counsel review checklist

Before any template here is published or signed:

- [ ] Replaced every `{{variable}}` with concrete content
- [ ] Removed every `<!-- HTML comment -->` review note
- [ ] Validated the legal entity name + jurisdiction
- [ ] Aligned the governing law + venue clauses with your contracts standard
- [ ] Updated the sub-processor list to reflect your actual operations
- [ ] Confirmed the data-residency claims match your hosting region(s)
- [ ] Checked the indemnification + liability cap clauses against your insurance coverage
- [ ] Validated the compliance claims against current certifications (per [compliance.md](../compliance.md))
- [ ] Confirmed the security + privacy commitments are achievable per [security.md](../security.md) + [privacy.md](../privacy.md)
- [ ] Counsel has signed off in writing

If any item is unchecked, the document is not ready to publish.

## Adjacent docs

- [../privacy.md](../privacy.md) — operational truth that the privacy policy is built from
- [../security.md](../security.md) — operational truth that the security commitments are built from
- [../compliance.md](../compliance.md) — certification status the legal templates can rely on
- [../sla.md](../sla.md) — service-level commitments that flow into the customer contract
