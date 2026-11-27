"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "next/navigation";
import { AlertCircle, CheckCircle2, Clock, Loader2 } from "lucide-react";
import {
  getExecution,
  getExecutionObservability,
  listExecutionAttempts,
} from "@/lib/api/executions";
import { ExecutionProgress } from "@/components/composer/execution-progress";
import { ExecutionResult } from "@/components/composer/execution-result";
import { ApproveDialog } from "@/components/composer/approve-dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/composer/empty-state";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ExecutionLifecycleSummary } from "@/components/composer/execution-lifecycle-summary";
import { ExecutionAttemptTimeline } from "@/components/composer/execution-attempt-timeline";
import { AdminExecutionDiagnostics } from "@/components/composer/admin-execution-diagnostics";
import { ComposerApiError } from "@/lib/api/client";
import type { ComposerEvent } from "@/lib/ws";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

const ACTIVE_STATES = new Set(["pending", "queued", "running", "waiting_approval"]);
const TERMINAL_STATES = new Set(["completed", "failed", "canceled"]);
const REFRESH_DEBOUNCE_MS = 300;

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

export default function ExecutionPage() {
  const { executionId } = useParams<{ workflowId: string; executionId: string }>();
  const queryClient = useQueryClient();
  const [finalStatus, setFinalStatus] = useState<string | null>(null);
  const [finalOutput, setFinalOutput] = useState<unknown>(null);
  const previousStatus = useRef<string>();

  const {
    data: execution,
    isLoading,
    isError,
    error,
  } = useQuery({
    queryKey: ["execution", executionId],
    queryFn: () => getExecution(executionId),
    refetchInterval: (query) =>
      ACTIVE_STATES.has(finalStatus ?? query.state.data?.status ?? "") ? 2000 : false,
  });

  const pollInterval =
    execution && ACTIVE_STATES.has(finalStatus ?? execution.status) ? 5000 : false;
  const summary = useQuery({
    queryKey: ["execution-observability", executionId],
    queryFn: () => getExecutionObservability(executionId),
    refetchInterval: pollInterval,
    retry: false,
  });
  const limit = 50;
  const offset = 0;
  const attempts = useQuery({
    queryKey: ["execution-attempts", executionId, limit, offset],
    queryFn: () => listExecutionAttempts(executionId, { limit, offset }),
    refetchInterval: pollInterval,
    retry: false,
  });
  // Only lifecycle events change the durable summary and attempts; per-node events are covered
  // by the active-status polling. A reconnect replays many events at once, so coalesce them into
  // one trailing refresh instead of three refetches per event.
  const refreshTimer = useRef<ReturnType<typeof setTimeout>>();
  const refreshExecution = useCallback(
    (ev: ComposerEvent) => {
      if (!ev.type.startsWith("workflow_") && !ev.type.startsWith("approval_")) return;
      clearTimeout(refreshTimer.current);
      refreshTimer.current = setTimeout(() => {
        void queryClient.invalidateQueries({ queryKey: ["execution", executionId] });
        void queryClient.invalidateQueries({
          queryKey: ["execution-observability", executionId],
        });
        void queryClient.invalidateQueries({ queryKey: ["execution-attempts", executionId] });
      }, REFRESH_DEBOUNCE_MS);
    },
    [executionId, queryClient]
  );
  useEffect(() => () => clearTimeout(refreshTimer.current), []);

  useEffect(() => {
    if (!execution) return;
    // A missed terminal socket event must still fetch the final durable
    // lifecycle and attempts before polling stops.
    if (
      ACTIVE_STATES.has(previousStatus.current ?? "") &&
      TERMINAL_STATES.has(execution.status) &&
      finalStatus === null
    ) {
      void queryClient.invalidateQueries({ queryKey: ["execution-observability", executionId] });
      void queryClient.invalidateQueries({ queryKey: ["execution-attempts", executionId] });
    }
    previousStatus.current = execution.status;
    // Pin the status the first time we see a terminal value from the polled
    // row.  WS-delivered terminal status arrives via onTerminal below.
    if (TERMINAL_STATES.has(execution.status) && finalStatus === null) {
      setFinalStatus(execution.status);
    }
    // Capture output as soon as the polled row has it — independently of
    // whether the WS terminal event already pinned finalStatus.  The WS
    // event only carries status, so without this branch a finalStatus that
    // arrived via WS would gate the output-from-DB path forever.
    if (finalOutput === null && execution.output !== null && execution.output !== undefined) {
      setFinalOutput(execution.output);
    }
  }, [execution, finalStatus, finalOutput, executionId, queryClient]);

  // The shared event callback invalidates persisted state; retain WS output
  // immediately while that final fetch is in flight.
  const handleTerminal = useCallback((s: string, out?: unknown) => {
    setFinalStatus(s);
    if (out !== undefined) setFinalOutput(out);
  }, []);

  if (isLoading) return <Skeleton className="h-64 w-full" />;
  const notFound = [error, summary.error, attempts.error].some(
    (error) => error instanceof ComposerApiError && error.status === 404
  );
  if (notFound || !execution) {
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
    finalOutput !== null && finalOutput !== undefined ? finalOutput : (execution.output ?? null);
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

      {isError && (
        <p role="status">Could not refresh execution. Displayed information may be stale.</p>
      )}
      {summary.data && <ExecutionLifecycleSummary summary={summary.data} stale={summary.isError} />}
      {summary.isLoading && <p role="status">Loading execution status...</p>}
      {summary.isError && (
        <div role="status" className="space-y-2 text-sm">
          <p>Could not refresh execution status.</p>
          <Button
            variant="outline"
            aria-label="Retry execution status refresh"
            onClick={() => void summary.refetch()}
          >
            Retry
          </Button>
        </div>
      )}
      <AdminExecutionDiagnostics diagnostics={summary.data?.adminDiagnostics} />

      <ExecutionProgress
        executionId={executionId}
        initialStatus={execution.status}
        onTerminal={handleTerminal}
        onEvent={refreshExecution}
      />

      {status === "failed" && execution.error && (
        <div className="border-destructive/40 bg-destructive/10 flex items-start gap-2 rounded-md border px-3 py-2 text-sm text-destructive">
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
                The workflow completed but didn&apos;t set a final output. Expand the node trace
                below to see intermediate values, or add an End node connected after your last
                useful node so its <code className="font-mono">lastOutput</code> propagates as the
                final result.
              </p>
            </CardContent>
          </Card>
        )
      )}

      <ExecutionAttemptTimeline
        page={attempts.data}
        isLoading={attempts.isLoading}
        isError={attempts.isError}
        onRetry={() => void attempts.refetch()}
      />

      {/* Caller's input — collapsed by default so the result stays
          prominent. */}
      {execution.input !== null && execution.input !== undefined && (
        <details className="rounded-md border bg-card">
          <summary className="cursor-pointer select-none px-4 py-3 text-sm font-medium text-muted-foreground hover:text-foreground">
            Show input
          </summary>
          <div className="border-t px-4 py-3">
            <pre className="bg-muted/30 max-h-48 overflow-auto rounded-md p-3 text-xs">
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
                      <pre className="bg-muted/40 mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded px-2 py-1 font-mono text-muted-foreground">
                        {renderValue(nr.input)}
                      </pre>
                    </details>
                  )}
                  {nr.output !== undefined && nr.output !== null && (
                    <details className="mt-1 text-[11px]">
                      <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
                        output
                      </summary>
                      <pre className="bg-muted/60 mt-1 max-h-60 overflow-auto whitespace-pre-wrap break-words rounded px-2 py-1 font-mono text-muted-foreground">
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
