"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { createExecution } from "@/lib/api/executions";
import { toast } from "sonner";

interface SaveControlsProps {
  workflowId: string;
  isSaving?: boolean;
  onSave: () => Promise<void>;
}

export function SaveControls({ workflowId, isSaving = false, onSave }: SaveControlsProps) {
  const router = useRouter();
  const [isRunning, setIsRunning] = useState(false);

  async function handleSave() {
    try {
      await onSave();
      toast.success("Workflow saved.");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to save workflow.");
    }
  }

  async function handleRunDraft() {
    setIsRunning(true);
    try {
      // Save first so the latest canvas state is persisted before running.
      await onSave();
      const execution = await createExecution({ workflowId, input: {} });
      router.push(`/runs/${workflowId}/executions/${execution.id}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to start execution.");
    } finally {
      setIsRunning(false);
    }
  }

  return (
    <div className="flex items-center gap-2">
      <Button variant="outline" size="sm" onClick={handleSave} disabled={isSaving || isRunning}>
        {isSaving ? "Saving…" : "Save"}
      </Button>
      <Button size="sm" onClick={handleRunDraft} disabled={isSaving || isRunning}>
        {isRunning ? "Starting…" : "Run draft"}
      </Button>
    </div>
  );
}
