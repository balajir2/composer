"use client";

import { useEffect, useRef, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { ArrowLeft, Download, FileText, Settings } from "lucide-react";
import { Button, buttonVariants } from "@/components/ui/button";
import { getWorkflow, updateWorkflow } from "@/lib/api/workflows";
import {
  downloadString,
  exportWorkflowAsJson,
  exportWorkflowAsMarkdown,
} from "@/lib/workflow-import-export";
import { WorkflowCanvas } from "@/components/composer/canvas/workflow-canvas";
import { SaveControls } from "@/components/composer/canvas/save-controls";
import { PublishedEndpoint } from "@/components/composer/published-endpoint";
import {
  DesignerExecutionPanel,
  type DesignerExecutionState,
} from "@/components/composer/canvas/designer-execution-panel";
import { toReactFlow, fromReactFlow } from "@/lib/workflow-to-rf";
import type { ComposerNode, ComposerEdge } from "@/lib/workflow-to-rf";
import type { Node as RFNode, Edge as RFEdge } from "reactflow";
import { useAutosave } from "@/lib/use-autosave";

interface PageProps {
  params: { workflowId: string };
}

export default function DesignerCanvasPage({ params }: PageProps) {
  const { workflowId } = params;
  const queryClient = useQueryClient();

  // Track current canvas state via refs so Save can read the latest without
  // requiring a re-render on every node/edge change event.
  const nodesRef = useRef<RFNode[]>([]);
  const edgesRef = useRef<RFEdge[]>([]);

  const [isSaving, setIsSaving] = useState(false);
  // The draft-run currently in flight (if any).  Set when Run Draft
  // successfully starts an execution; cleared when the user closes the
  // execution panel.  The run stays in-canvas — we do NOT navigate away.
  const [draftExecutionId, setDraftExecutionId] = useState<string | null>(null);
  // Live node-run state, pushed up from DesignerExecutionPanel so the canvas
  // can decorate nodes (running / completed / failed).
  const [runState, setRunState] = useState<DesignerExecutionState | null>(null);

  const {
    data: workflow,
    isLoading,
    isError,
  } = useQuery({
    queryKey: ["workflow", workflowId],
    queryFn: () => getWorkflow(workflowId),
  });

  const saveMutation = useMutation({
    mutationFn: async () => {
      if (!workflow) throw new Error("Workflow not loaded.");
      const { nodes, edges } = fromReactFlow(nodesRef.current, edgesRef.current);
      return updateWorkflow(workflowId, {
        name: workflow.name,
        description: workflow.description ?? null,
        category: workflow.category ?? null,
        tags: workflow.tags ?? [],
        difficulty: workflow.difficulty ?? null,
        estimatedTime: workflow.estimatedTime ?? null,
        nodes: nodes as Parameters<typeof updateWorkflow>[1]["nodes"],
        edges: edges as Parameters<typeof updateWorkflow>[1]["edges"],
        version: workflow.version ?? null,
        isTemplate: workflow.isTemplate,
        isPublic: workflow.isPublic,
        isProduction: workflow.isProduction ?? null,
        externalSlug: workflow.externalSlug ?? null,
      });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["workflow", workflowId] });
    },
  });

  const autosave = useAutosave(async () => {
    await saveMutation.mutateAsync();
  }, 3000);

  async function handleSave() {
    setIsSaving(true);
    try {
      await autosave.saveNow();
    } finally {
      setIsSaving(false);
    }
  }

  useEffect(() => {
    function handleBeforeUnload(e: BeforeUnloadEvent) {
      if (autosave.status === "dirty" || autosave.status === "saving") {
        e.preventDefault();
        e.returnValue = "";
      }
    }
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [autosave.status]);

  if (isLoading) {
    return (
      <div className="flex h-[calc(100vh-8rem)] items-center justify-center text-sm text-muted-foreground">
        Loading workflow…
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

  const { rfNodes, rfEdges } = toReactFlow(
    (workflow.nodes as unknown as ComposerNode[]) ?? [],
    (workflow.edges as unknown as ComposerEdge[]) ?? []
  );

  // Seed refs with the loaded data so an immediate Save works correctly.
  if (nodesRef.current.length === 0 && rfNodes.length > 0) {
    nodesRef.current = rfNodes;
  }
  if (edgesRef.current.length === 0 && rfEdges.length > 0) {
    edgesRef.current = rfEdges;
  }

  return (
    <div className="flex h-[calc(100vh-8rem)] flex-col">
      {/* Slim canvas top bar */}
      <div className="flex items-center justify-between border-b bg-background px-4 py-2">
        <div className="flex items-center gap-3">
          <Link
            href="/designer"
            className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="h-4 w-4" />
            Workflows
          </Link>
          <span className="text-muted-foreground">/</span>
          <span className="text-sm font-medium">{workflow.name}</span>
          <PublishedEndpoint
            isProduction={Boolean(workflow.isProduction)}
            externalSlug={workflow.externalSlug}
            variant="inline"
          />
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              const slug =
                workflow.name
                  .toLowerCase()
                  .replace(/[^a-z0-9]+/g, "-")
                  .replace(/^-+|-+$/g, "") || "workflow";
              const ts = new Date().toISOString().slice(0, 10);
              downloadString(
                `${slug}-${ts}.json`,
                "application/json",
                exportWorkflowAsJson(workflow)
              );
            }}
            title="Download this workflow as JSON"
          >
            <Download className="mr-1.5 h-3.5 w-3.5" />
            JSON
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              const slug =
                workflow.name
                  .toLowerCase()
                  .replace(/[^a-z0-9]+/g, "-")
                  .replace(/^-+|-+$/g, "") || "workflow";
              const ts = new Date().toISOString().slice(0, 10);
              downloadString(
                `${slug}-${ts}.md`,
                "text/markdown;charset=utf-8",
                exportWorkflowAsMarkdown(workflow)
              );
            }}
            title="Download this workflow as Markdown (with embedded JSON for re-import)"
          >
            <FileText className="mr-1.5 h-3.5 w-3.5" />
            Markdown
          </Button>
          <Link
            href={`/designer/${workflowId}/settings`}
            className={buttonVariants({ variant: "outline", size: "sm" })}
          >
            <Settings className="mr-1.5 h-3.5 w-3.5" />
            Settings
          </Link>
          <SaveControls
            workflowId={workflowId}
            isSaving={isSaving}
            saveStatus={autosave.status}
            onSave={handleSave}
            getCurrentNodes={() =>
              nodesRef.current.map((n) => ({
                type: String(n.type ?? ""),
                data: (n.data as Record<string, unknown>) ?? {},
              }))
            }
            onExecutionStarted={(executionId) => {
              setDraftExecutionId(executionId);
              setRunState(null);
            }}
          />
        </div>
      </div>

      {/* Canvas + execution panel */}
      <div className="flex flex-1 overflow-hidden">
        <div className="flex-1 overflow-hidden">
          <WorkflowCanvas
            initialNodes={rfNodes}
            initialEdges={rfEdges}
            onNodesChange={(nodes) => {
              nodesRef.current = nodes;
              autosave.markDirty();
            }}
            onEdgesChange={(edges) => {
              edgesRef.current = edges;
              autosave.markDirty();
            }}
            runState={runState ?? undefined}
          />
        </div>
        {draftExecutionId && (
          <DesignerExecutionPanel
            executionId={draftExecutionId}
            onClose={() => {
              setDraftExecutionId(null);
              setRunState(null);
            }}
            onRunStateChange={setRunState}
          />
        )}
      </div>
    </div>
  );
}
