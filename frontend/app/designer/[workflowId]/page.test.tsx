import { beforeEach, describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect } from "react";
import type { Node as RFNode, Edge as RFEdge } from "reactflow";
import DesignerCanvasPage from "./page";

// The page reads its route param via next/navigation's useParams(); the test
// drives it through this mutable holder instead of a `params` prop.
const routeParams = vi.hoisted(() => ({ current: { workflowId: "" } }));
vi.mock("next/navigation", () => ({
  useParams: () => routeParams.current,
}));

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
  SaveControls: ({ saveStatus, onSave }: { saveStatus: string; onSave: () => Promise<void> }) => (
    <div>
      <div data-testid="save-status">{saveStatus}</div>
      <button onClick={() => onSave().catch(() => undefined)}>save-now</button>
    </div>
  ),
}));

// Deep-clones nodes/edges into FRESH array/object instances with identical
// content. Stands in for what a second `toReactFlow(...)` call (React 18
// Strict Mode double-invoking the page's render body) or ReactFlow's own
// post-mount dimension sync would hand back: same data, different
// reference.
function cloneNodes(nodes: RFNode[]): RFNode[] {
  return nodes.map((n) => ({
    ...n,
    position: { ...n.position },
    data: { ...(n.data as Record<string, unknown>) },
  }));
}
function cloneEdges(edges: RFEdge[]): RFEdge[] {
  return edges.map((e) => ({ ...e }));
}

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
            onNodesChange?.([
              ...initialNodes,
              { id: "new", type: "agent", position: { x: 0, y: 0 }, data: {} },
            ])
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
        <button
          onClick={() => {
            // Simulate React 18 Strict Mode double-invoking the mount
            // effect: the SAME logical content handed back twice, each
            // time as brand-new array/object instances (never `===` the
            // original `initialNodes`/`initialEdges`, and not `===` each
            // other either).
            onNodesChange?.(cloneNodes(initialNodes));
            onNodesChange?.(cloneNodes(initialNodes));
            onEdgesChange?.(cloneEdges(initialEdges));
            onEdgesChange?.(cloneEdges(initialEdges));
          }}
        >
          simulate-strict-mode-remount
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
    nodes: [{ id: "start-1", type: "start", position: { x: 0, y: 0 }, data: {} }],
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
  routeParams.current = { workflowId };
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <DesignerCanvasPage />
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

  it("does not mark dirty when the mount effect fires twice with same-content-but-different-reference data (React Strict Mode)", async () => {
    getWorkflow.mockResolvedValueOnce(workflowFixture("wf-a"));
    renderPage("wf-a");

    await screen.findByTestId("save-status");
    expect(screen.getByTestId("save-status").textContent).toBe("idle");

    // Two calls, each with a freshly-cloned (never `===` the original,
    // never `===` each other) but content-identical array — exactly what
    // Strict Mode's setup -> cleanup(no-op) -> setup double-invoke of the
    // mount effect produces once the page's render body (and therefore
    // `toReactFlow`) has also run twice. A reference-equality or
    // invocation-count based gate would incorrectly call markDirty() on
    // the second invocation; content comparison must not.
    fireEvent.click(screen.getByText("simulate-strict-mode-remount"));
    expect(screen.getByTestId("save-status").textContent).toBe("idle");

    // A genuinely different edit afterward must still flip it dirty.
    fireEvent.click(screen.getByText("edit-nodes"));
    await waitFor(() => expect(screen.getByTestId("save-status").textContent).toBe("dirty"));
  });

  it("marks dirty on a real edit after mount", async () => {
    getWorkflow.mockResolvedValueOnce(workflowFixture("wf-a"));
    renderPage("wf-a");

    await screen.findByTestId("save-status");
    expect(screen.getByTestId("save-status").textContent).toBe("idle");

    fireEvent.click(screen.getByText("edit-nodes"));

    await waitFor(() => expect(screen.getByTestId("save-status").textContent).toBe("dirty"));
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
    // fresh mount, resetting initialNodesRef/initialEdgesRef (and
    // nodesRef/edgesRef) instead of leaking wf-a's state into wf-b.
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    getWorkflow.mockResolvedValueOnce(workflowFixture("wf-b"));
    routeParams.current = { workflowId: "wf-b" };
    rerender(
      <QueryClientProvider client={qc}>
        <DesignerCanvasPage />
      </QueryClientProvider>
    );

    await waitFor(() => expect(getWorkflow).toHaveBeenCalledWith("wf-b"));
    await waitFor(() => expect(screen.getByTestId("save-status").textContent).toBe("idle"));
  });
});

// ---------------------------------------------------------------------------
// Optimistic locking: never overwrite someone else's save unknowingly
// ---------------------------------------------------------------------------

const SEEN = "2026-11-24T10:00:00.123000Z";
const AFTER_SAVE = "2026-11-24T10:01:00.456000Z";
const THEIRS = "2026-11-24T10:05:00.500000Z";

function changedError() {
  return {
    status: 409,
    detail: {
      detail: {
        code: "workflow_changed",
        message: "workflow changed since you loaded it",
        currentUpdatedAt: THEIRS,
      },
    },
  };
}

function bodyOfCall(n: number): Record<string, unknown> {
  return updateWorkflow.mock.calls[n]?.[1] as Record<string, unknown>;
}

describe("DesignerCanvasPage optimistic locking", () => {
  beforeEach(() => {
    getWorkflow.mockReset();
    updateWorkflow.mockReset();
    // Background refetches (after a save) keep answering with the originally loaded copy.
    getWorkflow.mockResolvedValue({ ...workflowFixture("wf-a"), updatedAt: SEEN });
  });

  it("sends the last-seen updatedAt, then the one each save returned", async () => {
    updateWorkflow.mockResolvedValue({ updatedAt: AFTER_SAVE });
    renderPage("wf-a");
    await screen.findByTestId("save-status");

    fireEvent.click(screen.getByText("save-now"));
    await waitFor(() => expect(updateWorkflow).toHaveBeenCalledTimes(1));
    expect(bodyOfCall(0).expectedUpdatedAt).toBe(SEEN);

    await waitFor(() => expect(screen.getByTestId("save-status").textContent).toBe("saved"));
    fireEvent.click(screen.getByText("save-now"));
    await waitFor(() => expect(updateWorkflow).toHaveBeenCalledTimes(2));
    expect(bodyOfCall(1).expectedUpdatedAt).toBe(AFTER_SAVE);
  });

  it("opens the dialog on a 409 instead of silently failing", async () => {
    updateWorkflow.mockRejectedValue(changedError());
    renderPage("wf-a");
    await screen.findByTestId("save-status");

    fireEvent.click(screen.getByText("save-now"));

    expect(await screen.findByText("This workflow was saved elsewhere")).toBeTruthy();
    expect(screen.getByText(/saved from another tab or by someone else at /)).toBeTruthy();
  });

  it("Reload refetches the workflow and the next save uses its updatedAt", async () => {
    updateWorkflow.mockRejectedValueOnce(changedError()).mockResolvedValue({ updatedAt: "x" });
    renderPage("wf-a");
    await screen.findByTestId("save-status");
    fireEvent.click(screen.getByText("save-now"));
    await screen.findByText("This workflow was saved elsewhere");

    getWorkflow.mockResolvedValueOnce({ ...workflowFixture("wf-a"), updatedAt: THEIRS });
    fireEvent.click(screen.getByRole("button", { name: "Reload" }));

    await waitFor(() => expect(getWorkflow).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByText("This workflow was saved elsewhere")).toBeNull());
    expect(updateWorkflow).toHaveBeenCalledTimes(1); // reloading saves nothing
    expect(screen.getByTestId("save-status").textContent).toBe("idle");

    fireEvent.click(screen.getByText("save-now"));
    await waitFor(() => expect(updateWorkflow).toHaveBeenCalledTimes(2));
    expect(bodyOfCall(1).expectedUpdatedAt).toBe(THEIRS);
  });

  it("Overwrite resends the same save without the precondition", async () => {
    updateWorkflow
      .mockRejectedValueOnce(changedError())
      .mockResolvedValue({ updatedAt: AFTER_SAVE });
    renderPage("wf-a");
    await screen.findByTestId("save-status");
    fireEvent.click(screen.getByText("save-now"));
    await screen.findByText("This workflow was saved elsewhere");

    fireEvent.click(screen.getByRole("button", { name: "Overwrite" }));

    await waitFor(() => expect(updateWorkflow).toHaveBeenCalledTimes(2));
    expect(bodyOfCall(1).expectedUpdatedAt).toBeUndefined();
    await waitFor(() => expect(screen.queryByText("This workflow was saved elsewhere")).toBeNull());

    // Later saves are guarded again, from what the overwrite returned.
    fireEvent.click(screen.getByText("save-now"));
    await waitFor(() => expect(updateWorkflow).toHaveBeenCalledTimes(3));
    expect(bodyOfCall(2).expectedUpdatedAt).toBe(AFTER_SAVE);
  });

  it("sends a save made during an in-flight save only after it resolves, with its stamp", async () => {
    let finishFirst!: (v: unknown) => void;
    updateWorkflow
      .mockReturnValueOnce(new Promise((r) => (finishFirst = r)))
      .mockResolvedValue({ updatedAt: "2026-11-24T10:02:00.000000Z" });
    renderPage("wf-a");
    await screen.findByTestId("save-status");

    fireEvent.click(screen.getByText("save-now"));
    await waitFor(() => expect(updateWorkflow).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByText("save-now"));
    await new Promise((r) => setTimeout(r, 50));
    expect(updateWorkflow).toHaveBeenCalledTimes(1); // held back, not racing the first

    finishFirst({ updatedAt: AFTER_SAVE });
    await waitFor(() => expect(updateWorkflow).toHaveBeenCalledTimes(2));
    expect(bodyOfCall(0).expectedUpdatedAt).toBe(SEEN);
    expect(bodyOfCall(1).expectedUpdatedAt).toBe(AFTER_SAVE);
    expect(screen.queryByText("This workflow was saved elsewhere")).toBeNull();
  });

  it("applies Overwrite to its own save, not to one already in flight", async () => {
    updateWorkflow
      .mockRejectedValueOnce(changedError())
      .mockResolvedValue({ updatedAt: AFTER_SAVE });
    renderPage("wf-a");
    await screen.findByTestId("save-status");
    fireEvent.click(screen.getByText("save-now"));
    await screen.findByText("This workflow was saved elsewhere");

    // A save is requested, and Overwrite is clicked before it has even started.
    fireEvent.click(screen.getByText("save-now"));
    fireEvent.click(screen.getByRole("button", { name: "Overwrite" }));

    await waitFor(() => expect(updateWorkflow).toHaveBeenCalledTimes(3));
    expect(bodyOfCall(1).expectedUpdatedAt).toBe(SEEN); // the ordinary save keeps its guard
    expect(bodyOfCall(2).expectedUpdatedAt).toBeUndefined(); // the overwrite is the one without
  });

  it("still warns on unload when an edit landed during a save that then succeeded", async () => {
    let finish!: (v: unknown) => void;
    updateWorkflow.mockReturnValueOnce(new Promise((r) => (finish = r)));
    renderPage("wf-a");
    await screen.findByTestId("save-status");

    fireEvent.click(screen.getByText("save-now"));
    await waitFor(() => expect(updateWorkflow).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByText("edit-nodes")); // edited while the save is in flight
    finish({ updatedAt: AFTER_SAVE });
    await waitFor(() => expect(screen.getByTestId("save-status").textContent).toBe("dirty"));

    const event = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
  });
});
