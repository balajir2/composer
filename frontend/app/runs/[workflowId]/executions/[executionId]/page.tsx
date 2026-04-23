"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getExecution } from "@/lib/api/executions";
import { ExecutionProgress } from "@/components/composer/execution-progress";
import { ExecutionResult } from "@/components/composer/execution-result";
import { ApproveDialog } from "@/components/composer/approve-dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/composer/empty-state";

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
    refetchInterval: finalStatus ? false : 2000, // fall-back polling if WS drops
  });

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
      {status === "waiting_approval" && <ApproveDialog executionId={executionId} />}
      {finalOutput !== null && <ExecutionResult output={finalOutput} />}
      {finalOutput === null && status === "completed" && execution.output !== null && (
        <ExecutionResult output={execution.output} />
      )}
    </div>
  );
}
