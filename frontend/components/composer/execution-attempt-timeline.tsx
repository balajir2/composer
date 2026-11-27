import { Badge } from "@/components/ui/badge";
import type { ExecutionAttemptPage } from "@/lib/api/executions";

type Attempt = ExecutionAttemptPage["items"][number];

const RESULT_LABELS: Record<string, string> = {
  running: "In progress",
  completed: "Completed",
  paused: "Waiting for approval",
  failed: "Failed",
  cancelled: "Canceled",
  canceled: "Canceled",
  superseded: "Superseded",
};
const TRIGGER_LABELS: Record<string, string> = {
  run: "Started",
  resume: "Resumed",
  recovery: "Recovery attempt",
};

function formatDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Time unavailable" : date.toLocaleString();
}

function AttemptItem({ attempt }: { attempt: Attempt }) {
  const result = Object.hasOwn(RESULT_LABELS, attempt.result)
    ? RESULT_LABELS[attempt.result]
    : "Result unavailable";
  const trigger = Object.hasOwn(TRIGGER_LABELS, attempt.triggerKind)
    ? TRIGGER_LABELS[attempt.triggerKind]
    : "Trigger unavailable";
  return (
    <li className="space-y-2 rounded-md border p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-medium">Attempt {attempt.ordinal}</h3>
        <Badge variant={attempt.result === "failed" ? "destructive" : "secondary"}>{result}</Badge>
      </div>
      <p className="text-sm text-muted-foreground">{trigger}</p>
      <p className="text-xs text-muted-foreground">
        {formatDate(attempt.startedAt)}
        {attempt.endedAt ? ` to ${formatDate(attempt.endedAt)}` : " (in progress)"}
      </p>
      {attempt.hasApproval && <p className="text-sm">Approval associated with this attempt.</p>}
      {attempt.failureGuidance && <p className="text-sm">{attempt.failureGuidance}</p>}
    </li>
  );
}

export function ExecutionAttemptTimeline({
  page,
  isLoading,
  isError,
  onRetry,
}: {
  page: ExecutionAttemptPage | undefined;
  isLoading: boolean;
  isError: boolean;
  onRetry: () => void;
}) {
  if (isLoading && !page) {
    return (
      <section aria-labelledby="execution-attempts-heading">
        <h2 id="execution-attempts-heading" className="mb-3 text-base font-semibold">
          Attempt history
        </h2>
        <p role="status" className="text-sm text-muted-foreground">
          Loading attempt history...
        </p>
      </section>
    );
  }

  const items = [...(page?.items ?? [])].sort((a, b) => a.ordinal - b.ordinal);
  return (
    <section aria-labelledby="execution-attempts-heading" className="space-y-3">
      <h2 id="execution-attempts-heading" className="text-base font-semibold">
        Attempt history
      </h2>
      {isError && (
        <div
          role="status"
          className="border-destructive/40 flex flex-wrap items-center justify-between gap-2 rounded-md border p-3 text-sm"
        >
          <span>Could not refresh attempt history. Displayed information may be stale.</span>
          <button
            type="button"
            onClick={onRetry}
            className="rounded-md border px-3 py-1.5 font-medium hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            aria-label="Retry attempt history refresh"
          >
            Retry
          </button>
        </div>
      )}
      {!page ? null : !page.historyAvailable ? (
        <p className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
          This execution started before attempt history was recorded.
        </p>
      ) : items.length === 0 ? (
        <p className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
          No attempts to display.
        </p>
      ) : (
        <>
          <ol className="space-y-2">
            {items.map((attempt) => (
              <AttemptItem key={attempt.id} attempt={attempt} />
            ))}
          </ol>
          {page.total > items.length && (
            <p className="text-sm text-muted-foreground">
              Showing {items.length} of {page.total} attempts
            </p>
          )}
        </>
      )}
    </section>
  );
}
