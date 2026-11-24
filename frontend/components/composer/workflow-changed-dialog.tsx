"use client";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";

function whenText(currentUpdatedAt: string | null): string {
  if (currentUpdatedAt) {
    const when = new Date(currentUpdatedAt);
    if (!Number.isNaN(when.getTime())) return `at ${when.toLocaleString()}`;
  }
  return "since you opened it";
}

/**
 * Shown when a save or builder message was refused because someone else changed
 * the workflow after this page loaded it. The user must pick: discard their
 * unsaved changes and load the latest, or save theirs over the other person's.
 */
export function WorkflowChangedDialog({
  open,
  currentUpdatedAt,
  busy = false,
  onReload,
  onOverwrite,
}: {
  open: boolean;
  currentUpdatedAt: string | null;
  busy?: boolean;
  onReload: () => void;
  onOverwrite: () => void;
}) {
  return (
    <Dialog open={open} onOpenChange={() => undefined}>
      <DialogContent showCloseButton={false}>
        <DialogHeader>
          <DialogTitle>This workflow was saved elsewhere</DialogTitle>
          <DialogDescription>
            It was saved from another tab or by someone else {whenText(currentUpdatedAt)}. Reload to
            discard your unsaved changes and see the latest, or overwrite to keep yours and replace
            it.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" disabled={busy} onClick={onReload}>
            Reload
          </Button>
          <Button variant="destructive" disabled={busy} onClick={onOverwrite}>
            Overwrite
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
