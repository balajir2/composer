import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect } from "react";
import type { Node as RFNode, Edge as RFEdge } from "reactflow";
import DesignerCanvasPage from "./page";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------
//
// This test targets DesignerCanvasPage's own logic (mount-vs-edit dirty
// tracking, remount-on-workflowId-change) — not WorkflowCanvas/SaveControls
// internals. Those are mocked with thin stand-ins so the test doesn't drag
// in ReactFlow, next-auth, sonner, etc.

const getWorkflow = vi.fn();
const updateWorkflow = vi.fn().mockResolvedValue({});

vi.mock("@/lib/api/workflows", () => ({
  getWorkflow: (id: string) => getWorkflow(id),
  updateWorkflow: (id: string, body: unknown) => updateWorkflow(id, body),
}));

vi.mock("@/components/composer/published-endpoint", () => ({
  PublishedEndpoint: () => null,
}));

vi.mock("@/components/composer/canvas/designer-execution-panel", () => ({
  DesignerExecutionPanel: () => null,
}));

vi.mock("@/components/composer/canvas/save-controls", () => ({
  SaveControls: ({ saveStatus }: { saveStatus: string }) => (
    <div data-testid="save-status">{saveStatus}</div>
  ),
}));

// Faithfully mirrors the real WorkflowCanvas's mount behavior (workflow-canvas.tsx
// lines 380-386): onNodesChange/onEdgesChange fire once on mount with the
// just-loaded initial nodes/edges via a plain useEffect, then again whenever
// `nodes`/`edges` state changes (simulated here via the "edit" buttons).
vi.mock("@/components/composer/canvas/workflow-canvas", () => ({
  WorkflowCanvas: ({
    initialNodes,
    initialEdges,
    onNodesChange,
    onEdgesChange,
  }: {
    initialNodes: RFNode[];
    initialEdges: RFEdge[];
    onNodesChange?: (nodes: RFNode[]) => void;
    onEdgesChange?: (edges: RFEdge[]) => void;
  }) => {
    useEffect(() => {
      onNodesChange?.(initialNodes);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [initialNodes]);
    useEffect(() => {
      onEdgesChange?.(initialEdges);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [initialEdges]);
    return (
      <div>
        <button
          onClick={() =>
            onNodesChange?.([...initialNodes, { id: "new", type: "agent", position: { x: 0, y: 0 }, data: {} }])
          }
        >
          edit-nodes
        </button>
        <button
          onClick={() =>
            onEdgesChange?.([...initialEdges, { id: "new-edge", source: "a", target: "b" }])
          }
        >
          edit-edges
        </button>
      </div>
    );
  },
}));

function workflowFixture(id: string) {
  return {
    id,
    name: `Workflow ${id}`,
    description: null,
    category: null,
    tags: [],
    difficulty: null,
    estimatedTime: null,
    nodes: [
      { id: "start-1", type: "start", position: { x: 0, y: 0 }, data: {} },
    ],
    edges: [],
    version: 1,
    isTemplate: false,
    isPublic: false,
    isProduction: false,
    externalSlug: null,
    createdAt: null,
    updatedAt: null,
  };
}

function renderPage(workflowId: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <DesignerCanvasPage params={{ workflowId }} />
    </QueryClientProvider>
  );
}

describe("DesignerCanvasPage autosave dirty-tracking", () => {
  it("does not mark dirty from the initial mount-driven onNodesChange/onEdgesChange", async () => {
    getWorkflow.mockResolvedValueOnce(workflowFixture("wf-a"));
    renderPage("wf-a");

    await screen.findByTestId("save-status");
    // The mocked WorkflowCanvas fires onNodesChange/onEdgesChange on mount,
    // exactly like the real one. Status must stay idle, not flip to dirty.
    expect(screen.getByTestId("save-status").textContent).toBe("idle");
  });

  it("marks dirty on a real edit after mount", async () => {
    getWorkflow.mockResolvedValueOnce(workflowFixture("wf-a"));
    renderPage("wf-a");

    await screen.findByTestId("save-status");
    expect(screen.getByTestId("save-status").textContent).toBe("idle");

    fireEvent.click(screen.getByText("edit-nodes"));

    await waitFor(() =>
      expect(screen.getByTestId("save-status").textContent).toBe("dirty")
    );
  });

  it("re-arms mount-vs-edit tracking when navigating to a different workflowId", async () => {
    getWorkflow.mockResolvedValueOnce(workflowFixture("wf-a"));
    const { rerender } = renderPage("wf-a");
    await screen.findByTestId("save-status");
    expect(screen.getByTestId("save-status").textContent).toBe("idle");

    // Simulate a client-side navigation to a different workflow. This
    // rerenders the same React tree with new params — precisely the
    // scenario where Next.js's App Router might reconcile the page as a
    // props update rather than an unmount/remount. The `key={workflowId}`
    // on the inner component is what forces React to treat this as a
    // fresh mount, resetting nodesInitializedRef/edgesInitializedRef (and
    // nodesRef/edgesRef) instead of leaking wf-a's state into wf-b.
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    getWorkflow.mockResolvedValueOnce(workflowFixture("wf-b"));
    rerender(
      <QueryClientProvider client={qc}>
        <DesignerCanvasPage params={{ workflowId: "wf-b" }} />
      </QueryClientProvider>
    );

    await waitFor(() => expect(getWorkflow).toHaveBeenCalledWith("wf-b"));
    await waitFor(() =>
      expect(screen.getByTestId("save-status").textContent).toBe("idle")
    );
  });
});
