"use client";

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertCircle } from "lucide-react";
import { getExecution } from "@/lib/api/executions";
import { ExecutionProgress } from "@/components/composer/execution-progress";
import { ExecutionResult } from "@/components/composer/execution-result";
import { ApproveDialog } from "@/components/composer/approve-dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/composer/empty-state";

const TERMINAL_STATES = new Set(["completed", "failed", "cancelled"]);

export default function ExecutionPage({
  params,
}: {
  params: { workflowId: string; executionId: string };
}) {
  const { executionId } = params;
  const [finalStatus, setFinalStatus] = useState<string | null>(null);
  const [finalOutput, setFinalOutput] = useState<unknown>(null);

  const {
    data: execution,
    isLoading,
    isError,
  } = useQuery({
    queryKey: ["execution", executionId],
    queryFn: () => getExecution(executionId),
    // Fall-back polling if the WebSocket drops events.  Stop once we have a
    // locally-confirmed terminal state (prevents an infinite poll loop after
    // the execution finished).
    refetchInterval: finalStatus && TERMINAL_STATES.has(finalStatus) ? false : 2000,
  });

  // Synthesize the terminal handoff from polling so the UI reflects the
  // backend-recorded state even when WS events were missed (e.g., subscribe
  // landed after the backend already emitted + closed the bus).
  useEffect(() => {
    if (!execution) return;
    if (TERMINAL_STATES.has(execution.status) && finalStatus === null) {
      setFinalStatus(execution.status);
      if (execution.output !== null && execution.output !== undefined) {
        setFinalOutput(execution.output);
      }
    }
  }, [execution, finalStatus]);

  if (isLoading) return <Skeleton className="h-64 w-full" />;
  if (isError || !execution) {
    return (
      <EmptyState
        title="Execution not found"
        description="This execution doesn't exist or you don't have access to it."
      />
    );
  }

  const status = finalStatus ?? execution.status;

  return (
    <div className="max-w-3xl space-y-6">
      <h2 className="text-2xl font-semibold">Execution</h2>
      <ExecutionProgress
        executionId={executionId}
        initialStatus={execution.status}
        onTerminal={(s, out) => {
          setFinalStatus(s);
          if (out !== undefined) setFinalOutput(out);
        }}
      />
      {status === "failed" && execution.error && (
        <div className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          <AlertCircle className="mt-0.5 size-4 shrink-0" />
          <div className="min-w-0 flex-1">
            <div className="font-semibold">Execution failed</div>
            <pre className="mt-1 whitespace-pre-wrap break-words font-mono text-xs">
              {execution.error}
            </pre>
          </div>
        </div>
      )}
      {status === "waiting_approval" && <ApproveDialog executionId={executionId} />}
      {finalOutput !== null && <ExecutionResult output={finalOutput} />}
      {finalOutput === null && status === "completed" && execution.output !== null && (
        <ExecutionResult output={execution.output} />
      )}
    </div>
  );
}
