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
import { updateWorkflow } from "@/lib/api/workflows";
import { slugify } from "@/lib/slugify";
import { ComposerApiError } from "@/lib/api/client";
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
  const [isPending, setIsPending] = useState(false);

  // Re-sync slug when dialog opens or workflow name changes.
  useEffect(() => {
    if (open) {
      setSlug(slugify(workflow.name));
      setSlugError(null);
    }
  }, [open, workflow.name]);

  const previewUrl = `${apiBase}/api/run/${slug || "<slug>"}`;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSlugError(null);
    setIsPending(true);
    try {
      await updateWorkflow(workflow.id, {
        name: workflow.name,
        description: workflow.description ?? null,
        category: workflow.category ?? null,
        tags: workflow.tags ?? [],
        difficulty: workflow.difficulty ?? null,
        estimatedTime: workflow.estimatedTime ?? null,
        nodes: workflow.nodes as Parameters<typeof updateWorkflow>[1]["nodes"],
        edges: workflow.edges as Parameters<typeof updateWorkflow>[1]["edges"],
        version: workflow.version ?? null,
        isTemplate: workflow.isTemplate,
        isPublic: workflow.isPublic,
        isProduction: true,
        externalSlug: slug,
      });
      toast.success("Workflow published.");
      void queryClient.invalidateQueries({ queryKey: ["workflow", workflow.id] });
      onOpenChange(false);
    } catch (err) {
      if (err instanceof ComposerApiError && err.status === 409) {
        setSlugError("This slug is already in use. Choose a different one.");
      } else {
        toast.error(err instanceof Error ? err.message : "Failed to publish workflow.");
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
              <p className="text-destructive text-sm font-medium">{slugError}</p>
            )}
          </div>
          <div className="space-y-1">
            <p className="text-muted-foreground text-xs">External URL preview:</p>
            <p className="bg-muted break-all rounded-md px-3 py-2 font-mono text-xs text-foreground">
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
