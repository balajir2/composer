"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { adminUpdateWorkflowFlags } from "@/lib/api/workflows";

export function WorkflowProductionToggle({
  workflowId,
  isProduction,
  currentSlug,
}: {
  workflowId: string;
  isProduction: boolean;
  currentSlug: string | null;
}) {
  const [dialogOpen, setDialogOpen] = useState(false);
  const [slug, setSlug] = useState(currentSlug ?? "");
  const qc = useQueryClient();

  const publishMutation = useMutation({
    mutationFn: (nextSlug: string) =>
      adminUpdateWorkflowFlags(workflowId, {
        isProduction: true,
        externalSlug: nextSlug,
      }),
    onSuccess: () => {
      toast.success("Workflow published.");
      setDialogOpen(false);
      void qc.invalidateQueries({ queryKey: ["admin-all-workflows"] });
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "Failed."),
  });

  const unpublishMutation = useMutation({
    mutationFn: () =>
      adminUpdateWorkflowFlags(workflowId, { isProduction: false }),
    onSuccess: () => {
      toast.success("Workflow unpublished.");
      void qc.invalidateQueries({ queryKey: ["admin-all-workflows"] });
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "Failed."),
  });

  if (isProduction) {
    return (
      <button
        type="button"
        onClick={() => {
          if (window.confirm("Unpublish this workflow? The external slug will be cleared.")) {
            unpublishMutation.mutate();
          }
        }}
        disabled={unpublishMutation.isPending}
        className="cursor-pointer disabled:cursor-wait disabled:opacity-60"
        title="Click to unpublish"
      >
        <Badge variant="default">Yes</Badge>
      </button>
    );
  }

  return (
    <>
      <button
        type="button"
        onClick={() => {
          setSlug(currentSlug ?? "");
          setDialogOpen(true);
        }}
        className="cursor-pointer"
        title="Click to publish"
      >
        <Badge variant="outline">No</Badge>
      </button>
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        {dialogOpen && (
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Publish workflow</DialogTitle>
              <DialogDescription>
                Publishing makes the workflow invokable at{" "}
                <code>POST /api/run/&#123;slug&#125;</code> using an API key.
                Choose a URL-safe identifier (2–64 chars, lowercase alphanumeric
                + hyphens).
              </DialogDescription>
            </DialogHeader>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                publishMutation.mutate(slug.trim());
              }}
              className="space-y-4"
            >
              <div className="space-y-2">
                <Label htmlFor={`slug-${workflowId}`}>External slug</Label>
                <Input
                  id={`slug-${workflowId}`}
                  value={slug}
                  onChange={(e) => setSlug(e.target.value)}
                  placeholder="e.g. customer-summarizer"
                  required
                  pattern="^[a-z0-9][a-z0-9-]{1,63}$"
                />
                <p className="text-xs text-muted-foreground">
                  Becomes the URL path at <code>/api/run/&lt;slug&gt;</code>.
                </p>
              </div>
              <DialogFooter>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => setDialogOpen(false)}
                  disabled={publishMutation.isPending}
                >
                  Cancel
                </Button>
                <Button
                  type="submit"
                  disabled={publishMutation.isPending || !slug.trim()}
                >
                  {publishMutation.isPending ? "Publishing…" : "Publish"}
                </Button>
              </DialogFooter>
            </form>
          </DialogContent>
        )}
      </Dialog>
    </>
  );
}
