import { Badge } from "@/components/ui/badge";
import type { ExecutionObservabilityRead } from "@/lib/api/executions";

const STATUS_LABELS: Record<string, string> = {
  queued: "Queued",
  pending: "Queued",
  running: "In progress",
  waiting_approval: "Waiting for approval",
  completed: "Completed",
  cancelled: "Canceled",
  canceled: "Canceled",
  failed: "Failed",
};

export function ExecutionLifecycleSummary({
  summary,
  stale,
}: {
  summary: ExecutionObservabilityRead;
  stale: boolean;
}) {
  const statusLabel = STATUS_LABELS[summary.status] ?? "Status unavailable";

  return (
    <section
      aria-labelledby="execution-lifecycle-heading"
      className="space-y-3 rounded-lg border p-4"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="execution-lifecycle-heading" className="text-base font-semibold">
          Execution status
        </h2>
        <Badge variant={summary.status === "failed" ? "destructive" : "secondary"}>
          {statusLabel}
        </Badge>
      </div>
      {stale && (
        <p role="status" className="text-sm text-muted-foreground">
          This information may be out of date.
        </p>
      )}
      {summary.currentNodeId && summary.status === "running" && (
        <p className="text-sm text-muted-foreground">Current step: {summary.currentNodeId}</p>
      )}
      {summary.status === "cancelled" ||
      summary.status === "canceled" ||
      summary.recoveryState === "ineligible_canceled" ? (
        <p className="text-sm">This execution was canceled and cannot be recovered.</p>
      ) : summary.recoveryState === "assessment_required" ? (
        <p className="text-sm">
          A safe recovery assessment is required before recovery can be considered.
        </p>
      ) : null}
      {summary.failureGuidance && (
        <p className="text-sm text-muted-foreground">{summary.failureGuidance}</p>
      )}
      <p className="text-xs text-muted-foreground">
        {summary.historyAvailable
          ? `${summary.attemptCount} ${summary.attemptCount === 1 ? "attempt" : "attempts"} recorded`
          : "Attempt history is not available for this execution."}
      </p>
    </section>
  );
}
