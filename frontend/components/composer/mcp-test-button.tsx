"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { testMcpConnection } from "@/lib/api/mcp-servers";

export function McpTestButton({
  serverId,
  serverName,
}: {
  serverId: string;
  serverName: string;
}) {
  const qc = useQueryClient();
  const mutation = useMutation({
    mutationFn: () => testMcpConnection(serverId),
    onSuccess: (res) => {
      if (res.ok) {
        toast.success(`${serverName}: ${res.message || "connected"}`);
      } else {
        toast.error(`${serverName}: ${res.message || "failed"}`);
      }
      void qc.invalidateQueries({ queryKey: ["admin-mcp-servers"] });
      void qc.invalidateQueries({ queryKey: ["mcp-servers"] });
    },
    onError: (err) =>
      toast.error(err instanceof Error ? err.message : "Test failed."),
  });

  return (
    <Button
      size="sm"
      variant="outline"
      onClick={() => mutation.mutate()}
      disabled={mutation.isPending}
    >
      {mutation.isPending ? "Testing…" : "Test"}
    </Button>
  );
}
