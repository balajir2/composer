"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation } from "@tanstack/react-query";
import { createWorkflow } from "@/lib/api/workflows";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "sonner";

const MINIMAL_WF_BODY = {
  nodes: [
    {
      id: "s",
      type: "start" as const,
      position: { x: 0, y: 0 },
      data: { label: "Start" },
    },
    {
      id: "e",
      type: "end" as const,
      position: { x: 400, y: 0 },
      data: { label: "End" },
    },
  ],
  edges: [{ id: "e1", source: "s", target: "e" }],
  isTemplate: false,
  isPublic: false,
};

export function NewWorkflowDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const router = useRouter();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  const mutation = useMutation({
    mutationFn: () =>
      createWorkflow({
        name: name.trim(),
        description: description.trim() || null,
        ...MINIMAL_WF_BODY,
      }),
    onSuccess: (created) => {
      onOpenChange(false);
      setName("");
      setDescription("");
      router.push(`/designer/${created.id}`);
    },
    onError: (err) =>
      toast.error(err instanceof Error ? err.message : "Failed to create workflow."),
  });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    mutation.mutate();
  }

  function handleOpenChange(next: boolean) {
    if (!next) {
      setName("");
      setDescription("");
    }
    onOpenChange(next);
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New workflow</DialogTitle>
          <DialogDescription>
            Give your workflow a name to get started. You can change it later.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="wf-name">Name</Label>
            <Input
              id="wf-name"
              placeholder="My workflow"
              value={name}
              onChange={(e) => setName(e.target.value)}
              autoFocus
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="wf-description">
              Description <span className="font-normal text-muted-foreground">(optional)</span>
            </Label>
            <Textarea
              id="wf-description"
              placeholder="What does this workflow do?"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={3}
            />
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => handleOpenChange(false)}
              disabled={mutation.isPending}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={!name.trim() || mutation.isPending}>
              {mutation.isPending ? "Creating..." : "Create"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
