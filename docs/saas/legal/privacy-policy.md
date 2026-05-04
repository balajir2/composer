# Privacy Policy — Template

> **Maintained by:** Balaji Rajan (`balajirajan@gmail.com`)
>
> **⚠️ Template only — not legal advice.** Replace every `{{variable}}` and validate with privacy counsel before publishing. The operational truth that this policy must match is in [`../privacy.md`](../privacy.md); the two should never disagree.

**Effective Date:** {{EFFECTIVE_DATE}}
**Last Updated:** {{LAST_UPDATED}}

This Privacy Policy describes how {{LEGAL_ENTITY}} ("**we**", "**us**", "**Provider**") collects, uses, and shares information when you use Composer ("**Service**"). If you have questions, contact us at {{PRIVACY_CONTACT_EMAIL}}.

This policy is for the customer-facing version of Composer that we operate. Customers who self-host Composer publish their own privacy policy that reflects their own operations.

## 1. Information We Collect

### 1.1 Information you provide

**Account information.** When you or your administrator creates an account, we collect your email address, name, role within your organisation, and authentication method (single sign-on or password).

**Workflow content.** When you build workflows, we store the workflow definition (nodes, edges, configuration), inputs, outputs, and any uploaded documents you process through the workflow.

**API keys for third-party services.** When your administrator provides API keys for LLM providers, vector databases, or MCP servers, we store these keys encrypted and use them only to fulfil the workflows you author.

**Communications with us.** When you contact our support team, we keep a record of your communications.

### 1.2 Information collected automatically

**Usage information.** We log requests to the Service, including timestamps, the actions performed, the user identifier, and the result. This information is used to operate the Service, debug issues, prevent abuse, and bill usage.

**Technical information.** We collect IP addresses, browser type, and similar technical metadata as part of standard server logging.

### 1.3 Information we do NOT collect

- We do not use third-party analytics, advertising, or tracking on our application surfaces. There are no Google Analytics, no Segment, no Pendo, no Hotjar.
- We do not sell, rent, or share your personal information for advertising.
- We do not retain documents that you upload to extract text from. Uploads are processed in memory and discarded immediately after extraction.
- We do not phone home from the codebase. The only outbound calls Composer makes are those your workflows initiate or those your administrator opted into (LangSmith tracing, optional product telemetry).

## 2. How We Use Information

We use the information collected to:

- **Provide the Service.** Run your workflows, store your workflow definitions, and authenticate your access.
- **Operate and improve the Service.** Monitor performance, debug issues, prevent abuse, plan capacity.
- **Communicate with you.** Respond to support requests, send service notifications, deliver important policy updates.
- **Comply with legal obligations.** Maintain records required by law and respond to lawful requests from authorities.
- **Bill for usage.** Calculate fees per the plan you've signed up for.

We rely on the following lawful bases under GDPR (where applicable):
- **Contract** — to provide the Service you've signed up for.
- **Legitimate interest** — to operate, secure, and improve the Service, balanced against your privacy rights.
- **Legal obligation** — to comply with laws applicable to us.
- **Consent** — for any optional uses where we ask for explicit consent.

## 3. How We Share Information

We share information only as described below. **We do not sell personal information.**

### 3.1 Sub-processors

Composer routes data to a small set of third parties so we can deliver the Service. The full list, kept current, is at [`subprocessors.md`](subprocessors.md). At a high level:

| Category | Examples |
|---|---|
| LLM inference | Anthropic, OpenAI, Google AI, Groq |
| Search and scraping tools | Tavily, Firecrawl, Serper, Browserless |
| Other AI tools | Gamma, Arcade |
| Vector databases (optional) | Pinecone, Qdrant, Chroma, Weaviate, Milvus |
| MCP servers | Whatever your workflow connects to |
| Hosting | Neon (database), Vercel (frontend), {{BACKEND_HOST}} (backend) |
| Tracing (optional) | LangSmith |

Each sub-processor receives only the data necessary for its function, and is contractually committed to confidentiality and data-protection standards we've reviewed.

### 3.2 Customer instructions

We act on your administrator's instructions about which integrations to enable, which providers to route LLM calls to, and which workflows to expose externally. Where you direct us to send your data to a third party, that data goes per your direction.

### 3.3 Legal requirements

We may disclose information if required by law, subpoena, or court order, or to protect the rights, property, or safety of us, our users, or the public. Where legally permitted, we will notify you before disclosing your data.

### 3.4 Business transfers

If we are involved in a merger, acquisition, or sale of assets, your information may be transferred as part of that transaction. We will notify you of any change in ownership or use.

## 4. Data Retention

| Data type | Default retention |
|---|---|
| Account information | Until account deletion |
| Workflow definitions | Until you delete them |
| Execution records (status, metadata, inputs/outputs) | {{EXECUTION_RETENTION_DAYS}} days for runs of published workflows; {{DRAFT_RETENTION_DAYS}} days for draft runs |
| Approval records | Same as the parent execution |
| API key records (hashes only) | Until revoked, plus an audit-trail period |
| Server logs | {{LOG_RETENTION_DAYS}} days |
| Backups | {{BACKUP_RETENTION_DAYS}} days |
| LangSmith traces (if enabled) | Per the LangSmith retention setting |

Retention defaults can be customised per your contract. We delete data that exceeds retention through automated processes.

## 5. Security

We implement administrative, technical, and physical safeguards to protect your information. Specifics are in our [Security](../security.md) and [Compliance](../compliance.md) documentation. Highlights:

- Encryption of secrets at rest using AES-256-GCM.
- Encryption of all data in transit using TLS 1.2 or higher.
- Bcrypt-hashed authentication credentials.
- Role-based access control with admin operations logged separately.
- Annual independent penetration testing.
- Vulnerability disclosure process detailed at {{VULN_DISCLOSURE_URL}}.

No system is perfectly secure. If a security breach affects your data, we will notify you per Section 11.

## 6. International Transfers

Our servers, sub-processors, and team are distributed across multiple jurisdictions. When data transfers from your region to another, we rely on:

- **Standard Contractual Clauses (EU SCCs 2021/914 modules 2 + 3)** for transfers from the EEA.
- **UK International Data Transfer Addendum** for transfers from the UK.
- **Swiss Federal Data Protection Act amendments** for transfers from Switzerland.
- **Adequacy decisions** where applicable.

Where you have data-residency requirements, your administrator can choose a hosting region for managed deployments. See [`../privacy.md`](../privacy.md).

## 7. Your Rights

Depending on where you live, you may have rights to:

- **Access** — request a copy of personal information we hold about you.
- **Correction** — ask us to correct inaccurate information.
- **Deletion** — ask us to delete your information ("right to erasure").
- **Restriction** — ask us to limit how we use your information.
- **Portability** — receive your information in a machine-readable format.
- **Objection** — object to certain types of processing.
- **Withdraw consent** — where processing relies on consent.
- **Lodge a complaint** with a supervisory authority.

To exercise these rights, contact us at {{PRIVACY_CONTACT_EMAIL}}. We respond within {{RIGHTS_RESPONSE_DAYS}} days for verified requests.

For California residents, the same rights are available under the CCPA / CPRA, plus the right to opt out of the sale or sharing of personal information (we don't sell or share for advertising).

## 8. Cookies and Tracking

The Service uses essential cookies for authentication and session management. We do not use cookies for advertising or third-party tracking. Specific cookies in use:

| Cookie | Purpose | Duration |
|---|---|---|
| Session token | Keeping you signed in | Session |
| Refresh token | Renewing your session without re-login | 30 days |

You can clear cookies through your browser settings, though this will sign you out of the Service.

## 9. Children

The Service is intended for B2B use and is not directed at children under 16. We do not knowingly collect personal information from children. If you believe a child has provided us with information, contact us and we will delete it.

## 10. Third-Party Links

The Service includes integrations with and references to third-party services. We are not responsible for the privacy practices of those services. Review their privacy policies before submitting personal information.

## 11. Changes to This Policy

We may update this policy from time to time. Material changes will be posted at least {{NOTICE_PERIOD_DAYS}} days in advance. We will notify the primary contact on each managed customer account by email.

For data breaches affecting personal information, we will notify the affected customer's primary technical contact within **24 hours** of confirming the breach, with a follow-up technical report within 7 days.

## 12. Contact

For privacy questions, requests, or complaints, contact:

- **Email:** {{PRIVACY_CONTACT_EMAIL}}
- **Postal address:** {{POSTAL_ADDRESS}}
- **Data Protection Officer (where applicable):** {{DPO_NAME_OR_NA}}

For EU representatives (where applicable under GDPR Article 27): {{EU_REP_NAME_AND_ADDRESS}}

---

<!-- Implementer note: Keep this policy synchronised with ../privacy.md. Where the operational doc evolves and this customer-facing notice doesn't, customers will catch the discrepancy. The policy is a faithful summary; the operational doc is the deeper treatment. -->
