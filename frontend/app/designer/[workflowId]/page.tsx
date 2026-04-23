"use client";

import { useRef, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { ArrowLeft, Settings } from "lucide-react";
import { buttonVariants } from "@/components/ui/button";
import { getWorkflow, updateWorkflow } from "@/lib/api/workflows";
import { WorkflowCanvas } from "@/components/composer/canvas/workflow-canvas";
import { SaveControls } from "@/components/composer/canvas/save-controls";
import { toReactFlow, fromReactFlow } from "@/lib/workflow-to-rf";
import type { ComposerNode, ComposerEdge } from "@/lib/workflow-to-rf";
import type { Node as RFNode, Edge as RFEdge } from "reactflow";

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

  async function handleSave() {
    setIsSaving(true);
    try {
      await saveMutation.mutateAsync();
    } finally {
      setIsSaving(false);
    }
  }

  if (isLoading) {
    return (
      <div className="text-muted-foreground flex h-[calc(100vh-8rem)] items-center justify-center text-sm">
        Loading workflow…
      </div>
    );
  }

  if (isError || !workflow) {
    return (
      <div className="text-destructive flex h-[calc(100vh-8rem)] items-center justify-center text-sm">
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
            className="text-muted-foreground flex items-center gap-1 text-sm hover:text-foreground"
          >
            <ArrowLeft className="h-4 w-4" />
            Workflows
          </Link>
          <span className="text-muted-foreground">/</span>
          <span className="text-sm font-medium">{workflow.name}</span>
        </div>
        <div className="flex items-center gap-2">
          <Link
            href={`/designer/${workflowId}/settings`}
            className={buttonVariants({ variant: "outline", size: "sm" })}
          >
            <Settings className="mr-1.5 h-3.5 w-3.5" />
            Settings
          </Link>
          <SaveControls workflowId={workflowId} isSaving={isSaving} onSave={handleSave} />
        </div>
      </div>

      {/* Canvas area fills remaining viewport */}
      <div className="flex-1 overflow-hidden">
        <WorkflowCanvas
          initialNodes={rfNodes}
          initialEdges={rfEdges}
          onNodesChange={(nodes) => {
            nodesRef.current = nodes;
          }}
          onEdgesChange={(edges) => {
            edgesRef.current = edges;
          }}
        />
      </div>
    </div>
  );
}
