"use client";

import { useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { getWorkflow, updateWorkflow } from "@/lib/api/workflows";
import { ManageAssigneesDialog } from "@/components/composer/manage-assignees-dialog";
import { PublishDialog } from "@/components/composer/publish-dialog";
import { PublishedEndpoint } from "@/components/composer/published-endpoint";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "sonner";

interface PageProps {
  params: { workflowId: string };
}

export default function WorkflowSettingsPage({ params }: PageProps) {
  const { workflowId } = params;
  const queryClient = useQueryClient();

  const {
    data: workflow,
    isLoading,
    isError,
  } = useQuery({
    queryKey: ["workflow", workflowId],
    queryFn: () => getWorkflow(workflowId),
  });

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [category, setCategory] = useState("");
  const [tagsInput, setTagsInput] = useState("");
  const [isPublic, setIsPublic] = useState(false);
  const [isTemplate, setIsTemplate] = useState(false);
  const [publishOpen, setPublishOpen] = useState(false);

  // Seed form fields when workflow data loads.
  useEffect(() => {
    if (workflow) {
      setName(workflow.name);
      setDescription(workflow.description ?? "");
      setCategory(workflow.category ?? "");
      setTagsInput((workflow.tags ?? []).join(", "));
      setIsPublic(workflow.isPublic);
      setIsTemplate(workflow.isTemplate);
    }
  }, [workflow]);

  const saveMutation = useMutation({
    mutationFn: async () => {
      if (!workflow) throw new Error("Workflow not loaded.");
      const tags = tagsInput
        .split(",")
        .map((t) => t.trim())
        .filter(Boolean);
      // Preserve existing nodes + edges — only update metadata fields.
      return updateWorkflow(workflowId, {
        name: name.trim() || workflow.name,
        description: description.trim() || null,
        category: category.trim() || null,
        tags,
        difficulty: workflow.difficulty ?? null,
        estimatedTime: workflow.estimatedTime ?? null,
        nodes: workflow.nodes as Parameters<typeof updateWorkflow>[1]["nodes"],
        edges: workflow.edges as Parameters<typeof updateWorkflow>[1]["edges"],
        version: workflow.version ?? null,
        isTemplate,
        isPublic,
        isProduction: workflow.isProduction ?? false,
        externalSlug: workflow.externalSlug ?? null,
      });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["workflow", workflowId] });
      // Templates list pulled by /designer/templates is keyed separately,
      // so make sure flipping the toggle takes effect there too.
      void queryClient.invalidateQueries({ queryKey: ["designer-templates"] });
      toast.success("Settings saved.");
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "Failed to save settings."),
  });

  const unpublishMutation = useMutation({
    mutationFn: async () => {
      if (!workflow) throw new Error("Workflow not loaded.");
      return updateWorkflow(workflowId, {
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
        isProduction: false,
        externalSlug: workflow.externalSlug ?? null,
      });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["workflow", workflowId] });
      toast.success("Workflow unpublished.");
    },
    onError: (err) =>
      toast.error(err instanceof Error ? err.message : "Failed to unpublish workflow."),
  });

  if (isLoading) {
    return (
      <div className="mx-auto max-w-2xl space-y-6 px-4 py-8">
        <Skeleton className="h-6 w-48" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }

  if (isError || !workflow) {
    return (
      <div className="flex h-[calc(100vh-8rem)] items-center justify-center text-sm text-destructive">
        Failed to load workflow.{" "}
        <Link href="/designer" className="ml-2 underline">
          Go back
        </Link>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-2xl space-y-8 px-4 py-8">
      {/* Back link */}
      <div className="flex items-center gap-3">
        <Link
          href={`/designer/${workflowId}`}
          className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" />
          Back to canvas
        </Link>
        <span className="text-muted-foreground">/</span>
        <span className="text-sm font-medium">Settings</span>
      </div>

      <h1 className="text-xl font-semibold">Workflow settings</h1>

      {/* Metadata form */}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          saveMutation.mutate();
        }}
        className="space-y-5"
      >
        <div className="space-y-1.5">
          <Label htmlFor="settings-name">Name</Label>
          <Input
            id="settings-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Workflow name"
          />
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="settings-description">
            Description <span className="font-normal text-muted-foreground">(optional)</span>
          </Label>
          <Textarea
            id="settings-description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="What does this workflow do?"
            rows={3}
          />
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="settings-category">
            Category <span className="font-normal text-muted-foreground">(optional)</span>
          </Label>
          <Input
            id="settings-category"
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            placeholder="e.g. automation, analytics"
          />
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="settings-tags">
            Tags{" "}
            <span className="font-normal text-muted-foreground">(comma-separated, optional)</span>
          </Label>
          <Input
            id="settings-tags"
            value={tagsInput}
            onChange={(e) => setTagsInput(e.target.value)}
            placeholder="e.g. sales, crm, email"
          />
        </div>

        {/* isPublic toggle */}
        <div className="flex items-center justify-between rounded-lg border px-4 py-3">
          <div>
            <p className="text-sm font-medium">Public workflow</p>
            <p className="text-xs text-muted-foreground">
              Allow anyone to discover and view this workflow.
            </p>
          </div>
          <Button
            type="button"
            variant={isPublic ? "default" : "outline"}
            size="sm"
            onClick={() => setIsPublic((prev) => !prev)}
          >
            {isPublic ? "Public" : "Private"}
          </Button>
        </div>

        {/* isTemplate toggle — surfaces this workflow on the Templates
            gallery page where any designer can clone it as a starting
            point.  Independent of the Public toggle: a workflow can be
            a private template (visible to admins only) or a public
            non-template (read-only reference).  Most templates ship as
            both isPublic + isTemplate. */}
        <div className="flex items-center justify-between rounded-lg border px-4 py-3">
          <div>
            <p className="text-sm font-medium">Mark as template</p>
            <p className="text-xs text-muted-foreground">
              Show this workflow on the Templates page so other designers
              can clone it as a reference implementation.
            </p>
          </div>
          <Button
            type="button"
            variant={isTemplate ? "default" : "outline"}
            size="sm"
            onClick={() => setIsTemplate((prev) => !prev)}
          >
            {isTemplate ? "Template" : "Not a template"}
          </Button>
        </div>

        {/* Sharing — grants full read/write access to other users without
            changing ownership.  All-or-nothing: no view-vs-edit split yet. */}
        <div className="flex items-center justify-between rounded-lg border px-4 py-3">
          <div>
            <p className="text-sm font-medium">Shared access</p>
            <p className="text-xs text-muted-foreground">
              Give other users full access to view and edit this workflow.
            </p>
          </div>
          <ManageAssigneesDialog workflowId={workflowId} workflowName={workflow.name} />
        </div>

        <Button type="submit" disabled={saveMutation.isPending}>
          {saveMutation.isPending ? "Saving…" : "Save settings"}
        </Button>
      </form>

      {/* Publish state section */}
      <div className="space-y-3 border-t pt-6">
        <h2 className="text-base font-medium">Publish state</h2>
        {workflow.isProduction ? (
          <div className="space-y-3">
            <PublishedEndpoint
              isProduction={Boolean(workflow.isProduction)}
              externalSlug={workflow.externalSlug}
              variant="block"
            />
            <div className="flex justify-end">
              <Button
                variant="destructive"
                size="sm"
                onClick={() => unpublishMutation.mutate()}
                disabled={unpublishMutation.isPending}
              >
                {unpublishMutation.isPending ? "Unpublishing…" : "Unpublish"}
              </Button>
            </div>
          </div>
        ) : (
          <div className="flex items-center justify-between rounded-lg border px-4 py-3">
            <div>
              <p className="text-sm font-medium">Not published</p>
              <p className="text-xs text-muted-foreground">
                Publish to expose this workflow via an external API endpoint.
              </p>
            </div>
            <Button size="sm" onClick={() => setPublishOpen(true)}>
              Publish
            </Button>
          </div>
        )}
      </div>

      {/* Publish dialog — controlled open state, no DialogTrigger asChild */}
      <PublishDialog open={publishOpen} onOpenChange={setPublishOpen} workflow={workflow} />
    </div>
  );
}
