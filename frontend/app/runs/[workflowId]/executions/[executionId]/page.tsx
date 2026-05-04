"use client";

import { useCallback, useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, CheckCircle2, Clock, Loader2 } from "lucide-react";
import { getExecution } from "@/lib/api/executions";
import { ExecutionProgress } from "@/components/composer/execution-progress";
import { ExecutionResult } from "@/components/composer/execution-result";
import { ApproveDialog } from "@/components/composer/approve-dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/composer/empty-state";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

const TERMINAL_STATES = new Set(["completed", "failed", "cancelled"]);

type NodeResult = {
  node_id?: string;
  status?: string;
  input?: unknown;
  output?: unknown;
  error?: string | null;
};

function renderValue(v: unknown): string {
  if (v === null || v === undefined) return "(none)";
  if (typeof v === "string") return v;
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}

export default function ExecutionPage({
  params,
}: {
  params: { workflowId: string; executionId: string };
}) {
  const { executionId } = params;
  const queryClient = useQueryClient();
  const [finalStatus, setFinalStatus] = useState<string | null>(null);
  const [finalOutput, setFinalOutput] = useState<unknown>(null);

  const {
    data: execution,
    isLoading,
    isError,
  } = useQuery({
    queryKey: ["execution", executionId],
    queryFn: () => getExecution(executionId),
    // Keep polling until BOTH status is terminal AND output is resolved.
    // Stopping on status alone leaves a window where the WS terminal event
    // pinned status="completed" but the persisted output hadn't been
    // fetched yet — the row sat untouched until window-focus refetch
    // (~minutes), showing the user "didn't set a final output" in the
    // meantime even though the DB row is correct.
    refetchInterval:
      finalStatus &&
      TERMINAL_STATES.has(finalStatus) &&
      finalOutput !== null &&
      finalOutput !== undefined
        ? false
        : 2000,
  });

  useEffect(() => {
    if (!execution) return;
    // Pin the status the first time we see a terminal value from the polled
    // row.  WS-delivered terminal status arrives via onTerminal below.
    if (TERMINAL_STATES.has(execution.status) && finalStatus === null) {
      setFinalStatus(execution.status);
    }
    // Capture output as soon as the polled row has it — independently of
    // whether the WS terminal event already pinned finalStatus.  The WS
    // event only carries status, so without this branch a finalStatus that
    // arrived via WS would gate the output-from-DB path forever.
    if (
      finalOutput === null &&
      execution.output !== null &&
      execution.output !== undefined
    ) {
      setFinalOutput(execution.output);
    }
  }, [execution, finalStatus, finalOutput]);

  // When the WS terminal event arrives, force one immediate refetch so we
  // pull the persisted output without waiting for the next 2s poll.  The
  // backend's executor awaits the DB persist before emitting the terminal
  // event, so by the time this fires the output is guaranteed to be in
  // the row.
  const handleTerminal = useCallback(
    (s: string, out?: unknown) => {
      setFinalStatus(s);
      if (out !== undefined) setFinalOutput(out);
      void queryClient.invalidateQueries({ queryKey: ["execution", executionId] });
    },
    [executionId, queryClient]
  );

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
  // Surface either the WS-delivered output or the polled execution row's
  // output — whichever fired first.
  const resolvedOutput =
    finalOutput !== null && finalOutput !== undefined
      ? finalOutput
      : (execution.output ?? null);
  const hasResolvedOutput = resolvedOutput !== null && resolvedOutput !== undefined;

  // Per-node outputs straight from the persisted execution row — works
  // even when WS events were missed and gives users a complete view of
  // intermediate values regardless of whether the final node produced
  // a non-null finalOutput.
  const nodeResults =
    execution.nodeResults && typeof execution.nodeResults === "object"
      ? (execution.nodeResults as Record<string, NodeResult>)
      : {};
  const orderedNodes = Object.entries(nodeResults);

  return (
    <div className="max-w-3xl space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-semibold">Execution</h2>
        <div className="flex items-center gap-2">
          {status === "running" && <Loader2 className="size-4 animate-spin text-primary" />}
          {status === "completed" && <CheckCircle2 className="size-4 text-emerald-600" />}
          {status === "failed" && <AlertCircle className="size-4 text-destructive" />}
          {status === "waiting_approval" && <Clock className="size-4 text-amber-600" />}
          <Badge variant={status === "failed" ? "destructive" : "default"}>{status}</Badge>
        </div>
      </div>

      <ExecutionProgress
        executionId={executionId}
        initialStatus={execution.status}
        onTerminal={handleTerminal}
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

      {/* Final result lives at the top of the post-progress sections so
          it's the first thing the user sees.  Input + trace are
          collapsible underneath for drill-down. */}
      {hasResolvedOutput ? (
        <ExecutionResult output={resolvedOutput} />
      ) : (
        status === "completed" && (
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Result</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-muted-foreground">
                The workflow completed but didn&apos;t set a final output.
                Expand the node trace below to see intermediate values, or
                add an End node connected after your last useful node so
                its <code className="font-mono">lastOutput</code> propagates
                as the final result.
              </p>
            </CardContent>
          </Card>
        )
      )}

      {/* Caller's input — collapsed by default so the result stays
          prominent. */}
      {execution.input !== null && execution.input !== undefined && (
        <details className="rounded-md border bg-card">
          <summary className="cursor-pointer select-none px-4 py-3 text-sm font-medium text-muted-foreground hover:text-foreground">
            Show input
          </summary>
          <div className="border-t px-4 py-3">
            <pre className="max-h-48 overflow-auto rounded-md bg-muted/30 p-3 text-xs">
              {renderValue(execution.input)}
            </pre>
          </div>
        </details>
      )}

      {/* Per-node trace — collapsed by default so users see only the
          final result.  Expand to inspect intermediate inputs/outputs.
          Useful when the final node produced a null/empty output or
          something looks off and you need to drill in. */}
      {orderedNodes.length > 0 && (
        <details className="rounded-md border bg-card">
          <summary className="cursor-pointer select-none px-4 py-3 text-sm font-medium text-muted-foreground hover:text-foreground">
            Show node-by-node trace ({orderedNodes.length})
          </summary>
          <div className="space-y-3 border-t px-4 py-3">
            {orderedNodes.map(([nodeId, nr]) => {
              const nodeStatus = nr.status ?? "unknown";
              return (
                <div
                  key={nodeId}
                  className={`rounded-md border px-3 py-2 ${
                    nodeStatus === "failed"
                      ? "border-destructive/40 bg-destructive/5"
                      : nodeStatus === "completed"
                        ? "border-emerald-500/30 bg-emerald-500/5"
                        : "border-border"
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <code className="font-mono text-xs">{nodeId}</code>
                    <Badge variant={nodeStatus === "failed" ? "destructive" : "secondary"}>
                      {nodeStatus}
                    </Badge>
                  </div>
                  {nr.error && (
                    <pre className="mt-1 whitespace-pre-wrap break-words font-mono text-[11px] text-destructive">
                      {nr.error}
                    </pre>
                  )}
                  {nr.input !== undefined && nr.input !== null && (
                    <details className="mt-1 text-[11px]">
                      <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
                        input
                      </summary>
                      <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded bg-muted/40 px-2 py-1 font-mono text-muted-foreground">
                        {renderValue(nr.input)}
                      </pre>
                    </details>
                  )}
                  {nr.output !== undefined && nr.output !== null && (
                    <details className="mt-1 text-[11px]">
                      <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
                        output
                      </summary>
                      <pre className="mt-1 max-h-60 overflow-auto whitespace-pre-wrap break-words rounded bg-muted/60 px-2 py-1 font-mono text-muted-foreground">
                        {renderValue(nr.output)}
                      </pre>
                    </details>
                  )}
                </div>
              );
            })}
          </div>
        </details>
      )}

    </div>
  );
}
