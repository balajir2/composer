import { Badge } from "@/components/ui/badge";
import type { components } from "@/lib/api/generated/schema";

type Diagnostics = components["schemas"]["AdminExecutionDiagnostics"];

function formatDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Time unavailable" : date.toLocaleString();
}

export function AdminExecutionDiagnostics({
  diagnostics,
}: {
  diagnostics: Diagnostics | null | undefined;
}) {
  if (!diagnostics) return null;
  return (
    <section
      aria-labelledby="admin-execution-diagnostics-heading"
      className="space-y-3 rounded-lg border p-4"
    >
      <h2 id="admin-execution-diagnostics-heading" className="text-base font-semibold">
        Administrator diagnostics
      </h2>
      <dl className="grid gap-3 text-sm sm:grid-cols-2">
        <div>
          <dt className="text-muted-foreground">Thread</dt>
          <dd className="break-all font-mono">{diagnostics.threadId}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Delivery attempts</dt>
          <dd>{diagnostics.deliveryAttempts}</dd>
        </div>
        {diagnostics.failureCode && (
          <div>
            <dt className="text-muted-foreground">Failure code</dt>
            <dd>
              <Badge variant="outline">{diagnostics.failureCode}</Badge>
            </dd>
          </div>
        )}
        {diagnostics.leaseOwner && (
          <div>
            <dt className="text-muted-foreground">Lease owner</dt>
            <dd>{diagnostics.leaseOwner}</dd>
          </div>
        )}
        {diagnostics.leaseExpiresAt && (
          <div>
            <dt className="text-muted-foreground">Lease expires</dt>
            <dd>{formatDate(diagnostics.leaseExpiresAt)}</dd>
          </div>
        )}
        <div>
          <dt className="text-muted-foreground">Available references</dt>
          <dd>None supplied</dd>
        </div>
      </dl>
    </section>
  );
}
