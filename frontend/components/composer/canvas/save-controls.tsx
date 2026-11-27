"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";

import { RunDraftDialog } from "./run-draft-dialog";
import type { SaveStatus } from "@/lib/use-autosave";
import type { WorkflowWarning } from "@/lib/workflow-warnings";

interface SaveControlsProps {
  workflowId: string;
  isSaving?: boolean;
  saveStatus: SaveStatus;
  onSave: () => Promise<void>;
  /** Snapshot of the current canvas — used to read the Start node's
   *  declared inputVariables so Run Draft can prompt for them.
   *  Called only when Run Draft is clicked (not on every render). */
  getCurrentNodes: () => Array<{ type: string; data: Record<string, unknown> }>;
  /** Called when the Run Draft dialog successfully starts an execution.
   *  The designer uses this to pin the executionId and render live progress
   *  in-canvas — we deliberately do NOT navigate away. */
  onExecutionStarted: (executionId: string) => void;
  /** Problems the server reported on the last save. The canvas still saves with
   *  them, but running or publishing is refused until they are fixed. */
  warnings?: WorkflowWarning[];
}

export function SaveControls({
  workflowId,
  isSaving = false,
  saveStatus,
  onSave,
  getCurrentNodes,
  onExecutionStarted,
  warnings = [],
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
      toast.error(err instanceof Error ? err.message : "Failed to save before running.");
    } finally {
      setIsPreparing(false);
    }
  }

  return (
    <div className="flex items-center gap-3">
      {warnings.length > 0 && (
        <details
          data-testid="save-warnings"
          className="relative text-xs text-amber-700 dark:text-amber-400"
        >
          <summary className="cursor-pointer select-none">
            {`${warnings.length} ${warnings.length === 1 ? "problem" : "problems"}`}
            {warnings.some((w) => w.code === "missing_branch") && ": this workflow can't run yet"}
          </summary>
          <ul className="absolute right-0 z-20 mt-1 w-80 list-disc space-y-1 rounded-md border bg-card p-3 pl-6 text-foreground shadow-md">
            {warnings.map((w, i) => (
              <li key={`${w.code}:${w.nodeId}:${w.branch ?? ""}:${i}`}>{w.message}</li>
            ))}
          </ul>
        </details>
      )}
      <span className="text-xs text-muted-foreground">
        {saveStatus === "saving"
          ? "Saving…"
          : saveStatus === "error"
            ? "Save failed"
            : saveStatus === "dirty"
              ? "Unsaved changes"
              : saveStatus === "saved"
                ? "All changes saved"
                : ""}
      </span>
      <div className="flex items-center gap-2">
        <Button variant="outline" size="sm" onClick={handleSave} disabled={isSaving || isPreparing}>
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
    </div>
  );
}
