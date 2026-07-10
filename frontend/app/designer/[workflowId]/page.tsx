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

// ---------------------------------------------------------------------------
// Mount-vs-edit content comparison
// ---------------------------------------------------------------------------
//
// WorkflowCanvas's onNodesChange/onEdgesChange-firing effects (its
// mount-driven useEffects with no cleanup, workflow-canvas.tsx ~380-386)
// can fire MORE THAN ONCE with the exact same just-loaded data before any
// real user edit ever happens:
//
//  - React 18 Strict Mode (the Next.js dev-mode default) double-invokes
//    BOTH the mount effect (setup -> cleanup(no-op) -> setup) AND the
//    surrounding function component's render body itself. The latter
//    means `toReactFlow(...)` below can run twice per commit, producing
//    two array instances that are deep-equal but NOT the same reference.
//  - ReactFlow itself measures each node's DOM size after first paint and
//    dispatches its own *internal* node-change (setting width/height/
//    positionAbsolute) before our effect even fires — so even a single,
//    non-Strict-Mode mount can hand back an array that is reference-
//    unequal to `initialNodes` despite carrying no meaningful edit.
//
// A previous fix (commit 85b75a4) tracked "have I been invoked before" via
// booleans, which breaks under Strict Mode's double mount-effect (the
// second invocation looks just like a "real" second call). Comparing
// content instead of invocation count sidesteps both problems above: we
// only care whether the semantically-meaningful fields (id/type/position/
// data for nodes; id/source/target/sourceHandle/label for edges) still
// match what was originally loaded, not whether the array happens to be
// the same object.
function nodesMatchInitial(current: RFNode[], initial: RFNode[] | null): boolean {
  if (initial === null) return true;
  if (current === initial) return true;
  if (current.length !== initial.length) return false;
  const initialById = new Map(initial.map((n) => [n.id, n]));
  return current.every((n) => {
    const orig = initialById.get(n.id);
    if (!orig) return false;
    return (
      n.type === orig.type &&
      n.position.x === orig.position.x &&
      n.position.y === orig.position.y &&
      JSON.stringify(n.data ?? {}) === JSON.stringify(orig.data ?? {})
    );
  });
}

function edgesMatchInitial(current: RFEdge[], initial: RFEdge[] | null): boolean {
  if (initial === null) return true;
  if (current === initial) return true;
  if (current.length !== initial.length) return false;
  const initialById = new Map(initial.map((e) => [e.id, e]));
  return current.every((e) => {
    const orig = initialById.get(e.id);
    if (!orig) return false;
    return (
      e.source === orig.source &&
      e.target === orig.target &&
      (e.sourceHandle ?? null) === (orig.sourceHandle ?? null) &&
      (e.label ?? null) === (orig.label ?? null)
    );
  });
}

export default function DesignerCanvasPage({ params }: PageProps) {
  // `key` forces a full remount (resetting every ref/state below) whenever
  // workflowId changes. Next.js's App Router does NOT guarantee an unmount
  // when navigating client-side between two instances of the same dynamic
  // route (e.g. /designer/A -> /designer/B) — the page component can be
  // reconciled as a props update instead of a fresh mount. Without this key,
  // stale refs (nodesRef, initialNodesRef, etc.) from workflow A would leak
  // into workflow B: the "seed refs" logic below only seeds when the ref is
  // still empty/null, so it'd silently keep showing A's canvas data, and
  // the mount-vs-edit dirty tracking below would never re-arm for B's
  // initial load.
  return <DesignerCanvasPageInner key={params.workflowId} workflowId={params.workflowId} />;
}

function DesignerCanvasPageInner({ workflowId }: { workflowId: string }) {
  const queryClient = useQueryClient();

  // Track current canvas state via refs so Save can read the latest without
  // requiring a re-render on every node/edge change event.
  const nodesRef = useRef<RFNode[]>([]);
  const edgesRef = useRef<RFEdge[]>([]);

  // The as-loaded nodes/edges, captured once per mount (see the seed
  // logic below, right after `toReactFlow` runs). onNodesChange/
  // onEdgesChange compare their incoming array's CONTENT against these
  // rather than counting invocations — see the comment above
  // nodesMatchInitial/edgesMatchInitial for why content comparison is
  // required (Strict Mode's double-invoke of both the mount effect and
  // the render body, plus ReactFlow's own post-mount dimension sync,
  // both defeat a simple "first call vs. later calls" counter).
  const initialNodesRef = useRef<RFNode[] | null>(null);
  const initialEdgesRef = useRef<RFEdge[] | null>(null);

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

  // Capture the as-loaded baseline exactly once per mount (unlike the
  // seed above, this deliberately does NOT gate on length>0 — an empty
  // brand-new workflow's `[]` is a valid, meaningful baseline to compare
  // future callbacks against).
  if (initialNodesRef.current === null) {
    initialNodesRef.current = rfNodes;
  }
  if (initialEdgesRef.current === null) {
    initialEdgesRef.current = rfEdges;
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
              if (!nodesMatchInitial(nodes, initialNodesRef.current)) {
                autosave.markDirty();
              }
            }}
            onEdgesChange={(edges) => {
              edgesRef.current = edges;
              if (!edgesMatchInitial(edges, initialEdgesRef.current)) {
                autosave.markDirty();
              }
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
