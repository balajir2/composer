"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { adminUpdateWorkflowFlags } from "@/lib/api/workflows";

export function WorkflowVisibilityToggle({
  workflowId,
  isPublic,
}: {
  workflowId: string;
  isPublic: boolean;
}) {
  const qc = useQueryClient();
  const mutation = useMutation({
    mutationFn: (nextValue: boolean) =>
      adminUpdateWorkflowFlags(workflowId, { isPublic: nextValue }),
    onSuccess: (_, v) => {
      toast.success(`Workflow is now ${v ? "public" : "private"}.`);
      void qc.invalidateQueries({ queryKey: ["admin-all-workflows"] });
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "Failed."),
  });

  return (
    <button
      type="button"
      onClick={() => mutation.mutate(!isPublic)}
      disabled={mutation.isPending}
      className="cursor-pointer disabled:cursor-wait disabled:opacity-60"
      title="Click to toggle visibility"
    >
      <Badge variant={isPublic ? "default" : "secondary"}>
        {isPublic ? "Public" : "Private"}
      </Badge>
    </button>
  );
}
