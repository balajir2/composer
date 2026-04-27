"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { MoreHorizontal, Copy, Download, FileText, Trash2 } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import { getWorkflow, createWorkflow, deleteWorkflow } from "@/lib/api/workflows";
import {
  downloadString,
  exportWorkflowAsJson,
  exportWorkflowAsMarkdown,
} from "@/lib/workflow-import-export";
import { toast } from "sonner";

type Workflow = {
  id: string;
  name: string;
  description?: string | null;
  isPublic: boolean;
  isProduction: boolean;
};

export function DesignerWorkflowCard({ wf }: { wf: Workflow }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [deleteOpen, setDeleteOpen] = useState(false);

  const duplicateMutation = useMutation({
    mutationFn: async () => {
      const fetched = await getWorkflow(wf.id);
      return createWorkflow({
        name: fetched.name + " (copy)",
        description: fetched.description ?? null,
        category: fetched.category ?? null,
        tags: fetched.tags ?? [],
        difficulty: fetched.difficulty ?? null,
        estimatedTime: fetched.estimatedTime ?? null,
        nodes: (fetched.nodes ?? []) as Parameters<typeof createWorkflow>[0]["nodes"],
        edges: (fetched.edges ?? []) as Parameters<typeof createWorkflow>[0]["edges"],
        version: fetched.version ?? null,
        isTemplate: fetched.isTemplate,
        isPublic: fetched.isPublic,
        isProduction: fetched.isProduction ?? null,
        externalSlug: null,
      });
    },
    onSuccess: (created) => {
      toast.success("Workflow duplicated.");
      void queryClient.invalidateQueries({ queryKey: ["designer-workflows"] });
      router.push(`/designer/${created.id}`);
    },
    onError: (err) =>
      toast.error(err instanceof Error ? err.message : "Failed to duplicate workflow."),
  });

  // Slugify the workflow name for download filenames so files are
  // findable on disk and don't collide.
  const filenameSlug =
    wf.name
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "") || "workflow";

  const downloadJsonMutation = useMutation({
    mutationFn: async () => {
      // Fetch the full workflow (the card prop only carries display
      // fields).  Using getWorkflow keeps export consistent with the
      // server's canonical shape.
      const full = await getWorkflow(wf.id);
      const ts = new Date().toISOString().slice(0, 10);
      downloadString(
        `${filenameSlug}-${ts}.json`,
        "application/json",
        exportWorkflowAsJson(full)
      );
    },
    onError: (err) =>
      toast.error(err instanceof Error ? err.message : "Download failed."),
  });

  const downloadMarkdownMutation = useMutation({
    mutationFn: async () => {
      const full = await getWorkflow(wf.id);
      const ts = new Date().toISOString().slice(0, 10);
      downloadString(
        `${filenameSlug}-${ts}.md`,
        "text/markdown;charset=utf-8",
        exportWorkflowAsMarkdown(full)
      );
    },
    onError: (err) =>
      toast.error(err instanceof Error ? err.message : "Download failed."),
  });

  const deleteMutation = useMutation({
    mutationFn: () => deleteWorkflow(wf.id),
    onSuccess: () => {
      setDeleteOpen(false);
      toast.success("Workflow deleted.");
      void queryClient.invalidateQueries({ queryKey: ["designer-workflows"] });
    },
    onError: (err) =>
      toast.error(err instanceof Error ? err.message : "Failed to delete workflow."),
  });

  return (
    <>
      <Card className="transition-shadow hover:shadow-md">
        <CardHeader>
          <div className="flex items-start justify-between gap-2">
            <CardTitle className="text-base leading-snug">{wf.name}</CardTitle>
            <DropdownMenu>
              <DropdownMenuTrigger
                render={<Button variant="ghost" size="icon-sm" aria-label="Workflow options" />}
              >
                <MoreHorizontal />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem
                  onClick={() => duplicateMutation.mutate()}
                  disabled={duplicateMutation.isPending}
                >
                  <Copy className="mr-2" />
                  Duplicate
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem
                  onClick={() => downloadJsonMutation.mutate()}
                  disabled={downloadJsonMutation.isPending}
                >
                  <Download className="mr-2" />
                  Download JSON
                </DropdownMenuItem>
                <DropdownMenuItem
                  onClick={() => downloadMarkdownMutation.mutate()}
                  disabled={downloadMarkdownMutation.isPending}
                >
                  <FileText className="mr-2" />
                  Download Markdown
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem variant="destructive" onClick={() => setDeleteOpen(true)}>
                  <Trash2 className="mr-2" />
                  Delete
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
          <div className="flex flex-wrap gap-1">
            {wf.isPublic && <Badge variant="secondary">Public</Badge>}
            {wf.isProduction && <Badge variant="default">Production</Badge>}
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="line-clamp-2 min-h-[2.5em] text-sm text-muted-foreground">
            {wf.description ?? "No description."}
          </p>
          <Link
            href={`/designer/${wf.id}`}
            className={cn(buttonVariants({ size: "sm" }), "w-full")}
          >
            Edit
          </Link>
        </CardContent>
      </Card>

      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete workflow</DialogTitle>
            <DialogDescription>
              Are you sure you want to delete &quot;{wf.name}&quot;? This action cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setDeleteOpen(false)}
              disabled={deleteMutation.isPending}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => deleteMutation.mutate()}
              disabled={deleteMutation.isPending}
            >
              {deleteMutation.isPending ? "Deleting..." : "Delete"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
