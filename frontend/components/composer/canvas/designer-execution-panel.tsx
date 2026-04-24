"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSession } from "next-auth/react";
import { AlertCircle, CheckCircle2, Clock, Loader2, X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { getExecution } from "@/lib/api/executions";
import { subscribeExecution, type ComposerEvent } from "@/lib/ws";

type NodeRunState = {
  status: "running" | "completed" | "failed";
  nodeName?: string;
  nodeType?: string;
  input?: unknown;
  output?: unknown;
  error?: string;
  startedAt: number;
};

export type DesignerExecutionState = {
  byNodeId: Record<string, NodeRunState>;
  currentNodeId: string | null;
  status: string;
};

const TERMINAL_STATES = new Set(["completed", "failed", "cancelled"]);

/**
 * Side panel that pins onto the designer while a draft run is in flight.
 * Shows overall status, per-node live results, the caller's input, and —
 * critically — any failure error so the designer never has to leave the
 * canvas to understand what went wrong.
 *
 * Follows OAB's in-designer ExecutionPanel pattern (workflow-builder/
 * ExecutionPanel.tsx) — no navigation, run completes in-place.
 */
export function DesignerExecutionPanel({
  executionId,
  onClose,
  onRunStateChange,
}: {
  executionId: string;
  onClose: () => void;
  onRunStateChange?: (state: DesignerExecutionState) => void;
}) {
  const { data: session } = useSession();
  const token = (session as unknown as { accessToken?: string } | null)?.accessToken;

  const [status, setStatus] = useState<string>("running");
  const [byNodeId, setByNodeId] = useState<Record<string, NodeRunState>>({});
  const [currentNodeId, setCurrentNodeId] = useState<string | null>(null);

  // Fallback polling — WS events can land after the backend already closed
  // the bus, so the poll picks up terminal status/error for reliability.
  const { data: execution } = useQuery({
    queryKey: ["execution", executionId],
    queryFn: () => getExecution(executionId),
    refetchInterval: TERMINAL_STATES.has(status) ? false : 2000,
  });

  useEffect(() => {
    if (!execution) return;
    if (TERMINAL_STATES.has(execution.status)) {
      setStatus(execution.status);
    }
  }, [execution]);

  // Stash the current onRunStateChange in a ref so the WS handler isn't
  // re-subscribed every render.  The canvas decorator reads through this.
  const onChangeRef = useRef(onRunStateChange);
  useEffect(() => {
    onChangeRef.current = onRunStateChange;
  });
  useEffect(() => {
    onChangeRef.current?.({ byNodeId, currentNodeId, status });
  }, [byNodeId, currentNodeId, status]);

  useEffect(() => {
    if (!token || !executionId) return;
    const unsub = subscribeExecution(executionId, token, (ev: ComposerEvent) => {
      if (ev.type === "workflow_started") {
        setStatus("running");
        return;
      }
      if (ev.type === "workflow_completed") {
        const s = (ev as unknown as { status?: string }).status ?? "completed";
        setStatus(s);
        setCurrentNodeId(null);
        return;
      }
      if (ev.type === "approval_required") {
        setStatus("waiting_approval");
        return;
      }
      if (ev.type === "node_started") {
        const e = ev as unknown as {
          nodeId: string;
          nodeName?: string;
          nodeType?: string;
        };
        setCurrentNodeId(e.nodeId);
        setByNodeId((prev) => ({
          ...prev,
          [e.nodeId]: {
            status: "running",
            nodeName: e.nodeName,
            nodeType: e.nodeType,
            startedAt: Date.now(),
          },
        }));
        return;
      }
      if (ev.type === "node_completed") {
        const e = ev as unknown as {
          nodeId: string;
          nodeName?: string;
          nodeType?: string;
          input?: unknown;
          output?: unknown;
        };
        setByNodeId((prev) => ({
          ...prev,
          [e.nodeId]: {
            ...(prev[e.nodeId] ?? { startedAt: Date.now() }),
            status: "completed",
            nodeName: e.nodeName ?? prev[e.nodeId]?.nodeName,
            nodeType: e.nodeType ?? prev[e.nodeId]?.nodeType,
            input: e.input,
            output: e.output,
          },
        }));
        return;
      }
      if (ev.type === "node_failed") {
        const e = ev as unknown as {
          nodeId: string;
          nodeName?: string;
          nodeType?: string;
          error?: string;
        };
        setByNodeId((prev) => ({
          ...prev,
          [e.nodeId]: {
            ...(prev[e.nodeId] ?? { startedAt: Date.now() }),
            status: "failed",
            nodeName: e.nodeName ?? prev[e.nodeId]?.nodeName,
            nodeType: e.nodeType ?? prev[e.nodeId]?.nodeType,
            error: e.error,
          },
        }));
      }
    });
    return unsub;
  }, [executionId, token]);

  const orderedNodeEntries = useMemo(
    () =>
      Object.entries(byNodeId).sort(
        ([, a], [, b]) => a.startedAt - b.startedAt
      ),
    [byNodeId]
  );

  const isTerminal = TERMINAL_STATES.has(status);
  const nodeCount = orderedNodeEntries.length;

  return (
    <div className="flex h-full w-80 flex-col overflow-hidden border-l bg-background">
      {/* Header */}
      <div className="flex items-center justify-between border-b px-4 py-3">
        <div className="min-w-0">
          <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Draft execution
          </p>
          <div className="mt-1 flex items-center gap-2">
            {status === "running" && (
              <Loader2 className="size-3.5 animate-spin text-primary" />
            )}
            {status === "completed" && (
              <CheckCircle2 className="size-3.5 text-emerald-600" />
            )}
            {status === "failed" && (
              <AlertCircle className="size-3.5 text-destructive" />
            )}
            {status === "waiting_approval" && <Clock className="size-3.5 text-amber-600" />}
            <Badge variant={status === "failed" ? "destructive" : "default"}>
              {status}
            </Badge>
          </div>
        </div>
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7"
          onClick={onClose}
          aria-label="Close execution panel"
        >
          <X className="h-4 w-4" />
        </Button>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto p-4 text-sm">
        {/* Show the exact input the backend received so designers can
            confirm the Run Draft form actually sent the new value (vs. a
            stale default or a prompt with a hardcoded literal). */}
        {execution?.input !== null && execution?.input !== undefined && (
          <div className="mb-4">
            <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Input
            </p>
            <pre className="max-h-32 overflow-auto whitespace-pre-wrap break-words rounded-md border bg-muted/40 px-2 py-1 font-mono text-[11px] text-muted-foreground">
              {typeof execution.input === "string"
                ? execution.input
                : JSON.stringify(execution.input, null, 2)}
            </pre>
          </div>
        )}

        {/* Top-level failure — rare case where the executor fails before any
            node_started event (e.g. validation). */}
        {status === "failed" && execution?.error && (
          <div className="mb-4 flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-destructive">
            <AlertCircle className="mt-0.5 size-4 shrink-0" />
            <div className="min-w-0 flex-1">
              <div className="font-semibold">Execution failed</div>
              <pre className="mt-1 whitespace-pre-wrap break-words font-mono text-xs">
                {execution.error}
              </pre>
            </div>
          </div>
        )}

        {nodeCount === 0 && !isTerminal && (
          <div className="rounded-md border border-dashed px-3 py-6 text-center text-xs text-muted-foreground">
            Waiting for first node to start…
          </div>
        )}
        {nodeCount === 0 && isTerminal && status !== "failed" && (
          <div className="rounded-md border border-dashed px-3 py-6 text-center text-xs text-muted-foreground">
            No node events were recorded.
          </div>
        )}

        {orderedNodeEntries.length > 0 && (
          <ol className="space-y-2">
            {orderedNodeEntries.map(([nodeId, n]) => (
              <li
                key={nodeId}
                className={`rounded-md border px-3 py-2 text-xs ${
                  n.status === "failed"
                    ? "border-destructive/40 bg-destructive/5"
                    : n.status === "completed"
                      ? "border-emerald-500/30 bg-emerald-500/5"
                      : "border-primary/30 bg-primary/5"
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-medium">
                      {n.nodeName ?? n.nodeType ?? nodeId}
                    </div>
                    {n.nodeType && n.nodeName && n.nodeName !== n.nodeType && (
                      <div className="truncate text-[10px] text-muted-foreground">
                        {n.nodeType}
                      </div>
                    )}
                  </div>
                  <Badge
                    variant={
                      n.status === "failed"
                        ? "destructive"
                        : n.status === "completed"
                          ? "default"
                          : "secondary"
                    }
                  >
                    {n.status}
                  </Badge>
                </div>
                {n.error && (
                  <pre className="mt-1 whitespace-pre-wrap break-words font-mono text-[11px] text-destructive">
                    {n.error}
                  </pre>
                )}
                {n.input !== undefined && n.input !== null && (
                  <details className="mt-1 text-[11px]">
                    <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
                      input
                    </summary>
                    <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded bg-muted/40 px-2 py-1 font-mono text-muted-foreground">
                      {typeof n.input === "string"
                        ? n.input
                        : JSON.stringify(n.input, null, 2)}
                    </pre>
                  </details>
                )}
                {n.status === "completed" && n.output !== undefined && (
                  <details className="mt-1 text-[11px]" open>
                    <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
                      output
                    </summary>
                    <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded bg-muted/60 px-2 py-1 font-mono text-muted-foreground">
                      {typeof n.output === "string"
                        ? n.output
                        : JSON.stringify(n.output, null, 2)}
                    </pre>
                  </details>
                )}
              </li>
            ))}
          </ol>
        )}

        {/* Final output when the whole flow completes successfully */}
        {status === "completed" && execution?.output !== null && execution?.output !== undefined && (
          <div className="mt-4">
            <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Final output
            </p>
            <pre className="max-h-60 overflow-auto whitespace-pre-wrap break-words rounded-md border bg-muted/60 px-2 py-1 font-mono text-[11px] text-muted-foreground">
              {typeof execution.output === "string"
                ? execution.output
                : JSON.stringify(execution.output, null, 2)}
            </pre>
          </div>
        )}
      </div>
    </div>
  );
}
