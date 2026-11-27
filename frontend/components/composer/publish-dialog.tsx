"use client";

import { useState, useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
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
import { getWorkflow, updateWorkflow } from "@/lib/api/workflows";
import { slugify } from "@/lib/slugify";
import { ComposerApiError, apiErrorText } from "@/lib/api/client";
import { toast } from "sonner";
import type { components } from "@/lib/api/generated/schema";

type WorkflowRead = components["schemas"]["WorkflowRead"];

const apiBase = process.env.NEXT_PUBLIC_COMPOSER_API_URL ?? "http://localhost:8000";

interface PublishDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  workflow: WorkflowRead;
}

export function PublishDialog({ open, onOpenChange, workflow }: PublishDialogProps) {
  const queryClient = useQueryClient();
  const [slug, setSlug] = useState(() => slugify(workflow.name));
  const [slugError, setSlugError] = useState<string | null>(null);
  const [publishError, setPublishError] = useState<string | null>(null);
  const [isPending, setIsPending] = useState(false);

  // Re-sync slug when dialog opens or workflow name changes.
  useEffect(() => {
    if (open) {
      setSlug(slugify(workflow.name));
      setSlugError(null);
      setPublishError(null);
    }
  }, [open, workflow.name]);

  const previewUrl = `${apiBase}/api/run/${slug || "<slug>"}`;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSlugError(null);
    setPublishError(null);
    setIsPending(true);
    try {
      // Re-fetch immediately before submitting rather than trusting the
      // `workflow` prop's node/edge snapshot. PUT /workflows/{id} is a
      // full replace, so publishing with a stale prop (e.g. the Settings
      // page's query cache lagging a save made moments ago in the
      // canvas, or in another tab) would silently overwrite newer nodes/
      // edges with older ones. Fetching fresh here closes that window.
      const fresh = await getWorkflow(workflow.id);
      await updateWorkflow(workflow.id, {
        name: fresh.name,
        description: fresh.description ?? null,
        category: fresh.category ?? null,
        tags: fresh.tags ?? [],
        difficulty: fresh.difficulty ?? null,
        estimatedTime: fresh.estimatedTime ?? null,
        nodes: fresh.nodes as Parameters<typeof updateWorkflow>[1]["nodes"],
        edges: fresh.edges as Parameters<typeof updateWorkflow>[1]["edges"],
        version: fresh.version ?? null,
        isTemplate: fresh.isTemplate,
        isPublic: fresh.isPublic,
        isProduction: true,
        externalSlug: slug,
      });
      toast.success("Workflow published.");
      void queryClient.invalidateQueries({ queryKey: ["workflow", workflow.id] });
      // Also bust the end-user Run list cache so the newly-published
      // workflow shows up immediately instead of waiting out staleTime.
      void queryClient.invalidateQueries({ queryKey: ["runnable-workflows"] });
      onOpenChange(false);
    } catch (err) {
      if (err instanceof ComposerApiError && err.status === 409) {
        setSlugError("This slug is already in use. Choose a different one.");
      } else {
        // Also kept inline: the toast fades, and a refusal (e.g. a required branch that is
        // not connected) names exactly what to fix on the canvas.
        const text = apiErrorText(err, "Failed to publish workflow.");
        setPublishError(text);
        toast.error(text);
      }
    } finally {
      setIsPending(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Publish workflow</DialogTitle>
          <DialogDescription>
            Publishing makes this workflow accessible via an external URL. Choose a URL-safe slug.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="publish-slug">External slug</Label>
            <Input
              id="publish-slug"
              value={slug}
              onChange={(e) => {
                setSlug(e.target.value);
                setSlugError(null);
              }}
              placeholder="my-workflow"
              aria-invalid={slugError !== null}
              autoFocus
            />
            {slugError !== null && (
              <p className="text-sm font-medium text-destructive">{slugError}</p>
            )}
            {publishError !== null && (
              <p role="alert" data-testid="publish-refused" className="text-sm text-destructive">
                {publishError}
              </p>
            )}
          </div>
          <div className="space-y-1">
            <p className="text-xs text-muted-foreground">External URL preview:</p>
            <p className="break-all rounded-md bg-muted px-3 py-2 font-mono text-xs text-foreground">
              {previewUrl}
            </p>
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
              disabled={isPending}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={isPending || !slug.trim()}>
              {isPending ? "Publishing…" : "Publish"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
