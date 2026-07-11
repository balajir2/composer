"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { resumeExecution } from "@/lib/api/executions";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "sonner";

export function ApproveDialog({ executionId }: { executionId: string }) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");

  const mutation = useMutation({
    mutationFn: (approved: boolean) =>
      resumeExecution(executionId, {
        decision: approved ? "approved" : "rejected",
        note: note || undefined,
      }),
    onSuccess: () => {
      setOpen(false);
      toast.success("Decision recorded.");
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "Failed."),
  });

  return (
    <>
      <Button onClick={() => setOpen(true)}>Review</Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Review this step</DialogTitle>
            <DialogDescription>Approve or reject to continue the workflow.</DialogDescription>
          </DialogHeader>
          <Textarea
            placeholder="Optional note"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            className="text-sm"
          />
          <DialogFooter className="gap-2">
            <Button
              variant="outline"
              onClick={() => mutation.mutate(false)}
              disabled={mutation.isPending}
            >
              Reject
            </Button>
            <Button onClick={() => mutation.mutate(true)} disabled={mutation.isPending}>
              Approve
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
