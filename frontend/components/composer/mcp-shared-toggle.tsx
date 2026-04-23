"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { setMcpShared } from "@/lib/api/mcp-servers";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";

interface McpSharedToggleProps {
  serverId: string;
  serverName: string;
  isShared: boolean;
}

export function McpSharedToggle({ serverId, serverName, isShared }: McpSharedToggleProps) {
  const qc = useQueryClient();

  const mutation = useMutation({
    mutationFn: () => setMcpShared(serverId, !isShared),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["admin-mcp-servers"] });
      toast.success(isShared ? `${serverName} is now private.` : `${serverName} is now shared.`);
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "Failed to update."),
  });

  return (
    <Button
      variant={isShared ? "default" : "outline"}
      size="sm"
      onClick={() => mutation.mutate()}
      disabled={mutation.isPending}
    >
      {mutation.isPending ? "Updating…" : isShared ? "Shared" : "Private"}
    </Button>
  );
}
