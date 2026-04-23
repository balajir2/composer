"use client";

import { useEffect, useState } from "react";
import { useSession } from "next-auth/react";
import { Badge } from "@/components/ui/badge";
import { subscribeExecution, type ComposerEvent } from "@/lib/ws";

type NodeStatus = {
  nodeId: string;
  nodeName: string;
  status: "running" | "completed" | "failed";
  output?: unknown;
  error?: string;
};

export function ExecutionProgress({
  executionId,
  initialStatus,
  onTerminal,
}: {
  executionId: string;
  initialStatus: string;
  onTerminal: (finalStatus: string, output?: unknown) => void;
}) {
  const { data: session } = useSession();
  const token = (session as unknown as { accessToken?: string } | null)?.accessToken;
  const [status, setStatus] = useState<string>(initialStatus);
  const [nodes, setNodes] = useState<NodeStatus[]>([]);

  useEffect(() => {
    if (!token) return;
    const unsub = subscribeExecution(executionId, token, (ev: ComposerEvent) => {
      if (ev.type === "workflow_started") setStatus("running");
      if (ev.type === "workflow_completed") {
        const s = (ev as unknown as { status?: string }).status ?? "completed";
        setStatus(s);
        onTerminal(s, (ev as unknown as { output?: unknown }).output);
      }
      if (ev.type === "approval_required") setStatus("waiting_approval");
      if (ev.type === "node_started") {
        const e = ev as unknown as { nodeId: string; nodeName: string };
        setNodes((prev) => [
          ...prev,
          { nodeId: e.nodeId, nodeName: e.nodeName, status: "running" },
        ]);
      }
      if (ev.type === "node_completed") {
        const e = ev as unknown as { nodeId: string; output?: unknown };
        setNodes((prev) =>
          prev.map((n) =>
            n.nodeId === e.nodeId ? { ...n, status: "completed", output: e.output } : n
          )
        );
      }
      if (ev.type === "node_failed") {
        const e = ev as unknown as { nodeId: string; error?: string };
        setNodes((prev) =>
          prev.map((n) => (n.nodeId === e.nodeId ? { ...n, status: "failed", error: e.error } : n))
        );
      }
    });
    return unsub;
  }, [executionId, token, onTerminal]);

  return (
    <div className="space-y-4">
      <div>
        <span className="text-muted-foreground pr-2 text-sm">Status:</span>
        <Badge variant={status === "failed" ? "destructive" : "default"}>{status}</Badge>
      </div>
      <div className="space-y-2">
        {nodes.map((n) => (
          <div
            key={n.nodeId}
            className="flex items-center justify-between rounded-md border px-3 py-2 text-sm"
          >
            <div>
              <div className="font-medium">{n.nodeName}</div>
              {n.error && <div className="text-destructive text-xs">{n.error}</div>}
            </div>
            <Badge variant={n.status === "failed" ? "destructive" : "secondary"}>{n.status}</Badge>
          </div>
        ))}
        {nodes.length === 0 && (
          <div className="text-muted-foreground rounded-md border border-dashed px-3 py-6 text-center text-xs">
            Waiting for node events…
          </div>
        )}
      </div>
    </div>
  );
}
