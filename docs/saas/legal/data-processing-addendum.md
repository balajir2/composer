# Data Processing Addendum (DPA) — Template

> **Maintained by:** Balaji Rajan (`balajirajan@gmail.com`)
>
> **⚠️ Template only — not legal advice.** Replace every `{{variable}}` and validate with privacy + commercial counsel before signing. Where bracketed text reads "select option," remove the inapplicable language before signing.

**Effective Date:** {{EFFECTIVE_DATE}}
**Last Updated:** {{LAST_UPDATED}}

This Data Processing Addendum ("**DPA**") is entered into by and between:

- **{{LEGAL_ENTITY}}** ("**Provider**" or "**Processor**"), and
- **{{CUSTOMER_LEGAL_ENTITY}}** ("**Customer**" or "**Controller**")

The DPA forms part of and is subject to the Terms of Service or Master Services Agreement between the parties (the "**Principal Agreement**"). It governs the processing of Personal Data by Provider on Customer's behalf in connection with Customer's use of Composer (the "**Service**").

The contact for privacy matters under this DPA is **balajirajan@gmail.com**.

## 1. Definitions

Capitalised terms not defined here have the meaning given in the Principal Agreement or in the relevant Data Protection Law (defined below).

- **"Data Protection Law"** means the European Union's General Data Protection Regulation 2016/679 (GDPR), the United Kingdom's Data Protection Act 2018 and UK GDPR, the California Consumer Privacy Act (CCPA) as amended by the California Privacy Rights Act (CPRA), and any other applicable data protection or privacy law that governs the processing of Personal Data under this DPA.
- **"Personal Data"** has the meaning given in the applicable Data Protection Law.
- **"Process"** and its variations have the meaning given in the applicable Data Protection Law.
- **"Sub-processor"** means any third party engaged by Provider to process Personal Data in connection with the Service.
- **"Standard Contractual Clauses"** or **"SCCs"** means the standard contractual clauses approved by the European Commission under Decision 2021/914 of 4 June 2021, modules 2 (controller-to-processor) and 3 (processor-to-processor) where applicable.

## 2. Scope and Roles

### 2.1 Scope

This DPA applies to Provider's processing of Personal Data submitted to the Service by Customer or its Authorised Users, and any Personal Data Provider processes for Customer in operating the Service.

### 2.2 Roles

The parties acknowledge that, in respect of Personal Data submitted to the Service:

- Customer acts as the **Controller** (or Processor on behalf of its own controller, in which case Provider is a Sub-processor in that chain).
- Provider acts as a **Processor** on Customer's behalf.

For the limited categories of Personal Data Provider processes for its own purposes (e.g., billing, account administration, security monitoring), Provider acts as a Controller and processes such data per its [Privacy Policy](privacy-policy.md).

## 3. Processing Details

The details of processing required by Article 28(3) GDPR are:

| Field | Value |
|---|---|
| Subject matter of processing | Provision of the Composer workflow platform Service |
| Duration | Term of the Principal Agreement plus retention periods set out in [`../privacy.md`](../privacy.md) |
| Nature and purpose of processing | Storing workflow definitions; executing workflows; storing execution records; routing requests to LLM providers and other sub-processors as directed by Customer's workflows |
| Categories of data subjects | Customer's authorised users; the data subjects whose information is contained in workflow inputs / outputs as Customer directs |
| Categories of Personal Data | Authentication identifiers (email, name, role); workflow content (which may include any data Customer chooses to process); execution logs; whatever is contained in uploads Customer chooses to submit |
| Special categories of Personal Data | None expected unless Customer explicitly submits them via workflow inputs; if Customer intends to do so, the parties shall agree in writing on the additional safeguards |
| Cross-border transfer mechanism | SCCs with the modules + parameters at Annex 2 |

## 4. Processor Obligations

### 4.1 Documented instructions

Provider will process Personal Data only on documented instructions from Customer, including the instructions encoded in the Service's configuration (LLM providers enabled, sub-processors authorised, retention settings) and any subsequent written instructions from Customer.

If Provider believes a Customer instruction infringes Data Protection Law, Provider will inform Customer without delay.

### 4.2 Personnel confidentiality

Provider ensures that personnel authorised to process Personal Data have committed themselves to confidentiality or are under appropriate statutory obligations of confidentiality.

### 4.3 Security measures

Provider implements appropriate technical and organisational security measures, including those described in **Annex 1** (Technical and Organisational Security Measures) below.

### 4.4 Sub-processors

Provider's current sub-processors are listed at [`subprocessors.md`](subprocessors.md). Provider may engage new sub-processors as follows:

- Provider notifies Customer at least **30 days** in advance of authorising a new sub-processor (the notice may be by email, by update to the published list, or by Customer's subscription to a notification mechanism Provider operates).
- Customer may object in writing within the notice period if it reasonably believes the new sub-processor cannot meet the requirements of Data Protection Law.
- If the parties cannot resolve the objection in good faith, Customer may terminate the Service for the affected workload without penalty for the unused portion of the Subscription Term.

Provider remains liable for any breach of this DPA caused by a sub-processor.

### 4.5 Assistance to Controller

Provider will provide reasonable assistance to Customer to enable Customer to:

- Respond to data subject requests (access, rectification, erasure, restriction, portability, objection).
- Conduct data protection impact assessments (DPIAs) and consult with supervisory authorities.
- Comply with security, breach notification, and other obligations under Data Protection Law.

The mechanisms Provider provides for these purposes are described in [`../privacy.md`](../privacy.md) and the [Admin Guide](../../admin-guide.md).

### 4.6 Personal Data Breach notification

Provider will notify Customer of any Personal Data Breach affecting Customer's data without undue delay, and in any event within **24 hours** of becoming aware. Notice will include:

- The nature of the breach, including categories and approximate number of data subjects and Personal Data records concerned.
- The likely consequences of the breach.
- The measures taken or proposed to address the breach and to mitigate its effects.

Provider will provide a follow-up technical report within 7 days. The breach-handling runbook is at [`../../operations/incident-response.md`](../../operations/incident-response.md).

### 4.7 Audits and inspections

Customer may audit Provider's compliance with this DPA at most once per calendar year, on at least 30 days' written notice, during business hours, with a scope agreed in advance, and at Customer's expense unless the audit identifies a material breach. Audits must:

- Be conducted by a qualified independent third party (or Customer's own personnel under NDA).
- Not unreasonably interfere with Provider's operations or other customers' data.
- Comply with Provider's confidentiality, security, and access requirements.

In lieu of a customer audit, Provider may make available a recent independent third-party audit report (e.g., SOC 2) under NDA. Where a recent report is available, Customer accepts that report as satisfying the audit obligation, except where Customer can show specific cause for an additional audit.

### 4.8 Return and deletion of Personal Data

On termination of the Principal Agreement, Provider will, at Customer's choice, delete or return all Personal Data and delete existing copies, except where retention is required by law. Operationally, the deletion paths are set out in [`../privacy.md`](../privacy.md).

## 5. Cross-Border Transfers

### 5.1 SCCs

Where Personal Data is transferred from the EEA, the United Kingdom, or Switzerland to a country that has not received an adequacy decision, the parties incorporate the SCCs by reference. The applicable modules, populated parameters, and annexes are at **Annex 2** below.

### 5.2 Supplementary measures

In addition to the SCCs, Provider implements the technical and organisational measures described in Annex 1, including encryption of Personal Data at rest and in transit, access controls, and the security commitments in [`../security.md`](../security.md). Provider will provide reasonable assistance if Customer's data protection impact assessment identifies a need for additional supplementary measures.

### 5.3 UK transfers

The UK International Data Transfer Addendum to the SCCs (the "UK Addendum") applies to transfers from the United Kingdom and is incorporated by reference, with the parameters at Annex 2.

### 5.4 Swiss transfers

For transfers from Switzerland under the Swiss Federal Act on Data Protection (FADP), references to the GDPR in the SCCs are deemed to refer also to the FADP, the Swiss Federal Data Protection and Information Commissioner is deemed to be the supervisory authority, and the term "Member State" includes Switzerland.

## 6. CCPA-Specific Terms

For Customer's processing of personal information of California residents:

- Provider acts as Customer's "service provider" within the meaning of the CCPA.
- Provider will not sell or share (as those terms are defined under the CPRA) personal information.
- Provider will process personal information only for the limited and specified purposes set out in the Principal Agreement and this DPA.
- Provider will not retain, use, or disclose personal information outside the direct business relationship with Customer.
- Provider will assist Customer with consumer requests under the CCPA per Section 4.5.

## 7. Liability

Liability under this DPA is governed by the Principal Agreement, except that the cap and exclusions do not apply to either party's obligations under Section 4.6 (breach notification), 4.7 (audits) where Customer has reasonably exercised its audit right, or any liability that cannot be excluded by Data Protection Law.

## 8. General

### 8.1 Order of precedence

In case of conflict between this DPA and the Principal Agreement, the DPA controls for matters relating to the processing of Personal Data. SCCs control over both for matters within their scope.

### 8.2 Modifications

Provider may update this DPA to reflect changes in Data Protection Law or in the Service's processing operations. Material changes are notified at least 30 days in advance per the change-notice protocol in [`./README.md`](README.md).

### 8.3 Term

This DPA takes effect on the Effective Date and continues until the Principal Agreement terminates and all Personal Data has been deleted or returned per Section 4.8.

### 8.4 Governing law

This DPA is governed by the law of {{GOVERNING_LAW}} except as required by Data Protection Law (in which case the relevant Data Protection Law's mandatory provisions take precedence).

### 8.5 Counterparts

This DPA may be signed in counterparts.

---

## Annex 1 — Technical and Organisational Security Measures

The measures described in [`../security.md`](../security.md) form part of this DPA. The summary at the level of detail expected by GDPR Article 32 is:

| Category | Measures |
|---|---|
| **Pseudonymisation and encryption** | AES-256-GCM at rest for secrets; TLS 1.2+ in transit; bcrypt for password and per-user API key hashing |
| **Confidentiality** | Role-based access control; least-privilege database roles; admin operations logged separately; named-employee access reviews |
| **Integrity** | Postgres transactional integrity; immutable execution audit trail; signed commits in CI |
| **Availability and resilience** | Daily backups with point-in-time restore (Neon); resilient detached-task wrapper; stuck-execution sweeper; quarterly DR drills |
| **Restoration after incident** | Documented DR runbook ([`../../operations/disaster-recovery.md`](../../operations/disaster-recovery.md)) with stated RPO/RTO per plan |
| **Regular testing** | Annual independent penetration testing; continuous CI gates (lint + format + type check + tests); quarterly access reviews |
| **Sub-processor management** | Documented sub-processor list with notification protocol; security review prior to onboarding; contractual data-protection commitments from each |

## Annex 2 — Standard Contractual Clauses Parameters

### EU SCCs (Decision 2021/914)

- **Module(s):** Module 2 (controller-to-processor) where Customer is a Controller; Module 3 (processor-to-processor) where Customer is itself a Processor.
- **Clause 7 (Docking Clause):** Optional — not adopted unless the parties expressly agree.
- **Clause 9(a) (Sub-processor authorisation):** Option 2 (general written authorisation) — with the 30-day notification described in Section 4.4 of this DPA.
- **Clause 11 (Redress):** Optional independent dispute resolution mechanism — not adopted.
- **Clause 13(a) (Supervisory authority):** {{LEAD_SUPERVISORY_AUTHORITY}}
- **Clause 17 (Governing law):** Laws of {{SCC_GOVERNING_LAW}}
- **Clause 18(b) (Choice of forum):** Courts of {{SCC_VENUE}}
- **Annex I.A (List of Parties):** Provider and Customer per the parties' details on the cover page.
- **Annex I.B (Description of Transfer):** As described in Section 3 (Processing Details) of this DPA.
- **Annex II (Technical and Organisational Measures):** As described in Annex 1 above.
- **Annex III (List of Sub-processors):** As listed at [`subprocessors.md`](subprocessors.md).

### UK Addendum

- **Table 1 (Parties):** As above.
- **Table 2 (Selected SCCs, Modules and Selected Clauses):** Per the EU SCCs section above.
- **Table 3 (Appendix Information):** As referenced in the EU SCCs section.
- **Table 4 (Ending the Addendum when the Approved Addendum Changes):** Both Importer and Exporter may end this Addendum.

## Signatures

By signing the Principal Agreement, the parties signify their acceptance of this DPA.

**For Provider**: {{PROVIDER_SIGNATORY_NAME}}, {{PROVIDER_SIGNATORY_TITLE}}
Date: {{PROVIDER_SIGNATURE_DATE}}

**For Customer**: {{CUSTOMER_SIGNATORY_NAME}}, {{CUSTOMER_SIGNATORY_TITLE}}
Date: {{CUSTOMER_SIGNATURE_DATE}}
