import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  getExecution,
  getExecutionObservability,
  listExecutionAttempts,
  type ExecutionObservabilityRead,
  type ExecutionAttemptPage,
} from "@/lib/api/executions";
import { ComposerApiError } from "@/lib/api/client";
import { subscribeExecution, type ComposerEvent, type ComposerEventHandler } from "@/lib/ws";

type ComposerEventType = ComposerEvent["type"];
import ExecutionPage from "./page";

vi.mock("next/navigation", () => ({
  useParams: () => ({ executionId: "run-1", workflowId: "flow-1" }),
}));
vi.mock("next-auth/react", () => ({
  useSession: () => ({ data: { accessToken: "token", user: { role: "member" } } }),
  getSession: vi.fn(),
}));
vi.mock("@/lib/api/executions", () => ({
  getExecution: vi.fn(),
  getExecutionObservability: vi.fn(),
  listExecutionAttempts: vi.fn(),
  resumeExecution: vi.fn(),
}));
vi.mock("@/lib/ws", () => ({ subscribeExecution: vi.fn() }));
const summary: ExecutionObservabilityRead = {
  executionId: "run-1",
  workflowId: "flow-1",
  status: "completed",
  currentNodeId: null,
  startedAt: "2026-11-26T10:00:00Z",
  completedAt: "2026-11-26T10:01:00Z",
  historyAvailable: true,
  attemptCount: 2,
  failureGuidance: null,
  recoveryState: "not_applicable",
  observedAt: "2026-11-26T10:01:00Z",
};
const attempts: ExecutionAttemptPage = {
  historyAvailable: true,
  total: 2,
  limit: 50,
  offset: 0,
  items: [1, 2].map((ordinal) => ({
    id: `attempt-${ordinal}`,
    ordinal,
    startedAt: "2026-11-26T10:00:00Z",
    endedAt: "2026-11-26T10:01:00Z",
    result: ordinal === 1 ? "paused" : "completed",
    triggerKind: ordinal === 1 ? "run" : "resume",
    failureGuidance: null,
    hasApproval: ordinal === 2,
  })),
};
let event: ComposerEventHandler;
let client: QueryClient;
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(subscribeExecution).mockImplementation((_id, _token, callback) => {
    event = callback;
    return vi.fn();
  });
  vi.mocked(getExecution).mockResolvedValue({
    id: "run-1",
    workflowId: "flow-1",
    status: "completed",
    startedAt: summary.startedAt,
    threadId: "thread",
    output: "Final output",
    nodeResults: { end: { status: "completed", output: "Node output" } },
  });
  vi.mocked(getExecutionObservability).mockResolvedValue(summary);
  vi.mocked(listExecutionAttempts).mockResolvedValue(attempts);
});
afterEach(() => {
  cleanup();
  client?.clear();
  vi.useRealTimers();
});
function mount() {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ExecutionPage />
    </QueryClientProvider>
  );
}
it("shows lifecycle, multiple attempts and existing output/trace without diagnostics or recovery controls", async () => {
  mount();
  expect(await screen.findByRole("heading", { name: "Execution status" })).toBeVisible();
  expect(await screen.findByText("Attempt 2")).toBeVisible();
  expect(screen.getByText("Final output")).toBeVisible();
  expect(screen.getByText(/Show node-by-node trace/)).toBeVisible();
  expect(screen.queryByRole("heading", { name: "Administrator diagnostics" })).toBeNull();
  expect(screen.queryByRole("button", { name: /recover/i })).toBeNull();
  expect(listExecutionAttempts).toHaveBeenCalledWith("run-1", { limit: 50, offset: 0 });
  expect(client.getQueryData(["execution-observability", "run-1"])).toEqual(summary);
  expect(client.getQueryData(["execution-attempts", "run-1", 50, 0])).toEqual(attempts);
});
it("renders diagnostics only when supplied by the API, regardless of client role", async () => {
  vi.mocked(getExecutionObservability).mockResolvedValue({
    ...summary,
    adminDiagnostics: { threadId: "admin-thread", deliveryAttempts: 3 },
  });
  mount();
  expect(await screen.findByRole("heading", { name: "Administrator diagnostics" })).toBeVisible();
  expect(screen.getByText("admin-thread")).toBeVisible();
});
it.each(["execution", "summary", "attempts"])(
  "uses the same not-found state for a %s 404",
  async (read) => {
    const fn =
      read === "execution"
        ? getExecution
        : read === "summary"
          ? getExecutionObservability
          : listExecutionAttempts;
    vi.mocked(fn).mockRejectedValue(new ComposerApiError(404, null));
    mount();
    expect(await screen.findByText("Execution not found")).toBeVisible();
    expect(screen.queryByText("Attempt 1")).toBeNull();
  }
);
it("keeps successful data on refresh errors, warns and offers retry", async () => {
  mount();
  await screen.findByText("Attempt 2");
  vi.mocked(getExecutionObservability).mockRejectedValue(new Error("offline"));
  vi.mocked(listExecutionAttempts).mockRejectedValue(new Error("offline"));
  vi.mocked(getExecution).mockRejectedValue(new Error("offline"));
  await act(async () => {
    await client.invalidateQueries();
  });
  expect(screen.getByText("Attempt 2")).toBeVisible();
  expect(screen.getByText("Final output")).toBeVisible();
  expect(await screen.findByText(/may be out of date/)).toBeVisible();
  expect(screen.getByRole("button", { name: "Retry execution status refresh" })).toBeVisible();
  vi.mocked(getExecutionObservability).mockResolvedValue(summary);
  fireEvent.click(screen.getByRole("button", { name: "Retry execution status refresh" }));
  await waitFor(() => expect(screen.queryByText(/may be out of date/)).toBeNull());
});
it.each(["queued", "running", "waiting_approval", "completed", "failed", "canceled"])(
  "bounds polling for %s, including terminal rows without output",
  async (status) => {
    vi.mocked(getExecution).mockResolvedValue({
      id: "run-1",
      workflowId: "flow-1",
      status,
      startedAt: summary.startedAt,
      threadId: "thread",
      output: null,
    });
    vi.mocked(getExecutionObservability).mockResolvedValue({ ...summary, status });
    vi.useFakeTimers();
    await act(async () => {
      mount();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10);
    });
    expect(screen.getByText("Attempt 2")).toBeVisible();
    const before = [
      vi.mocked(getExecution).mock.calls.length,
      vi.mocked(getExecutionObservability).mock.calls.length,
      vi.mocked(listExecutionAttempts).mock.calls.length,
    ];
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    const after = [
      vi.mocked(getExecution).mock.calls.length,
      vi.mocked(getExecutionObservability).mock.calls.length,
      vi.mocked(listExecutionAttempts).mock.calls.length,
    ];
    after.forEach((count, index) => {
      if (["completed", "failed", "canceled"].includes(status)) expect(count).toBe(before[index]);
      else expect(count).toBeGreaterThan(before[index] ?? 0);
    });
  }
);
it("uses the one progress socket to refresh all three reads on nonterminal and terminal events", async () => {
  mount();
  await screen.findByText("Attempt 2");
  for (const type of ["approval_required", "workflow_completed"] as const) {
    vi.mocked(getExecution).mockClear();
    vi.mocked(getExecutionObservability).mockClear();
    vi.mocked(listExecutionAttempts).mockClear();
    await act(async () => {
      event({
        type,
        executionId: "run-1",
        tenantId: null,
        timestamp: summary.observedAt,
        status: "completed",
        output: "WS output",
      });
    });
    await waitFor(() => {
      expect(getExecution).toHaveBeenCalledTimes(1);
      expect(getExecutionObservability).toHaveBeenCalledTimes(1);
      expect(listExecutionAttempts).toHaveBeenCalledTimes(1);
    });
  }
  expect(subscribeExecution).toHaveBeenCalledTimes(1);
  expect(screen.getByText("WS output")).toBeVisible();
});

const fire = (type: ComposerEventType) =>
  event({
    type,
    executionId: "run-1",
    tenantId: null,
    timestamp: summary.observedAt,
  });
const readCounts = () => [
  vi.mocked(getExecution).mock.calls.length,
  vi.mocked(getExecutionObservability).mock.calls.length,
  vi.mocked(listExecutionAttempts).mock.calls.length,
];

it("coalesces a replay burst of lifecycle events into one refresh", async () => {
  mount();
  await screen.findByText("Attempt 2");
  const before = readCounts();
  await act(async () => {
    for (const type of [
      "workflow_started",
      "approval_required",
      "workflow_started",
      "workflow_completed",
      "approval_required",
    ] as const) {
      fire(type);
    }
  });
  await waitFor(() => expect(readCounts()).toEqual(before.map((n) => n + 1)));
  await new Promise((resolve) => setTimeout(resolve, 700));
  expect(readCounts()).toEqual(before.map((n) => n + 1));
});

it("does not refresh the durable reads for per-node events", async () => {
  mount();
  await screen.findByText("Attempt 2");
  const before = readCounts();
  await act(async () => {
    for (const type of ["node_started", "node_completed", "node_failed"] as const) fire(type);
  });
  await new Promise((resolve) => setTimeout(resolve, 700));
  expect(readCounts()).toEqual(before);
});

it("says how many attempts are shown when the history is longer than one page", async () => {
  vi.mocked(listExecutionAttempts).mockResolvedValue({ ...attempts, total: 120, limit: 50 });
  mount();
  expect(await screen.findByText("Showing 2 of 120 attempts")).toBeVisible();
});

it("does not show a count note when every attempt is on screen", async () => {
  mount();
  await screen.findByText("Attempt 2");
  expect(screen.queryByText(/Showing \d+ of \d+ attempts/)).toBeNull();
});

it.each(["completed", "canceled"])(
  "refreshes the final lifecycle and attempts when polling sees %s without a socket event",
  async (terminalStatus) => {
    vi.mocked(getExecution).mockResolvedValue({
      id: "run-1",
      workflowId: "flow-1",
      status: "running",
      startedAt: summary.startedAt,
      threadId: "thread",
      output: null,
    });
    vi.mocked(getExecutionObservability).mockResolvedValue({ ...summary, status: "running" });
    vi.useFakeTimers();
    await act(async () => {
      mount();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10);
    });
    const summaryReads = vi.mocked(getExecutionObservability).mock.calls.length;
    const attemptReads = vi.mocked(listExecutionAttempts).mock.calls.length;
    vi.mocked(getExecution).mockResolvedValue({
      id: "run-1",
      workflowId: "flow-1",
      status: terminalStatus,
      startedAt: summary.startedAt,
      threadId: "thread",
      output: "Persisted output",
    });
    vi.mocked(getExecutionObservability).mockResolvedValue({ ...summary, status: terminalStatus });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2100);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10);
    });
    expect(getExecutionObservability).toHaveBeenCalledTimes(summaryReads + 1);
    expect(listExecutionAttempts).toHaveBeenCalledTimes(attemptReads + 1);
    expect(screen.getByText("Persisted output")).toBeVisible();
    expect(
      within(screen.getByRole("region", { name: "Execution status" })).getByText(
        terminalStatus === "canceled" ? "Canceled" : "Completed"
      )
    ).toBeVisible();
  }
);
