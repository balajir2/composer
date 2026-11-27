import { fireEvent, render, screen, waitFor, cleanup } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  listExecutions,
  getExecutionObservability,
  listExecutionAttempts,
  bulkDeleteExecutions,
} from "@/lib/api/executions";
import { subscribeExecution } from "@/lib/ws";
import HistoryPage from "./page";

vi.mock("next-auth/react", () => ({ useSession: () => ({ data: { user: { role: "member" } } }) }));
vi.mock("@/lib/api/executions", () => ({
  listExecutions: vi.fn(),
  getExecutionObservability: vi.fn(),
  listExecutionAttempts: vi.fn(),
  deleteExecution: vi.fn(),
  bulkDeleteExecutions: vi.fn(),
}));
vi.mock("@/lib/ws", () => ({ subscribeExecution: vi.fn() }));
const row = {
  id: "run-1",
  workflowId: "flow-1",
  status: "completed",
  startedAt: "2026-11-26T10:00:00Z",
  threadId: "thread",
};
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(listExecutions).mockResolvedValue({ items: [row], total: 101, limit: 100, offset: 0 });
});
afterEach(cleanup);
function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <HistoryPage />
    </QueryClientProvider>
  );
  return client;
}

it("filters on the server, clears selection, and never reads observability or opens row sockets", async () => {
  mount();
  fireEvent.click(await screen.findByLabelText("Select run-1"));
  expect(screen.getByRole("button", { name: "Delete selected (1)" })).toBeVisible();
  fireEvent.change(screen.getByRole("combobox", { name: "Status" }), {
    target: { value: "failed" },
  });
  await waitFor(() =>
    expect(listExecutions).toHaveBeenLastCalledWith({ limit: 100, offset: 0, status: "failed" })
  );
  expect(screen.queryByRole("button", { name: /Delete selected/ })).toBeNull();
  expect(getExecutionObservability).not.toHaveBeenCalled();
  expect(listExecutionAttempts).not.toHaveBeenCalled();
  expect(subscribeExecution).not.toHaveBeenCalled();
});
it("clears selection on pagination and resets pagination on filtering", async () => {
  mount();
  fireEvent.click(await screen.findByLabelText("Select run-1"));
  fireEvent.click(screen.getByRole("button", { name: "Next page" }));
  await waitFor(() =>
    expect(listExecutions).toHaveBeenLastCalledWith({ limit: 100, offset: 100, status: undefined })
  );
  expect(screen.queryByRole("button", { name: /Delete selected/ })).toBeNull();
  fireEvent.change(screen.getByRole("combobox", { name: "Status" }), {
    target: { value: "running" },
  });
  await waitFor(() =>
    expect(listExecutions).toHaveBeenLastCalledWith({ limit: 100, offset: 0, status: "running" })
  );
});
it.each(["empty", "error"])("keeps filters accessible in the %s state", async (state) => {
  if (state === "empty")
    vi.mocked(listExecutions).mockResolvedValue({ items: [], total: 0, limit: 100, offset: 0 });
  else vi.mocked(listExecutions).mockRejectedValue(new Error("offline"));
  mount();
  await screen.findByText(state === "empty" ? "No runs yet" : "Could not load history");
  expect(screen.getByRole("combobox", { name: "Status" })).toBeVisible();
});
it("retains selected deletion confirmation and sends only visible selected IDs", async () => {
  vi.mocked(bulkDeleteExecutions).mockResolvedValue({ deletedCount: 1, skippedCount: 0 });
  mount();
  fireEvent.click(await screen.findByLabelText("Select run-1"));
  fireEvent.click(screen.getByRole("button", { name: "Delete selected (1)" }));
  fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
  await waitFor(() =>
    expect(bulkDeleteExecutions).toHaveBeenCalledWith({ executionIds: ["run-1"] })
  );
});

it("uses the backend canceled status when selecting Canceled", async () => {
  mount();
  await screen.findByLabelText("Select run-1");
  const option = screen.getByRole("option", { name: "Canceled" }) as HTMLOptionElement;
  fireEvent.change(screen.getByRole("combobox", { name: "Status" }), {
    target: { value: option.value },
  });
  await waitFor(() =>
    expect(listExecutions).toHaveBeenLastCalledWith({ limit: 100, offset: 0, status: "canceled" })
  );
});
