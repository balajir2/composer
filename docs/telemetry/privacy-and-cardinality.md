# Telemetry privacy and cardinality

This is a binding target for future vendor-neutral telemetry; existing logs and optional LangSmith traces do not yet enforce it. See the [signal catalog](signal-catalog.md). Telemetry is finite-retention and may be sampled. Durable append-only audit records are separate and unsampled, with their own access, retention and export policies. Recovery is a separately authorized state transition using a safe checkpoint, with immutable history and an audited reason; it must not restart from the beginning or replay completed side effects silently. Cancellation remains terminal.

## Payload classes

| Class | Policy |
|---|---|
| Authorization and cookie headers, bearer tokens, API/provider keys, signed approval tokens or URLs, decrypted node credentials, connection strings, secrets and configured sensitive fields | Always forbidden in metrics, spans, logs and audit; strip recursively before export, including nested fields and exception text. |
| Prompts, model responses, uploaded documents, node inputs/outputs, HTTP bodies and non-secret HTTP headers, emails, exception text | Forbidden in metrics and default telemetry. Full payload capture, if explicitly configured for a separately governed destination, requires encryption, access control, retention policy and redaction; secret classes remain forbidden. Exception text and headers can contain secrets and must never be exported unredacted. |
| Bounded aggregate counts, durations, approved controlled vocabulary values, sanitized error classifications | Allowed as specified in the catalog; no raw payloads or identifiers as metric attributes. |
| Opaque request, workflow, execution, thread, attempt and node IDs | Allowed only in access-controlled traces and structured logs (and audit where appropriate); not metrics. Never derive IDs from sensitive data. |

Redaction precedes serialization and export; recursively remove secrets, authorization/cookie headers and configured fields, cap field sizes, and drop unknown fields by default. Avoid arbitrary URLs, exception messages, headers, payloads and identifiers in metric labels. Map unknown values to `other` and enforce the catalog allow-list. User-defined labels, raw provider/model strings and customer identifiers are unbounded. Apply these rules to collectors and destinations as well as application instrumentation. Never store customer query results in the baseline definitions.

Audit full payloads are configurable only with encryption, access control and explicit retention; audit is not a substitute for metrics or recovery. Existing LangSmith can capture prompts, tool payloads and exceptions: enable only with appropriate destination controls until these protections exist.
