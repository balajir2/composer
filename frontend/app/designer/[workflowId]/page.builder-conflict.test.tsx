import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import type { Node as RFNode, Edge as RFEdge } from "reactflow";
import DesignerCanvasPage from "./page";

// Production shape: the Designer autosaves while the builder panel sits beside the canvas.
// A fake backend with network latency enforces `expectedUpdatedAt` exactly like the real API
// (PUT /workflows/{id} and POST /builder/{id}/message, including the check after the turn).

const routeParams = vi.hoisted(() => ({ current: { workflowId: "wf-a" } }));
vi.mock("next/navigation", () => ({ useParams: () => routeParams.current }));

const LATENCY_MS = 550; // one way: a PUT takes ~1.1 s, as in the production log
const SEND_OVERHEAD_MS = 400; // session lookup + CORS preflight before the builder POST lands
const TURN_MS = 8000; // the LLM turn

const server = vi.hoisted(() => ({
  stamp: 1,
  puts: 0,
  builderConflicts: 0,
  saveConflicts: 0,
}));

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const iso = (n: number) => `2026-11-25T09:58:${String(n).padStart(2, "0")}.000000Z`;
const stale = (sent: string | undefined) => sent !== undefined && sent !== iso(server.stamp);
const conflictError = () => ({
  status: 409,
  detail: { detail: { code: "workflow_changed", message: "changed", currentUpdatedAt: "x" } },
});

vi.mock("@/lib/api/workflows", () => ({
  getWorkflow: async () => {
    await sleep(LATENCY_MS);
    const snapshot = server.stamp;
    await sleep(LATENCY_MS);
    return {
      id: "wf-a",
      name: "W",
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
      updatedAt: iso(snapshot),
    };
  },
  updateWorkflow: async (_id: string, body: { expectedUpdatedAt?: string }) => {
    await sleep(LATENCY_MS);
    if (stale(body.expectedUpdatedAt)) {
      server.saveConflicts += 1;
      throw conflictError();
    }
    server.stamp += 1;
    server.puts += 1;
    const saved = { updatedAt: iso(server.stamp) };
    await sleep(LATENCY_MS);
    return saved;
  },
}));

vi.mock("@/lib/api/builder", () => ({
  listBuilderModels: async () => ({ default: "a/b", models: [{ id: "a/b", label: "B" }] }),
  sendBuilderMessage: async (_id: string, body: { expectedUpdatedAt?: string }) => {
    await sleep(SEND_OVERHEAD_MS);
    // Pre-check, before the LLM turn.
    if (stale(body.expectedUpdatedAt)) {
      server.builderConflicts += 1;
      throw conflictError();
    }
    await sleep(TURN_MS);
    // Check on save, after the turn.
    if (stale(body.expectedUpdatedAt)) {
      server.builderConflicts += 1;
      throw conflictError();
    }
    server.stamp += 1;
    return { sessionId: "s", events: [], reply: "done", workflow: null, validation_errors: [] };
  },
}));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));
vi.mock("@/components/composer/published-endpoint", () => ({ PublishedEndpoint: () => null }));
vi.mock("@/components/composer/canvas/designer-execution-panel", () => ({
  DesignerExecutionPanel: () => null,
}));
vi.mock("@/components/composer/canvas/save-controls", () => ({
  SaveControls: ({ saveStatus }: { saveStatus: string }) => (
    <div data-testid="save-status">{saveStatus}</div>
  ),
}));

// Behaves like the real WorkflowCanvas: it owns the nodes state and reports it to the page in
// an effect keyed on [nodes, onNodesChange] (workflow-canvas.tsx), so it re-reports its
// current nodes whenever the page hands it a new callback, i.e. on every page render.
vi.mock("@/components/composer/canvas/workflow-canvas", () => ({
  WorkflowCanvas: ({
    initialNodes,
    initialEdges,
    onNodesChange,
    onEdgesChange,
  }: {
    initialNodes: RFNode[];
    initialEdges: RFEdge[];
    onNodesChange?: (n: RFNode[]) => void;
    onEdgesChange?: (e: RFEdge[]) => void;
  }) => {
    const [nodes, setNodes] = useState(initialNodes);
    const [edges] = useState(initialEdges);
    useEffect(() => {
      onNodesChange?.(nodes);
    }, [nodes, onNodesChange]);
    useEffect(() => {
      onEdgesChange?.(edges);
    }, [edges, onEdgesChange]);
    return (
      <button
        onClick={() =>
          setNodes((n) => [
            ...n,
            { id: `n${n.length}`, type: "agent", position: { x: 0, y: 0 }, data: {} },
          ])
        }
      >
        edit
      </button>
    );
  },
}));

// In small steps, each in its own act(), so React commits renders and effects between timers
// the way it does in a browser; one big act() would batch them all to the end.
async function advance(ms: number) {
  for (let left = ms; left > 0; left -= 100) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(Math.min(100, left));
    });
  }
}

function sendBuilderMessageFromPanel() {
  const input = screen.getByPlaceholderText("Describe your workflow…");
  fireEvent.change(input, { target: { value: "build it" } });
  fireEvent.keyDown(input, { key: "Enter" });
}

async function openDesigner() {
  routeParams.current = { workflowId: "wf-a" };
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <DesignerCanvasPage />
    </QueryClientProvider>
  );
  await advance(2000); // the workflow loads
}

describe("Designer autosave next to the builder panel", { timeout: 60_000 }, () => {
  beforeEach(() => {
    vi.useFakeTimers();
    server.stamp = 1;
    server.puts = 0;
    server.builderConflicts = 0;
    server.saveConflicts = 0;
  });
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("saves one edit once and then stays quiet, instead of re-saving every few seconds", async () => {
    await openDesigner();
    fireEvent.click(screen.getByText("edit"));

    await advance(30_000);

    expect(server.puts).toBe(1);
    expect(screen.getByTestId("save-status").textContent).toBe("saved");
  });

  it("does not conflict with its own autosave when a builder turn runs after an edit", async () => {
    await openDesigner();
    fireEvent.click(screen.getByText("edit"));
    await advance(1500);

    sendBuilderMessageFromPanel();
    await advance(40_000);

    expect(server.builderConflicts).toBe(0);
    expect(server.saveConflicts).toBe(0);
    expect(screen.getByText("done")).toBeTruthy();
  });

  it.each([0, 600, 1200, 2900, 3100, 4000, 5200, 7000])(
    "does not conflict when a builder message follows an edit by %i ms",
    async (sendAfter) => {
      await openDesigner();
      fireEvent.click(screen.getByText("edit"));
      await advance(sendAfter);

      sendBuilderMessageFromPanel();
      await advance(40_000);

      expect(server.builderConflicts).toBe(0);
      expect(server.saveConflicts).toBe(0);
    }
  );
});
