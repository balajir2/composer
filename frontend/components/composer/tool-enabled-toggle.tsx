"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { upsertDeploymentSetting } from "@/lib/api/admin";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";

interface ToolEnabledToggleProps {
  settingKey: string;
  toolLabel: string;
  enabled: boolean;
}

export function ToolEnabledToggle({ settingKey, toolLabel, enabled }: ToolEnabledToggleProps) {
  const qc = useQueryClient();

  const mutation = useMutation({
    mutationFn: () => upsertDeploymentSetting(settingKey, enabled ? "false" : "true"),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["deployment-settings"] });
      toast.success(enabled ? `${toolLabel} disabled.` : `${toolLabel} enabled.`);
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "Failed to update."),
  });

  return (
    <Button
      variant={enabled ? "default" : "outline"}
      size="sm"
      onClick={() => mutation.mutate()}
      disabled={mutation.isPending}
    >
      {mutation.isPending ? "Updating…" : enabled ? "Enabled" : "Disabled"}
    </Button>
  );
}
