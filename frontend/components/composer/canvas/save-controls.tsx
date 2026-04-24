"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";

import { RunDraftDialog } from "./run-draft-dialog";

interface SaveControlsProps {
  workflowId: string;
  isSaving?: boolean;
  onSave: () => Promise<void>;
  /** Snapshot of the current canvas — used to read the Start node's
   *  declared inputVariables so Run Draft can prompt for them.
   *  Called only when Run Draft is clicked (not on every render). */
  getCurrentNodes: () => Array<{ type: string; data: Record<string, unknown> }>;
  /** Called when the Run Draft dialog successfully starts an execution.
   *  The designer uses this to pin the executionId and render live progress
   *  in-canvas — we deliberately do NOT navigate away. */
  onExecutionStarted: (executionId: string) => void;
}

export function SaveControls({
  workflowId,
  isSaving = false,
  onSave,
  getCurrentNodes,
  onExecutionStarted,
}: SaveControlsProps) {
  const [isPreparing, setIsPreparing] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);

  async function handleSave() {
    try {
      await onSave();
      toast.success("Workflow saved.");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to save workflow.");
    }
  }

  async function handleRunDraft() {
    setIsPreparing(true);
    try {
      // Save first so the latest canvas state is persisted before the run.
      await onSave();
      setDialogOpen(true);
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : "Failed to save before running."
      );
    } finally {
      setIsPreparing(false);
    }
  }

  return (
    <div className="flex items-center gap-2">
      <Button
        variant="outline"
        size="sm"
        onClick={handleSave}
        disabled={isSaving || isPreparing}
      >
        {isSaving ? "Saving…" : "Save"}
      </Button>
      <Button size="sm" onClick={handleRunDraft} disabled={isSaving || isPreparing}>
        {isPreparing ? "Saving…" : "Run draft"}
      </Button>
      <RunDraftDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        workflowId={workflowId}
        workflow={{ nodes: getCurrentNodes() }}
        onStarted={(executionId) => onExecutionStarted(executionId)}
      />
    </div>
  );
}
